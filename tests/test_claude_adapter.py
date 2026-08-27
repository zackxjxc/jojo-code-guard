from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "skills" / "jojo-code-guard" / "scripts"
HOOK_PATH = ROOT / "hooks" / "lifecycle.py"
sys.path.insert(0, str(SCRIPT_ROOT))

from guard_core import Diagnostic
import check_diff
import hook_baseline


def _load_lifecycle():
    """用独立模块名加载 Hook，便于注入计数器。"""
    spec = importlib.util.spec_from_file_location("jojo_lifecycle_under_test", HOOK_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载生命周期 Hook")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(module, payload: dict[str, object]) -> tuple[int, str]:
    """调用 Hook 主函数并捕获模型可见输出。"""
    output = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
        with contextlib.redirect_stdout(output):
            result = module.main()
    return result, output.getvalue()


def _tool_payload(event: str, *, tool_id: str = "tool-1", command: str = "python build.py") -> dict[str, object]:
    """生成 Codex 工具事件的最小输入。"""
    return {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "cwd": str(ROOT),
        "hook_event_name": event,
        "tool_name": "Bash",
        "tool_use_id": tool_id,
        "tool_input": {"command": command},
    }


class PluginContractTests(unittest.TestCase):
    def test_runtime_and_instruction_budgets_remain_bounded(self) -> None:
        main_skill = ROOT / "skills" / "jojo-code-guard" / "SKILL.md"
        doctor = ROOT / "skills" / "jojo-code-guard" / "scripts" / "doctor.py"
        runtime_files = [
            path
            for directory in (ROOT / "skills", ROOT / "hooks")
            for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ]
        runtime_files.append(ROOT / ".codex-plugin" / "plugin.json")
        self.assertLessEqual(main_skill.stat().st_size, 3500)
        self.assertLessEqual(doctor.stat().st_size, 20_000)
        self.assertLessEqual(sum(path.stat().st_size for path in runtime_files), 180_000)

    def test_manifest_versions_match_strict_semver(self) -> None:
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        versions = {codex["version"], claude["version"], marketplace["metadata"]["version"]}
        versions.add(marketplace["plugins"][0]["version"])
        self.assertEqual(versions, {"0.2.15"})
        self.assertRegex(codex["version"], r"^\d+\.\d+\.\d+$")

    def test_skill_frontmatter_is_unique_and_matches_directory(self) -> None:
        names: set[str] = set()
        for path in (ROOT / "skills").glob("*/SKILL.md"):
            content = path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---\n"))
            frontmatter = content.split("---", 2)[1]
            name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
            description_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
            self.assertIsNotNone(name_match)
            self.assertIsNotNone(description_match)
            name = name_match.group(1).strip().strip('"\'')
            self.assertEqual(name, path.parent.name)
            self.assertNotIn(name, names)
            names.add(name)

    def test_hook_manifest_has_no_prompt_or_session_scans(self) -> None:
        manifest = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        hooks = manifest["hooks"]
        self.assertEqual(set(hooks), {"PreToolUse", "PostToolUse", "Stop"})
        self.assertNotIn("SessionStart", hooks)
        self.assertNotIn("UserPromptSubmit", hooks)

    def test_hook_manifest_uses_one_python_entrypoint_without_git_bash(self) -> None:
        manifest = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands: list[str] = []
        for groups in manifest["hooks"].values():
            for group in groups:
                for handler in group["hooks"]:
                    commands.extend(handler[key] for key in ("command", "commandWindows") if key in handler)
        self.assertTrue(commands)
        self.assertTrue(all("lifecycle.py" in command for command in commands))
        self.assertTrue(all("bash" not in command.casefold() for command in commands))
        self.assertTrue(all("run-hook.cmd" not in command for command in commands))
        self.assertEqual(len(commands), 6)
        self.assertIn("additionalContextLimit", manifest["hooks"]["PostToolUse"][0]["hooks"][0])

    def test_removed_entrypoints_cannot_be_migrated_into_duplicate_skills(self) -> None:
        self.assertEqual(list((ROOT / "commands").glob("*.md")), [])
        self.assertFalse((ROOT / "skills" / "jojo-code-guard-help" / "SKILL.md").exists())
        entrypoints = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(
            entrypoints,
            [
                "skills/jojo-code-guard-check-diff/SKILL.md",
                "skills/jojo-code-guard-doctor/SKILL.md",
                "skills/jojo-code-guard/SKILL.md",
            ],
        )

    def test_obsolete_hook_launchers_are_absent(self) -> None:
        for name in ("session-start", "post-write-check", "run-hook.cmd"):
            self.assertFalse((ROOT / "hooks" / name).exists())


class LifecycleBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ,
            {"JOJO_CODE_GUARD_STATE_DIR": self.temporary.name, "PLUGIN_ROOT": str(ROOT)},
        )
        self.environment.start()
        self.module = _load_lifecycle()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_confirmed_read_only_command_does_not_touch_repo_or_emit_context(self) -> None:
        commands = (
            "git status --short",
            "git -c core.quotepath=false diff --stat",
            "rg --files",
            "Get-Content README.md",
        )
        for command in commands:
            with self.subTest(command=command):
                payload = _tool_payload("PreToolUse", command=command)
                with mock.patch.object(self.module, "_repo_from_input", side_effect=AssertionError("不应访问仓库")):
                    code, output = _invoke(self.module, payload)
                self.assertEqual(code, 0)
                self.assertEqual(output, "")

    def test_git_subcommands_that_can_write_are_not_whitelisted(self) -> None:
        for command in ("git branch new-branch", "git add sample.txt", "git config core.autocrlf false"):
            with self.subTest(command=command):
                self.assertFalse(self.module._confirmed_read_only(_tool_payload("PreToolUse", command=command)))

    def test_stop_without_potential_write_does_not_touch_repo(self) -> None:
        payload = {
            "session_id": "pure-chat",
            "turn_id": "turn-1",
            "cwd": str(ROOT),
            "hook_event_name": "Stop",
            "last_assistant_message": "普通聊天答案",
        }
        with mock.patch.object(self.module, "_repo_from_input", side_effect=AssertionError("不应访问仓库")):
            code, output = _invoke(self.module, payload)
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    def test_unchanged_tool_call_runs_no_full_check(self) -> None:
        snapshot = {"fingerprint": "same", "files": {}}
        with mock.patch.object(self.module, "_repo_from_input", return_value=ROOT):
            with mock.patch.object(self.module, "workspace_snapshot", return_value=snapshot):
                with mock.patch.object(self.module, "collect_diagnostics") as checker:
                    _invoke(self.module, _tool_payload("PreToolUse"))
                    code, output = _invoke(self.module, _tool_payload("PostToolUse"))
        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        checker.assert_not_called()

    def test_real_write_runs_one_post_check_and_zero_stop_checks(self) -> None:
        before = {"fingerprint": "before", "files": {}}
        after = {"fingerprint": "after", "files": {}}
        checker = mock.Mock(return_value=[])
        with mock.patch.object(self.module, "_repo_from_input", return_value=ROOT):
            with mock.patch.object(self.module, "workspace_snapshot", side_effect=[before, after]):
                with mock.patch.object(self.module, "collect_diagnostics", checker):
                    _invoke(self.module, _tool_payload("PreToolUse"))
                    code, output = _invoke(self.module, _tool_payload("PostToolUse"))
            stop_payload = {
                "session_id": "session-1",
                "turn_id": "turn-1",
                "cwd": str(ROOT),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(self.module, "workspace_snapshot", return_value=after):
                with mock.patch.object(self.module, "collect_diagnostics", checker):
                    stop_code, stop_output = _invoke(self.module, stop_payload)
        self.assertEqual((code, output), (0, ""))
        self.assertEqual((stop_code, stop_output), (0, ""))
        self.assertEqual(checker.call_count, 1)

    def test_missing_post_is_checked_once_by_stop(self) -> None:
        before = {"fingerprint": "before", "files": {}}
        after = {"fingerprint": "after", "files": {}}
        checker = mock.Mock(return_value=[])
        with mock.patch.object(self.module, "_repo_from_input", return_value=ROOT):
            with mock.patch.object(self.module, "workspace_snapshot", return_value=before):
                _invoke(self.module, _tool_payload("PreToolUse"))
            stop_payload = {
                "session_id": "session-1",
                "turn_id": "turn-1",
                "cwd": str(ROOT),
                "hook_event_name": "Stop",
            }
            with mock.patch.object(self.module, "workspace_snapshot", return_value=after):
                with mock.patch.object(self.module, "collect_diagnostics", checker):
                    code, output = _invoke(self.module, stop_payload)
        self.assertEqual((code, output), (0, ""))
        self.assertEqual(checker.call_count, 1)

    def test_stop_never_copies_last_assistant_message(self) -> None:
        sentinel = "ORIGINAL_AUDIT_ANSWER_" + ("x" * 8000)
        before = {"fingerprint": "before", "files": {}}
        after = {"fingerprint": "after", "files": {}}
        with mock.patch.object(self.module, "_repo_from_input", return_value=ROOT):
            with mock.patch.object(self.module, "workspace_snapshot", return_value=before):
                _invoke(self.module, _tool_payload("PreToolUse"))
            stop_payload = {
                "session_id": "session-1",
                "turn_id": "turn-1",
                "cwd": str(ROOT),
                "hook_event_name": "Stop",
                "last_assistant_message": sentinel,
            }
            diagnostic = Diagnostic("BLOCKED", "EOL_CHANGED", "target.txt", "换行变化")
            with mock.patch.object(self.module, "workspace_snapshot", return_value=after):
                with mock.patch.object(self.module, "collect_diagnostics", return_value=[diagnostic]):
                    code, output = _invoke(self.module, stop_payload)
        self.assertEqual(code, 0)
        self.assertNotIn(sentinel, output)
        self.assertLess(len(output.encode("utf-8")), 4000)
        self.assertEqual(json.loads(output)["decision"], "block")

    def test_concurrent_tool_ids_use_distinct_state_files(self) -> None:
        snapshot = {"fingerprint": "same", "files": {}}
        with mock.patch.object(self.module, "_repo_from_input", return_value=ROOT):
            with mock.patch.object(self.module, "workspace_snapshot", return_value=snapshot):
                _invoke(self.module, _tool_payload("PreToolUse", tool_id="tool-a"))
                _invoke(self.module, _tool_payload("PreToolUse", tool_id="tool-b"))
        directory = self.module._state_directory(_tool_payload("PreToolUse", tool_id="tool-a"))
        self.assertEqual(len(hook_baseline.state_paths(directory)), 2)

    def test_same_turn_in_different_repositories_uses_distinct_state_files(self) -> None:
        first = _tool_payload("PreToolUse", tool_id="tool-a")
        second = _tool_payload("PreToolUse", tool_id="tool-a")
        directory = self.module._state_directory(first)
        first_path = self.module._tool_state_path(first, directory, ROOT)
        second_path = self.module._tool_state_path(second, directory, ROOT.parent / "another-repository")
        self.assertNotEqual(first_path, second_path)

    def test_later_success_cannot_hide_earlier_unresolved_blocker(self) -> None:
        snapshots = [
            {"fingerprint": "before-a", "files": {}},
            {"fingerprint": "after-a", "files": {}},
            {"fingerprint": "before-b", "files": {}},
            {"fingerprint": "after-b", "files": {}},
            {"fingerprint": "after-b", "files": {}},
        ]
        blocker = [{"level": "BLOCKED", "code": "EOL_CHANGED", "path": "target.txt", "message": "换行变化"}]
        with mock.patch.object(self.module, "_repo_from_input", return_value=ROOT):
            with mock.patch.object(self.module, "workspace_snapshot", side_effect=snapshots):
                with mock.patch.object(self.module, "_run_full_check", side_effect=[blocker, [], blocker]) as checker:
                    _invoke(self.module, _tool_payload("PreToolUse", tool_id="tool-a"))
                    _invoke(self.module, _tool_payload("PostToolUse", tool_id="tool-a"))
                    _invoke(self.module, _tool_payload("PreToolUse", tool_id="tool-b"))
                    _invoke(self.module, _tool_payload("PostToolUse", tool_id="tool-b"))
                    stop_payload = {
                        "session_id": "session-1",
                        "turn_id": "turn-1",
                        "cwd": str(ROOT),
                        "hook_event_name": "Stop",
                    }
                    code, output = _invoke(self.module, stop_payload)
        self.assertEqual(code, 0)
        self.assertEqual(checker.call_count, 3)
        self.assertEqual(json.loads(output)["decision"], "block")

    def test_missing_state_identifiers_falls_back_to_strict_post_check(self) -> None:
        payload = _tool_payload("PostToolUse")
        payload.pop("session_id")
        payload.pop("turn_id")
        payload.pop("tool_use_id")
        diagnostic = Diagnostic("BLOCKED", "EOL_CHANGED", "target.txt", "换行变化")
        with mock.patch.object(self.module, "_repo_from_input", return_value=ROOT):
            with mock.patch.object(self.module, "workspace_snapshot", return_value={"fingerprint": "after", "files": {}}):
                with mock.patch.object(self.module, "collect_diagnostics", return_value=[diagnostic]) as checker:
                    code, output = _invoke(self.module, payload)
        self.assertEqual(code, 0)
        checker.assert_called_once()
        self.assertIn("EOL_CHANGED", output)

    def test_unchanged_preexisting_diagnostic_is_not_returned(self) -> None:
        diagnostics = [
            {"level": "BLOCKED", "code": "OLD", "path": "old.txt", "message": "old"},
            {"level": "BLOCKED", "code": "NEW", "path": "new.txt", "message": "new"},
        ]
        with mock.patch.object(self.module, "unchanged_preexisting_paths", return_value={"old.txt"}):
            result = self.module._new_blocking(ROOT, {"files": {}}, diagnostics)
        self.assertEqual([item["code"] for item in result], ["NEW"])


class DiffFastPathTests(unittest.TestCase):
    def test_clean_repo_uses_one_git_process_for_candidate_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "sample.txt").write_text("sample\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "add", "sample.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)

            real_run = subprocess.run
            calls: list[object] = []

            def counting_run(*args, **kwargs):
                calls.append(args[0] if args else kwargs.get("args"))
                return real_run(*args, **kwargs)

            with mock.patch("subprocess.run", side_effect=counting_run):
                result = check_diff.collect_diagnostics(repo)
        self.assertEqual(result, [])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

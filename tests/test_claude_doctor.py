from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "skills" / "jojo-code-guard" / "scripts"
DOCTOR_PATH = SCRIPT_ROOT / "doctor.py"
sys.path.insert(0, str(SCRIPT_ROOT))


def _load_doctor():
    """独立加载 doctor，避免测试间共享参数状态。"""
    spec = importlib.util.spec_from_file_location("jojo_doctor_under_test", DOCTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 doctor")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _init_repo(path: pathlib.Path) -> None:
    """创建带首个提交的隔离仓库。"""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)


class CoreDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doctor = _load_doctor()

    def test_doctor_contains_no_update_installer_or_global_rule_writer(self) -> None:
        source = DOCTOR_PATH.read_text(encoding="utf-8")
        for obsolete in ("urllib", "winget", "Ninja", "--install-tools", "--sync-global-rules"):
            self.assertNotIn(obsolete, source)
        self.assertFalse(hasattr(self.doctor, "_sync_global_rules"))

    def test_hook_contract_requires_pre_post_stop_only(self) -> None:
        findings: list[object] = []
        self.doctor._check_plugin(findings)
        by_item = {item.item: item for item in findings}
        self.assertEqual(by_item["SessionStart"].level, "OK")
        self.assertEqual(by_item["UserPromptSubmit"].level, "OK")
        for event in ("PreToolUse", "PostToolUse", "Stop"):
            self.assertEqual(by_item[event].level, "OK")

    def test_legacy_loading_is_reported_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            agents = root / "AGENTS.md"
            hook = root / "session-start"
            config = root / "hooks.json"
            agents.write_text(
                "# 用户规则\n\n## jojo-code-guard 自动加载（必须严格遵守）\n\n旧规则\n",
                encoding="utf-8",
                newline="\n",
            )
            hook.write_text("# jojo-code-guard\nJOJO_CODE_GUARD=1\n", encoding="utf-8", newline="\n")
            config.write_text(
                json.dumps(
                    {"hooks": {"SessionStart": [{"command": r"C:\Users\test\.codex\hooks\session-start"}]}},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            before = {path: path.read_bytes() for path in (agents, hook, config)}
            findings: list[object] = []
            with mock.patch.object(self.doctor, "_legacy_candidates", return_value=[agents, hook, config]):
                self.doctor._check_legacy_loading(findings)
            self.assertEqual(len(findings), 3)
            self.assertTrue(all(item.level == "ACTION_REQUIRED" for item in findings))
            self.assertEqual(before, {path: path.read_bytes() for path in (agents, hook, config)})

    def test_repair_creates_only_missing_files_and_preserves_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = pathlib.Path(directory)
            _init_repo(repo)
            editorconfig = repo / ".editorconfig"
            editorconfig.write_bytes(b"custom = true\n")
            changed = self.doctor.repair_repo(repo)
            self.assertEqual(editorconfig.read_bytes(), b"custom = true\n")
            self.assertTrue((repo / ".gitattributes").is_file())
            self.assertTrue((repo / ".gitignore").is_file())
            self.assertIn(".gitattributes", changed)
            self.assertEqual(self.doctor._git_value(repo, "core.autocrlf"), "false")

    def test_repair_does_not_follow_linked_configuration(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            repo = pathlib.Path(directory)
            _init_repo(repo)
            outside = repo / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            link = repo / ".editorconfig"
            try:
                link.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"无法创建测试符号链接：{error}")
            with self.assertRaises(RuntimeError):
                self.doctor.repair_repo(repo)
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_removed_write_modes_are_rejected(self) -> None:
        for flag in ("--install-tools", "--sync-global-rules"):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.doctor._parse_arguments([flag])

    def test_current_repository_core_diagnosis_has_no_blocked_findings(self) -> None:
        output = io.StringIO()
        with mock.patch.object(self.doctor, "_legacy_candidates", return_value=[]):
            with contextlib.redirect_stdout(output):
                code = self.doctor.main(["--repo", str(ROOT), "--json"])
        findings = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(any(item["level"] == "BLOCKED" for item in findings))


if __name__ == "__main__":
    unittest.main()

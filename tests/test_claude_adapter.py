# Claude 适配包回归测试：验证同步结果和 SessionStart 调用链。

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


# 测试直接复用仓库中的同步脚本和资源清单
ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "jojo-code-guard" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402
import sync_claude_plugin  # noqa: E402
import sync_codex_plugin  # noqa: E402


class ClaudeAdapterTests(unittest.TestCase):
    """验证 Claude 插件适配包和 SessionStart 调用链。"""

    def test_sync_removes_obsolete_launchers(self) -> None:
        """同步包应完整生成并移除旧版启动器。"""
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "adapter"
            hooks_dir = destination / "hooks"
            hooks_dir.mkdir(parents=True)
            (hooks_dir / "run-hook.cmd").write_text("old\n", encoding="utf-8")
            (hooks_dir / "run-hook.sh").write_text("old\n", encoding="utf-8")
            old_skill = destination / "skills" / "jojo-code-guard-sync-global-rules"
            old_skill.mkdir(parents=True)
            (old_skill / "SKILL.md").write_text("old\n", encoding="utf-8")
            old_commit_skill = destination / "skills" / "jojo-code-guard-commit"
            old_commit_skill.mkdir(parents=True)
            (old_commit_skill / "SKILL.md").write_text("old commit skill\n", encoding="utf-8")
            old_commands = destination / "commands"
            old_commands.mkdir(parents=True)
            (old_commands / "commit.md").write_text("old command\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"JOJO_CLAUDE_PLUGIN_DIR": str(destination)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = sync_claude_plugin.main()

            self.assertEqual(result, 0)
            self.assertFalse((hooks_dir / "run-hook.cmd").exists())
            self.assertFalse((hooks_dir / "run-hook.sh").exists())
            self.assertFalse(old_skill.exists())
            self.assertFalse(old_commit_skill.exists())
            for relative in doctor.CLAUDE_PLUGIN_REQUIRED_FILES:
                self.assertTrue((destination / relative).is_file(), relative)
            self.assertEqual(
                (destination / "hooks" / "hooks.json").read_bytes(),
                (ROOT / "hooks" / "hooks.json").read_bytes(),
            )
            if os.name != "nt":
                for name in ("session-start", "post-write-check"):
                    mode = (hooks_dir / name).stat().st_mode
                    self.assertTrue(mode & stat.S_IXUSR, name)

    def test_codex_sync_removes_obsolete_skill(self) -> None:
        """Codex 同步包应包含主 Skill，且不得保留旧 Skill。"""
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "adapter"
            old_skill = destination / "skills" / "jojo-code-guard-sync-global-rules"
            old_skill.mkdir(parents=True)
            (old_skill / "SKILL.md").write_text("old\n", encoding="utf-8")
            old_commit_skill = destination / "skills" / "jojo-code-guard-commit"
            old_commit_skill.mkdir(parents=True)
            (old_commit_skill / "SKILL.md").write_text("old commit skill\n", encoding="utf-8")
            old_commands = destination / "commands"
            old_commands.mkdir(parents=True)
            (old_commands / "commit.md").write_text("old commit command\n", encoding="utf-8")
            old_hooks = destination / "hooks"
            old_hooks.mkdir(parents=True)
            (old_hooks / "run-hook.sh").write_text("old launcher\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"JOJO_CODEX_PLUGIN_DIR": str(destination)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = sync_codex_plugin.main()

            self.assertEqual(result, 0)
            self.assertFalse(old_skill.exists())
            self.assertFalse(old_commit_skill.exists())
            self.assertTrue((destination / "skills" / "jojo-code-guard-doctor" / "SKILL.md").is_file())
            self.assertFalse((destination / "commands" / "check-diff.md").exists())
            self.assertFalse((destination / "commands" / "commit.md").exists())
            self.assertFalse((destination / "hooks" / "run-hook.sh").exists())
            self.assertTrue((destination / "hooks" / "hooks.json").is_file())
            self.assertEqual(
                (destination / "hooks" / "hooks.json").read_bytes(),
                (ROOT / "hooks" / "hooks.json").read_bytes(),
            )
            self.assertTrue((destination / "hooks" / "post-write-check").is_file())
            if os.name != "nt":
                self.assertTrue((destination / "hooks" / "post-write-check").stat().st_mode & stat.S_IXUSR)

    def test_manifest_invokes_bash_explicitly(self) -> None:
        """插件 manifest 应在命令中直接通过 Bash 执行 SessionStart。"""
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        handler = data["hooks"]["SessionStart"][0]["hooks"][0]

        self.assertNotIn("shell", handler)
        self.assertIn("${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}", handler["command"])
        self.assertIn("/hooks/session-start", handler["command"])
        self.assertIn("commandWindows", handler)
        self.assertIn("CLAUDE_PLUGIN_ROOT:\\=/%", handler["commandWindows"])
        self.assertFalse(handler["async"])

    def test_manifest_runs_post_write_check_for_edit_tools(self) -> None:
        """Claude manifest 应在文件写入工具完成后触发差异检查。"""
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        entries = data["hooks"]["PostToolUse"]

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "apply_patch|Edit|Write|MultiEdit|NotebookEdit")
        handler = entries[0]["hooks"][0]
        self.assertNotIn("shell", handler)
        self.assertIn("${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}", handler["command"])
        self.assertIn("post-write-check", handler["command"])
        self.assertIn("commandWindows", handler)
        self.assertFalse(handler["async"])

    def test_post_write_check_blocks_eol_rewrite(self) -> None:
        """Claude 写入后 Hook 应能阻断纯换行重写。"""
        with tempfile.TemporaryDirectory(prefix="jojo post hook ") as directory:
            project = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=project, check=True)
            source = project / "example.cpp"
            source.write_bytes(b"int main() { return 0; }\n")
            subprocess.run(["git", "add", "example.cpp"], cwd=project, check=True)
            subprocess.run(
                ["git", "-c", "user.name=jojo-test", "-c", "user.email=jojo@example.com", "commit", "-qm", "基线"],
                cwd=project,
                check=True,
            )
            source.write_bytes(b"int main() { return 0; }\r\n")
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
            environment["CLAUDE_PROJECT_DIR"] = str(project)

            result = subprocess.run(
                ["bash", str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            output = result.stdout.decode("utf-8", errors="replace")
            payload = json.loads(output)
            self.assertFalse(payload["continue"])
            self.assertIn("stopReason", payload)
            self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")
            self.assertIn("PURE_TEXT_REWRITE", payload["hookSpecificOutput"]["additionalContext"])

    def test_post_write_check_skips_non_git_project(self) -> None:
        """非 Git 项目不应因缺少仓库基线而报告 Hook 错误。"""
        with tempfile.TemporaryDirectory(prefix="jojo non-git ") as directory:
            project = Path(directory)
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
            environment["CLAUDE_PROJECT_DIR"] = str(project)

            result = subprocess.run(
                ["bash", str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")

    def test_post_write_check_fails_closed_on_invalid_diagnostics(self) -> None:
        """检查脚本输出损坏时必须返回结构化阻断，不能静默放行。"""
        with tempfile.TemporaryDirectory(prefix="jojo invalid hook ") as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            fake_script = root / "skills" / "jojo-code-guard" / "scripts" / "check_diff.py"
            fake_script.parent.mkdir(parents=True)
            fake_script.write_text('print("{invalid")\n', encoding="utf-8")
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(root)
            environment["CLAUDE_PROJECT_DIR"] = str(project)
            result = subprocess.run(
                ["bash", str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertFalse(payload["continue"])
        self.assertIn("无法解析", payload["hookSpecificOutput"]["additionalContext"])

    def test_manifest_versions_match(self) -> None:
        """Claude、Codex 和 marketplace 版本必须保持一致。"""
        claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )

        versions = {
            claude["version"],
            codex["version"],
            marketplace["metadata"]["version"],
            marketplace["plugins"][0]["version"],
        }
        self.assertEqual(len(versions), 1)

    def test_codex_manifest_omits_unused_hook_field(self) -> None:
        """Codex 从标准目录发现 Hook，不依赖 manifest 中未读取的 hooks 字段。"""
        data = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

        self.assertNotIn("hooks", data)
        self.assertFalse((ROOT / "hooks.json").exists())
        self.assertTrue((ROOT / "hooks" / "hooks.json").is_file())

    def test_hook_manifest_covers_session_and_write_events(self) -> None:
        """Codex/Claude 共用的标准 Hook 清单应覆盖两个生命周期事件。"""
        data = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
        groups = data["hooks"]

        self.assertIn("SessionStart", groups)
        self.assertIn("PostToolUse", groups)
        post_entry = groups["PostToolUse"][0]
        self.assertEqual(post_entry["matcher"], "apply_patch|Edit|Write|MultiEdit|NotebookEdit")
        command = post_entry["hooks"][0]["command"]
        self.assertIn("${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}", command)
        self.assertIn("/hooks/post-write-check", command)

    def test_hook_command_resolves_plugin_root_from_project_cwd(self) -> None:
        """Codex Hook 从业务仓库 cwd 启动时也必须能找到插件脚本。"""
        data = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
        session_command = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        post_command = data["hooks"]["PostToolUse"][0]["hooks"][0]["command"]

        with tempfile.TemporaryDirectory(prefix="jojo hook cwd ") as directory:
            project = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=project, check=True)
            (project / "AGENTS.md").write_text("Codex 项目规则\n", encoding="utf-8")
            source = project / "example.cpp"
            source.write_bytes(b"int main() { return 0; }\n")
            subprocess.run(["git", "add", "example.cpp"], cwd=project, check=True)
            subprocess.run(
                ["git", "-c", "user.name=jojo-test", "-c", "user.email=jojo@example.com", "commit", "-qm", "base"],
                cwd=project,
                check=True,
            )
            source.write_bytes(b"int main() { return 0; }\r\n")
            environment = os.environ.copy()
            for name in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CODEX_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"):
                environment.pop(name, None)
            environment["PLUGIN_ROOT"] = str(ROOT)
            environment["CLAUDE_PLUGIN_ROOT"] = str(project / "stale-plugin-root")

            session = subprocess.run(
                ["bash", "-c", session_command],
                cwd=project,
                env=environment,
                input=json.dumps({"cwd": str(project)}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(session.returncode, 0, session.stderr.decode("utf-8", errors="replace"))
            self.assertIn("<JOJO_CODE_GUARD>", session.stdout.decode("utf-8"))
            self.assertIn("Codex 项目规则", session.stdout.decode("utf-8"))

            post = subprocess.run(
                ["bash", "-c", post_command],
                cwd=ROOT,
                env=environment,
                input=json.dumps({"cwd": str(project)}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(post.returncode, 0, post.stderr.decode("utf-8", errors="replace"))
            payload = json.loads(post.stdout.decode("utf-8"))
            self.assertFalse(payload["continue"])
            self.assertIn("PURE_TEXT_REWRITE", payload["hookSpecificOutput"]["additionalContext"])

    def test_codex_marketplace_uses_local_source_schema(self) -> None:
        """Codex marketplace 应使用当前 CLI 可安装的本地源格式。"""
        data = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        source = data["plugins"][0]["source"]

        self.assertEqual(source, {"source": "local", "path": "./"})

    def test_release_repository_attributes_preserve_script_bytes(self) -> None:
        """发布仓库应统一文本换行，同时保留批处理脚本的原始字节。"""
        lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()

        self.assertIn("* text=auto eol=lf", lines)
        self.assertIn("*.bat   -text diff", lines)
        self.assertIn("*.cmd   -text diff", lines)
        self.assertIn("hooks/session-start text eol=lf", lines)
        self.assertIn("hooks/post-write-check text eol=lf", lines)

    def test_release_repository_editor_rules_remain_strict(self) -> None:
        """发布仓库规则不应误用业务老项目的 unset/auto 模板。"""
        editorconfig = (ROOT / ".editorconfig").read_text(encoding="utf-8")
        settings = json.loads((ROOT / ".vscode/settings.json").read_text(encoding="utf-8"))

        self.assertIn("charset                = utf-8", editorconfig)
        self.assertIn("end_of_line            = lf", editorconfig)
        self.assertIn("insert_final_newline   = true", editorconfig)
        self.assertFalse(settings["files.autoGuessEncoding"])
        self.assertEqual(settings["[bat]"]["files.eol"], "\r\n")
        self.assertEqual(settings["[powershell]"]["files.eol"], "\n")

    def test_session_start_preserves_project_rules(self) -> None:
        """插件和项目路径含空格时也应原样注入项目规则。"""
        with tempfile.TemporaryDirectory(prefix="jojo project ") as directory:
            project = Path(directory)
            plugin_root = project / "plugin root"
            rules = '项目规则：保留 "引号"、\\反斜杠和中文。\n'
            (project / "AGENTS.md").write_text(rules, encoding="utf-8")
            with mock.patch.dict(os.environ, {"JOJO_CLAUDE_PLUGIN_DIR": str(plugin_root)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    sync_claude_plugin.main()
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
            manifest = json.loads((plugin_root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
            command = manifest["hooks"]["SessionStart"][0]["hooks"][0]["command"]

            result = subprocess.run(
                ["bash", "-c", command],
                cwd=str(project),
                env=environment,
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            context = result.stdout.decode("utf-8")
            self.assertTrue(context.startswith("【强制守护规则】"))
            self.assertIn("name: jojo-code-guard", context)
            self.assertIn(rules.strip(), context)
            self.assertTrue(context.endswith("</JOJO_CODE_GUARD>\n"))

    def test_session_start_reports_missing_skill_to_model(self) -> None:
        """Skill 资源缺失时应向模型注入暂停要求，不能静默继续。"""
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = directory

            script_path = str(ROOT / "hooks" / "session-start").replace("\\", "/")

            result = subprocess.run(
                [
                    "bash",
                    "--norc",
                    "--noprofile",
                    "-c",
                    f'script="{script_path}"; exec bash --norc --noprofile "$script"',
                ],
                env=environment,
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("JOJO_CODE_GUARD_LOAD_FAILED", result.stdout.decode("utf-8"))
            self.assertIn("暂停文件修改", result.stdout.decode("utf-8"))
            self.assertIn("无法读取 Skill", result.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

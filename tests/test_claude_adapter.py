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
            with mock.patch.dict(os.environ, {"JOJO_CLAUDE_PLUGIN_DIR": str(destination)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = sync_claude_plugin.main()

            self.assertEqual(result, 0)
            self.assertFalse((hooks_dir / "run-hook.cmd").exists())
            self.assertFalse((hooks_dir / "run-hook.sh").exists())
            self.assertFalse(old_skill.exists())
            for relative in doctor.CLAUDE_PLUGIN_REQUIRED_FILES:
                self.assertTrue((destination / relative).is_file(), relative)
            if os.name != "nt":
                mode = (hooks_dir / "session-start").stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR)

    def test_codex_sync_removes_obsolete_skill(self) -> None:
        """Codex 同步包不得保留已合并进 doctor 的旧 Skill。"""
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "adapter"
            old_skill = destination / "skills" / "jojo-code-guard-sync-global-rules"
            old_skill.mkdir(parents=True)
            (old_skill / "SKILL.md").write_text("old\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"JOJO_CODEX_PLUGIN_DIR": str(destination)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = sync_codex_plugin.main()

            self.assertEqual(result, 0)
            self.assertFalse(old_skill.exists())
            self.assertTrue((destination / "skills" / "jojo-code-guard-doctor" / "SKILL.md").is_file())

    def test_manifest_uses_explicit_bash_shell(self) -> None:
        """插件 manifest 应直接通过 Bash 执行 SessionStart。"""
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        handler = data["hooks"]["SessionStart"][0]["hooks"][0]

        self.assertEqual(handler["shell"], "bash")
        self.assertEqual(handler["command"], 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/session-start"')
        self.assertFalse(handler["async"])

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
            output = result.stdout.decode("utf-8")
            self.assertTrue(output.startswith("【强制守护规则】"))
            self.assertIn("name: jojo-code-guard", output)
            self.assertIn(rules.strip(), output)
            self.assertTrue(output.endswith("</JOJO_CODE_GUARD>\n"))

    def test_session_start_fails_when_skill_is_missing(self) -> None:
        """Skill 资源缺失时应明确失败，不能伪装成成功注入。"""
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = directory

            result = subprocess.run(
                ["bash", str(ROOT / "hooks" / "session-start")],
                env=environment,
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertIn("无法读取 Skill", result.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

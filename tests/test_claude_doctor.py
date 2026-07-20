# Claude doctor 回归测试：验证插件登记、启用状态和资源完整性。

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


# 测试直接复用仓库中的 doctor 实现
ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "jojo-code-guard" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import doctor  # noqa: E402


class ClaudeDoctorTests(unittest.TestCase):
    """验证 doctor 只认可完整且精确启用的 Claude 插件。"""

    def _write_json(self, path: Path, value: object) -> None:
        """写入一个 UTF-8 JSON 测试文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")

    def _create_plugin(self, root: Path) -> None:
        """创建满足 doctor 最小资源要求的插件目录。"""
        for relative in doctor.CLAUDE_PLUGIN_REQUIRED_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "hooks/hooks.json":
                value = {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "apply_patch|Edit|Write|MultiEdit|NotebookEdit",
                                "hooks": [{"type": "command", "command": "post-write-check"}],
                            }
                        ]
                    }
                }
                path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            else:
                path.write_text("{}\n" if path.suffix == ".json" else "test\n", encoding="utf-8")

    def _check(self, home: Path) -> list[doctor.Finding]:
        """在隔离的 Claude 用户目录中运行插件诊断。"""
        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor, "_find_claude_home", return_value=home):
            doctor._check_claude_hooks(findings)
        return findings

    def test_unrelated_session_start_is_not_accepted(self) -> None:
        """其他工具的 SessionStart 不能被误认成 jojo-code-guard。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._write_json(
                home / "settings.json",
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": '"/opt/example/dcc" hook session-start',
                                    }
                                ]
                            }
                        ]
                    }
                },
            )

            findings = self._check(home)

            self.assertTrue(any(item.item == "Plugin" and item.level == "ACTION_REQUIRED" for item in findings))
            self.assertFalse(any(item.level == "OK" and item.area == "Claude" for item in findings))

    def test_complete_enabled_plugin_is_ok(self) -> None:
        """资源完整且明确启用的插件应通过诊断。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            self._create_plugin(install_path)
            self._write_json(
                home / "settings.json",
                {"enabledPlugins": {doctor.CLAUDE_PLUGIN_ID: True}},
            )
            self._write_json(
                home / "plugins" / "installed_plugins.json",
                {
                    "plugins": {
                        doctor.CLAUDE_PLUGIN_ID: [
                            {"installPath": str(install_path), "version": "test"}
                        ]
                    }
                },
            )

            findings = self._check(home)

            self.assertTrue(any(item.item == "Plugin resources" and item.level == "OK" for item in findings))
            self.assertTrue(any(item.item == "PostToolUse" and item.level == "OK" for item in findings))
            self.assertTrue(any(item.item == "Plugin enabled" and item.level == "OK" for item in findings))

    def test_disabled_plugin_requires_action(self) -> None:
        """已安装但禁用的插件必须提示用户启用。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            self._create_plugin(install_path)
            self._write_json(
                home / "settings.json",
                {"enabledPlugins": {doctor.CLAUDE_PLUGIN_ID: False}},
            )
            self._write_json(
                home / "plugins" / "installed_plugins.json",
                {"plugins": {doctor.CLAUDE_PLUGIN_ID: [{"installPath": str(install_path)}]}},
            )

            findings = self._check(home)

            self.assertTrue(any(item.item == "Plugin enabled" and item.level == "ACTION_REQUIRED" for item in findings))

    def test_incomplete_post_write_matcher_requires_action(self) -> None:
        """只覆盖 Write 的旧配置不能冒充完整的写入检查。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            self._create_plugin(install_path)
            hooks_path = install_path / "hooks" / "hooks.json"
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
            hooks["hooks"]["PostToolUse"][0]["matcher"] = "Write"
            hooks_path.write_text(json.dumps(hooks) + "\n", encoding="utf-8")
            self._write_json(
                home / "settings.json",
                {"enabledPlugins": {doctor.CLAUDE_PLUGIN_ID: True}},
            )
            self._write_json(
                home / "plugins" / "installed_plugins.json",
                {"plugins": {doctor.CLAUDE_PLUGIN_ID: [{"installPath": str(install_path)}]}},
            )

            findings = self._check(home)

            self.assertTrue(any(item.item == "PostToolUse" and item.level == "ACTION_REQUIRED" for item in findings))

    def test_missing_plugin_resource_is_blocked(self) -> None:
        """安装登记存在但资源不完整时必须阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            install_path.mkdir()
            self._write_json(
                home / "settings.json",
                {"enabledPlugins": {doctor.CLAUDE_PLUGIN_ID: True}},
            )
            self._write_json(
                home / "plugins" / "installed_plugins.json",
                {"plugins": {doctor.CLAUDE_PLUGIN_ID: [{"installPath": str(install_path)}]}},
            )

            findings = self._check(home)

            self.assertTrue(any(item.item == "Plugin resources" and item.level == "BLOCKED" for item in findings))


if __name__ == "__main__":
    unittest.main()

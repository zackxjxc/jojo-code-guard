# Claude doctor 回归测试：验证插件登记、启用状态和资源完整性。

from __future__ import annotations

import json
import subprocess
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
                        "SessionStart": [
                            {
                                "matcher": "startup|resume|clear|compact",
                                "hooks": [{"type": "command", "command": "session-start"}],
                            }
                        ],
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
            self.assertTrue(any(item.item == "SessionStart" and item.level == "OK" for item in findings))
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

    def test_missing_session_start_requires_action(self) -> None:
        """缺少会话入口时不能把插件误报为完整自动加载。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            self._create_plugin(install_path)
            hooks_path = install_path / "hooks" / "hooks.json"
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
            del hooks["hooks"]["SessionStart"]
            hooks_path.write_text(json.dumps(hooks) + "\n", encoding="utf-8")
            self._write_json(home / "settings.json", {"enabledPlugins": {doctor.CLAUDE_PLUGIN_ID: True}})
            self._write_json(
                home / "plugins" / "installed_plugins.json",
                {"plugins": {doctor.CLAUDE_PLUGIN_ID: [{"installPath": str(install_path)}]}},
            )

            findings = self._check(home)

        self.assertTrue(any(item.item == "SessionStart" and item.level == "ACTION_REQUIRED" for item in findings))

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


class RepositorySettingsTests(unittest.TestCase):
    """验证编辑器设置不会主动统一老文件的编码或换行。"""

    def _check_settings(self, value: object) -> list[doctor.Finding]:
        """在隔离目录中运行 VS Code 设置检查。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            settings = repo / ".vscode" / "settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps(value) + "\n", encoding="utf-8")
            findings: list[doctor.Finding] = []
            with mock.patch.object(doctor, "_run", return_value=(1, "")):
                doctor._check_vscode_settings(findings, repo)
            return findings

    def test_auto_settings_are_safe(self) -> None:
        """auto 换行和自动编码识别应被报告为安全提示。"""
        findings = self._check_settings(
            {
                "files.autoGuessEncoding": True,
                "[bat]": {"files.eol": "auto"},
                "[powershell]": {"files.eol": "auto"},
            }
        )

        self.assertFalse(any("老文件可能被保存为统一换行" in item.message for item in findings))
        self.assertTrue(any("files.eol=auto" in item.message for item in findings))

    def test_forced_settings_are_warned(self) -> None:
        """固定换行和关闭自动编码识别应继续告警。"""
        findings = self._check_settings(
            {
                "files.autoGuessEncoding": False,
                "files.eol": "\\n",
            }
        )

        self.assertTrue(any("存在 files.eol 设置" in item.message for item in findings))
        self.assertTrue(any("关闭 autoGuessEncoding" in item.message for item in findings))

    def test_editorconfig_save_cleanup_is_warned(self) -> None:
        """自动补末尾换行和清理尾随空白也会改写老文件。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".editorconfig").write_text(
                "[*]\ninsert_final_newline = true\ntrim_trailing_whitespace = true\n",
                encoding="utf-8",
            )
            findings: list[doctor.Finding] = []
            doctor._check_editorconfig(findings, repo)

        warning = next(item for item in findings if item.item == ".editorconfig")
        self.assertEqual(warning.level, "WARNING")
        self.assertIn("insert_final_newline = true", warning.message)
        self.assertIn("trim_trailing_whitespace = true", warning.message)

    def test_editorconfig_preserving_values_are_safe(self) -> None:
        """unset/auto 和关闭保存清理不应产生误报。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".editorconfig").write_text(
                "[*]\ncharset = unset\nend_of_line = auto\n"
                "insert_final_newline = false\ntrim_trailing_whitespace = false\n",
                encoding="utf-8",
            )
            findings: list[doctor.Finding] = []
            doctor._check_editorconfig(findings, repo)

        self.assertTrue(any(item.item == ".editorconfig" and item.level == "OK" for item in findings))

    def test_specific_attributes_override_default_preservation(self) -> None:
        """具体路径的 text/eol 属性不能被全局 * -text 掩盖。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".gitattributes").write_text("* -text\n*.cpp text eol=lf\n", encoding="utf-8")
            findings: list[doctor.Finding] = []
            doctor._check_attributes(findings, repo)

        warning = next(item for item in findings if item.item == ".gitattributes")
        self.assertEqual(warning.level, "WARNING")
        self.assertIn("覆盖 * -text", warning.message)

    def test_repair_corrects_windows_local_git_protections(self) -> None:
        """Windows 一次修复应校正换行转换和权限位设置。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            values = {
                "core.autocrlf": "true",
                "core.safecrlf": "",
                "core.filemode": "true",
            }

            def config_value(_repo: Path, _scope: str, key: str) -> str:
                return values.get(key, "")

            with mock.patch.object(doctor, "_config", side_effect=config_value), mock.patch.object(
                doctor.os, "name", "nt"
            ), mock.patch.object(doctor.subprocess, "run") as run:
                created = doctor.repair_repo(repo)

        commands = [item.args[0] for item in run.call_args_list]
        self.assertIn(["git", "config", "--local", "core.autocrlf", "false"], commands)
        self.assertIn(["git", "config", "--local", "core.safecrlf", "warn"], commands)
        self.assertIn(["git", "config", "--local", "core.filemode", "false"], commands)
        self.assertIn("git local core.filemode=false", created)

    def test_missing_pre_commit_is_optional_warning(self) -> None:
        """未安装提交门禁只能提示可选项，不能阻断日常守护。"""
        with tempfile.TemporaryDirectory() as directory:
            findings: list[doctor.Finding] = []
            with mock.patch.object(doctor, "_run", return_value=(0, ".git/hooks")):
                doctor._check_hook(findings, Path(directory))

        self.assertTrue(
            any(item.item.endswith("pre-commit") and item.level == "WARNING" for item in findings)
        )

    def test_missing_repo_templates_preserve_legacy_bytes(self) -> None:
        """业务老项目的缺失配置模板不得继承发布仓库的强制格式。"""
        editorconfig = doctor._template(".editorconfig").decode("utf-8")
        attributes = doctor._template(".gitattributes").decode("utf-8")

        self.assertIn("charset = unset", editorconfig)
        self.assertIn("end_of_line = unset", editorconfig)
        self.assertIn("insert_final_newline = unset", editorconfig)
        self.assertEqual(attributes.splitlines()[-1], "* -text")

    def test_legacy_attributes_keep_git_whitespace_checks(self) -> None:
        """字节保真属性不能让源码尾随空白逃过 Git 检查。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / ".gitattributes").write_bytes(doctor._template(".gitattributes"))
            source = repo / "example.cpp"
            source.write_bytes(b"int value = 1;\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=jojo-test",
                    "-c",
                    "user.email=jojo@example.com",
                    "commit",
                    "-qm",
                    "base",
                ],
                cwd=repo,
                check=True,
            )
            source.write_bytes(b"int value = 2;  \n")

            result = subprocess.run(
                ["git", "diff", "--check"],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"trailing whitespace", result.stdout)


if __name__ == "__main__":
    unittest.main()

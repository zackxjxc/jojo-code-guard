# 插件 doctor 回归测试：验证两端登记、启用状态、缓存版本和资源完整性。

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


class PluginDoctorTests(unittest.TestCase):
    """验证 doctor 只认可版本与资源完整且精确启用的客户端插件。"""

    def _write_json(self, path: Path, value: object) -> None:
        """写入一个 UTF-8 JSON 测试文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")

    def _create_plugin(self, root: Path) -> None:
        """创建满足 doctor 最小资源要求的插件目录。"""
        version = doctor._current_plugin_version() or "test"
        for relative in doctor.CLAUDE_PLUGIN_REQUIRED_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "hooks/hooks.json":
                source = doctor._hook_manifest_path(doctor._plugin_root(), "Claude")
                path.write_bytes(source.read_bytes())
            elif relative == ".claude-plugin/plugin.json":
                self._write_json(path, {"name": "jojo-code-guard", "version": version})
            else:
                path.write_text("{}\n" if path.suffix == ".json" else "test\n", encoding="utf-8")
        manifest = doctor._read_json_object(root / "hooks" / "hooks.json")
        for relative in doctor._hook_command_resources(manifest):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("test\n", encoding="utf-8")

    def _create_codex_plugin(self, home: Path, version: str | None = None) -> Path:
        """在隔离的 CODEX_HOME 中创建一个完整缓存版本。"""
        expected = doctor._current_plugin_version() or "test"
        installed_version = version or expected
        root = doctor._codex_cache_root(home) / installed_version
        source_manifest = doctor._read_json_object(doctor._plugin_root() / ".codex-plugin" / "plugin.json")
        self.assertIsNotNone(source_manifest)
        manifest = dict(source_manifest or {})
        manifest["version"] = installed_version
        self._write_json(root / ".codex-plugin" / "plugin.json", manifest)
        source_hooks = doctor._hook_manifest_path(doctor._plugin_root(), "Codex")
        installed_hooks = doctor._hook_manifest_path(root, "Codex")
        installed_hooks.parent.mkdir(parents=True, exist_ok=True)
        installed_hooks.write_bytes(source_hooks.read_bytes())
        for relative in doctor.CODEX_PLUGIN_REQUIRED_FILES:
            path = root / relative
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test\n", encoding="utf-8")
        hooks = doctor._read_json_object(installed_hooks)
        for relative in doctor._hook_command_resources(hooks):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("test\n", encoding="utf-8")
        return root

    def _check_codex(self, home: Path, feature_output: str = "hooks stable true") -> list[doctor.Finding]:
        """在隔离的 Codex 用户目录中运行插件诊断。"""
        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor, "_find_codex_home", return_value=home), mock.patch.object(
            doctor.shutil, "which", return_value="codex"
        ), mock.patch.object(doctor, "_run", return_value=(0, feature_output)):
            doctor._check_codex_plugin(findings)
        return findings

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
                            {
                                "installPath": str(install_path),
                                "version": doctor._current_plugin_version() or "test",
                            }
                        ]
                    }
                },
            )

            findings = self._check(home)

            self.assertTrue(any(item.item == "Plugin resources" and item.level == "OK" for item in findings))
            self.assertTrue(any(item.item == "SessionStart" and item.level == "OK" for item in findings))
            self.assertTrue(any(item.item == "PostToolUse" and item.level == "OK" for item in findings))
            self.assertTrue(any(item.item == "Stop" and item.level == "OK" for item in findings))
            self.assertTrue(any(item.item == "Plugin version" and item.level == "OK" for item in findings))
            self.assertTrue(any(item.item == "Hook manifest" and item.level == "OK" for item in findings))
            self.assertTrue(any(item.item == "Plugin enabled" and item.level == "OK" for item in findings))
            self.assertTrue(any("人工验收" in item.item and item.level == "WARNING" for item in findings))

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

    def test_unrelated_stop_handler_requires_action(self) -> None:
        """未调用守护脚本的 Stop handler 不能冒充回合结束检查。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            self._create_plugin(install_path)
            hooks_path = install_path / "hooks" / "hooks.json"
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
            hooks["hooks"]["Stop"][0]["hooks"][0]["command"] = "echo unrelated"
            hooks_path.write_text(json.dumps(hooks) + "\n", encoding="utf-8")
            self._write_json(home / "settings.json", {"enabledPlugins": {doctor.CLAUDE_PLUGIN_ID: True}})
            self._write_json(
                home / "plugins" / "installed_plugins.json",
                {"plugins": {doctor.CLAUDE_PLUGIN_ID: [{"installPath": str(install_path)}]}},
            )

            findings = self._check(home)

        self.assertTrue(any(item.item == "Stop" and item.level == "ACTION_REQUIRED" for item in findings))

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

    def test_codex_config_parser_supports_generated_plugin_table(self) -> None:
        """Python 3.9 环境不依赖 tomllib 也能读取 Codex 生成的布尔配置。"""
        content = (
            "[features]\n"
            "hooks = true\n"
            f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\n'
            "enabled = false # explicit\n"
        )

        self.assertFalse(doctor._parse_codex_plugin_enabled(content))
        self.assertTrue(doctor._parse_codex_hooks_config(content))

    def test_complete_enabled_codex_plugin_reports_manual_runtime_checks(self) -> None:
        """Codex 静态状态通过后，信任和真实执行仍必须标为人工验收。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            self._create_codex_plugin(home)
            (home / "config.toml").write_text(
                "[features]\nhooks = true\n"
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = true\n',
                encoding="utf-8",
            )

            findings = self._check_codex(home)

        self.assertTrue(any(item.item == "Plugin enabled" and item.level == "OK" for item in findings))
        self.assertTrue(any(item.item == "Hooks feature" and item.level == "OK" for item in findings))
        self.assertTrue(any(item.item == "Plugin version" and item.level == "OK" for item in findings))
        self.assertTrue(any(item.item == "Hook manifest" and item.level == "OK" for item in findings))
        manual = [item for item in findings if "人工验收" in item.item]
        self.assertEqual(len(manual), 2)
        self.assertTrue(all(item.level == "WARNING" for item in manual))

    def test_disabled_codex_plugin_requires_action(self) -> None:
        """缓存存在但 Codex 配置禁用插件时必须提示启用。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            self._create_codex_plugin(home)
            (home / "config.toml").write_text(
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = false\n',
                encoding="utf-8",
            )

            findings = self._check_codex(home)

        self.assertTrue(
            any(item.item == "Plugin enabled" and item.level == "ACTION_REQUIRED" for item in findings)
        )

    def test_disabled_codex_hooks_feature_requires_action(self) -> None:
        """CLI 报告的有效 Hooks 功能关闭时不能只依据 config.toml 报通过。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            self._create_codex_plugin(home)
            (home / "config.toml").write_text(
                "[features]\nhooks = true\n"
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = true\n',
                encoding="utf-8",
            )

            findings = self._check_codex(home, feature_output="hooks stable false")

        self.assertTrue(
            any(item.item == "Hooks feature" and item.level == "ACTION_REQUIRED" for item in findings)
        )

    def test_stale_codex_cache_version_requires_action(self) -> None:
        """Codex 缓存版本落后于当前发布包时必须提示升级或重装。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            self._create_codex_plugin(home, version="0.0.1")
            (home / "config.toml").write_text(
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = true\n',
                encoding="utf-8",
            )

            findings = self._check_codex(home)

        self.assertTrue(
            any(item.item == "Plugin version" and item.level == "ACTION_REQUIRED" for item in findings)
        )

    def test_stale_codex_hook_manifest_requires_action(self) -> None:
        """版本号相同但 Hook 清单内容陈旧时也不能误报为当前版本。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            install_path = self._create_codex_plugin(home)
            hooks_path = doctor._hook_manifest_path(install_path, "Codex")
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
            hooks["doctorTestStale"] = True
            hooks_path.write_text(json.dumps(hooks) + "\n", encoding="utf-8")
            (home / "config.toml").write_text(
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = true\n',
                encoding="utf-8",
            )

            findings = self._check_codex(home)

        self.assertTrue(
            any(item.item == "Hook manifest" and item.level == "ACTION_REQUIRED" for item in findings)
        )

    def test_multiple_codex_caches_do_not_claim_loaded_version(self) -> None:
        """存在多个缓存时 doctor 不得把任一候选版本误报为实际加载版本。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            self._create_codex_plugin(home)
            self._create_codex_plugin(home, version="0.0.1")
            (home / "config.toml").write_text(
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = true\n',
                encoding="utf-8",
            )

            findings = self._check_codex(home)

        self.assertTrue(any(item.item == "Plugin cache" and item.level == "WARNING" for item in findings))
        self.assertFalse(any(item.item == "Plugin version" and item.level == "OK" for item in findings))

    def test_codex_doctor_never_calls_plugin_list(self) -> None:
        """只读 doctor 不得调用会刷新 marketplace snapshot 的 plugin list。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            self._create_codex_plugin(home)
            (home / "config.toml").write_text(
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = true\n',
                encoding="utf-8",
            )
            commands: list[list[str]] = []

            def run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
                del cwd
                commands.append(command)
                return 0, "hooks stable true"

            findings: list[doctor.Finding] = []
            with mock.patch.object(doctor, "_find_codex_home", return_value=home), mock.patch.object(
                doctor.shutil, "which", return_value="codex"
            ), mock.patch.object(doctor, "_run", side_effect=run):
                doctor._check_codex_plugin(findings)

        self.assertTrue(commands)
        self.assertFalse(any(command[1:3] == ["plugin", "list"] for command in commands))


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

# 插件 doctor 回归测试：验证两端登记、启用状态、缓存版本和资源完整性。

from __future__ import annotations

import json
import os
import shutil
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
        for relative in doctor.CLAUDE_PLUGIN_REQUIRED_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "hooks/hooks.json":
                source = doctor._hook_manifest_path(doctor._plugin_root(), "Claude")
                path.write_bytes(source.read_bytes())
            elif relative == ".claude-plugin/plugin.json":
                shutil.copyfile(doctor._plugin_root() / relative, path)
            else:
                source = doctor._plugin_root() / relative
                shutil.copyfile(source, path)
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
        manifest_path = root / ".codex-plugin" / "plugin.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if installed_version == expected:
            shutil.copyfile(doctor._plugin_root() / ".codex-plugin" / "plugin.json", manifest_path)
        else:
            self._write_json(manifest_path, manifest)
        source_hooks = doctor._hook_manifest_path(doctor._plugin_root(), "Codex")
        installed_hooks = doctor._hook_manifest_path(root, "Codex")
        installed_hooks.parent.mkdir(parents=True, exist_ok=True)
        installed_hooks.write_bytes(source_hooks.read_bytes())
        for relative in doctor.CODEX_PLUGIN_REQUIRED_FILES:
            path = root / relative
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(doctor._plugin_root() / relative, path)
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

    def _register_claude_plugin(self, home: Path, install_path: Path) -> None:
        """把隔离插件登记为 Claude 当前启用版本。"""
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

    def test_direct_skill_without_plugin_manifests_is_not_blocked(self) -> None:
        """直接 Skill 安装没有客户端 manifest 时只能标记版本未知，不能误报损坏。"""
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            doctor, "_plugin_root", return_value=Path(directory)
        ):
            findings: list[doctor.Finding] = []
            version = doctor._check_source_plugin_version(findings)

        self.assertIsNone(version)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].level, "WARNING")
        self.assertIn("直接 Skill 安装", findings[0].message)

    def test_embedded_resource_hashes_match_release_sources(self) -> None:
        """发布资源更新时必须同步 doctor 的内置完整性清单。"""
        root = doctor._plugin_root()
        expected = {
            **doctor.PLUGIN_RESOURCE_SHA256,
            **doctor.CLAUDE_PLUGIN_RESOURCE_SHA256,
            **doctor.CODEX_PLUGIN_RESOURCE_SHA256,
        }
        actual = {
            relative: doctor._resource_sha256(root / relative)
            for relative in expected
        }

        self.assertEqual(actual, expected)

    def test_tampered_claude_manifest_is_blocked(self) -> None:
        """Claude manifest 即使版本未变，改写其行为字段也必须触发阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            self._create_plugin(install_path)
            manifest_path = install_path / ".claude-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["description"] = "tampered"
            self._write_json(manifest_path, manifest)
            self._register_claude_plugin(home, install_path)

            findings = self._check(home)

        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertIn(".claude-plugin/plugin.json", integrity.message)

    def test_tampered_codex_manifest_is_blocked(self) -> None:
        """Codex manifest 的技能路径被改写时必须触发完整性阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            install_path = self._create_codex_plugin(home)
            manifest_path = install_path / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["skills"] = "../../attacker"
            self._write_json(manifest_path, manifest)

            findings = self._check_codex(home)

        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertIn(".codex-plugin/plugin.json", integrity.message)

    def test_tampered_agent_policy_is_blocked(self) -> None:
        """公开 Skill 的 Codex 调用策略被改写时必须触发完整性阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            install_path = self._create_codex_plugin(home)
            relative = "skills/jojo-code-guard-doctor/agents/openai.yaml"
            (install_path / relative).parent.mkdir(parents=True, exist_ok=True)
            (install_path / relative).write_text("policy: malicious\n", encoding="utf-8")

            findings = self._check_codex(home)

        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertIn(relative, integrity.message)

    def test_missing_runtime_entrypoint_is_blocked(self) -> None:
        """doctor 不能把缺少脚本、公开 Skill 或 Claude command 的安装判为健康。"""
        relative_paths = (
            "skills/jojo-code-guard/scripts/doctor.py",
            "skills/jojo-code-guard-doctor/SKILL.md",
            "skills/jojo-code-guard-check-diff/SKILL.md",
            "skills/jojo-code-guard-help/SKILL.md",
            "commands/doctor.md",
            "commands/check-diff.md",
            "commands/help.md",
        )
        for relative in relative_paths:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / ".claude"
                install_path = Path(directory) / "plugin"
                self._create_plugin(install_path)
                target = install_path / relative
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(doctor._plugin_root() / relative, target)
                target.unlink()
                self._register_claude_plugin(home, install_path)

                findings = self._check(home)

                resources = next(item for item in findings if item.item == "Plugin resources")
                self.assertEqual(resources.level, "BLOCKED")
                self.assertIn(relative, resources.message)

    def test_tampered_claude_command_resource_is_blocked(self) -> None:
        """公开 Claude command 被改写时必须触发完整性阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            self._create_plugin(install_path)
            command_path = install_path / "commands" / "doctor.md"
            if not command_path.exists():
                command_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(doctor._plugin_root() / "commands" / "doctor.md", command_path)
            command_path.write_text("malicious override\n", encoding="utf-8")
            self._register_claude_plugin(home, install_path)

            findings = self._check(home)

        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertIn("commands/doctor.md", integrity.message)

    def test_added_claude_public_entrypoint_is_blocked(self) -> None:
        """Claude 安装不能在夹带自动发现的未知入口时报告完整性健康。"""
        relative_paths = (
            "commands/unexpected.md",
            "commands/nested/unexpected.md",
            "skills/unexpected-entry/SKILL.md",
            "skills/category/unexpected-entry/SKILL.md",
            "skills/unexpected-entry/skill.md",
            "SKILL.md",
            "agents/unexpected.md",
            ".mcp.json",
            ".lsp.json",
            "monitors/monitors.json",
            "bin/unexpected-tool",
            "settings.json",
            "workflows/unexpected.js",
            "output-styles/unexpected.md",
            "themes/unexpected.json",
        )
        for relative in relative_paths:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / ".claude"
                install_path = Path(directory) / "plugin"
                self._create_plugin(install_path)
                unexpected = install_path / relative
                unexpected.parent.mkdir(parents=True, exist_ok=True)
                unexpected.write_text("unexpected entry\n", encoding="utf-8")
                self._register_claude_plugin(home, install_path)

                findings = self._check(home)

                integrity = next(
                    item for item in findings if item.item == "Plugin resource integrity"
                )
                self.assertEqual(integrity.level, "BLOCKED")
                self.assertIn(relative, integrity.message)

    def test_symlinked_claude_discovery_root_is_blocked(self) -> None:
        """Claude 自动发现根和未知 Skill 符号链接不能绕过 doctor 枚举。"""
        relative_paths = (
            Path("commands"),
            Path("commands/nested-link"),
            Path("skills"),
            Path("agents"),
            Path("agents/nested-link"),
            Path("skills/unexpected-link"),
        )
        original_is_symlink = Path.is_symlink
        for relative in relative_paths:
            with self.subTest(relative=str(relative)), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / ".claude"
                install_path = Path(directory) / "plugin"
                self._create_plugin(install_path)
                target = install_path / relative
                if not target.exists():
                    target.mkdir(parents=True)
                self._register_claude_plugin(home, install_path)

                def is_symlink(path: Path, target: Path = target) -> bool:
                    return path == target or original_is_symlink(path)

                with mock.patch.object(Path, "is_symlink", autospec=True, side_effect=is_symlink):
                    findings = self._check(home)

                integrity = next(
                    item for item in findings if item.item == "Plugin resource integrity"
                )
                self.assertEqual(integrity.level, "BLOCKED")
                self.assertIn(relative.as_posix(), integrity.message)

    def test_tampered_shared_subskill_is_blocked_for_codex(self) -> None:
        """Codex 安装中的共享公开 Skill 被改写时必须触发完整性阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            install_path = self._create_codex_plugin(home)
            relative = "skills/jojo-code-guard-help/SKILL.md"
            (install_path / relative).write_text("malicious override\n", encoding="utf-8")

            findings = self._check_codex(home)

        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertIn(relative, integrity.message)

    def test_added_codex_public_skill_is_blocked(self) -> None:
        """Codex 安装不能在夹带未知公共 Skill 时报告完整性健康。"""
        relative_paths = (
            "skills/unexpected-entry/SKILL.md",
            "skills/category/unexpected-entry/SKILL.md",
            "skills/unexpected-entry/skill.md",
        )
        for relative in relative_paths:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / ".codex"
                install_path = self._create_codex_plugin(home)
                unexpected = install_path / relative
                unexpected.parent.mkdir(parents=True)
                unexpected.write_text("unexpected entry\n", encoding="utf-8")

                findings = self._check_codex(home)

                integrity = next(
                    item for item in findings if item.item == "Plugin resource integrity"
                )
                self.assertEqual(integrity.level, "BLOCKED")
                self.assertIn(relative, integrity.message)

    def test_added_codex_default_component_file_is_blocked(self) -> None:
        """Codex 默认发现的 MCP/app 配置不能绕过 doctor 的公开入口 allowlist。"""
        for relative in (".mcp.json", ".app.json"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / ".codex"
                install_path = self._create_codex_plugin(home)
                self._write_json(install_path / relative, {})

                findings = self._check_codex(home)

                integrity = next(
                    item for item in findings if item.item == "Plugin resource integrity"
                )
                self.assertEqual(integrity.level, "BLOCKED")
                self.assertIn(relative, integrity.message)

    def test_symlinked_codex_unknown_skill_is_blocked(self) -> None:
        """Codex 未知顶层 Skill 符号链接不能绕过 doctor 枚举。"""
        relative_paths = (Path("skills"), Path("skills/unexpected-link"))
        original_is_symlink = Path.is_symlink
        for relative in relative_paths:
            with self.subTest(relative=str(relative)), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / ".codex"
                install_path = self._create_codex_plugin(home)
                target = install_path / relative
                if relative == Path("skills/unexpected-link"):
                    target.mkdir()

                def is_symlink(path: Path, target: Path = target) -> bool:
                    return path == target or original_is_symlink(path)

                with mock.patch.object(Path, "is_symlink", autospec=True, side_effect=is_symlink):
                    findings = self._check_codex(home)

                integrity = next(
                    item for item in findings if item.item == "Plugin resource integrity"
                )
                self.assertEqual(integrity.level, "BLOCKED")
                self.assertIn(relative.as_posix(), integrity.message)

    def test_missing_shared_runtime_entrypoint_is_blocked_for_codex(self) -> None:
        """Codex doctor 不能把缺少脚本或公开 Skill 的安装判为健康。"""
        relative_paths = (
            "skills/jojo-code-guard/scripts/doctor.py",
            "skills/jojo-code-guard-doctor/SKILL.md",
            "skills/jojo-code-guard-check-diff/SKILL.md",
            "skills/jojo-code-guard-help/SKILL.md",
        )
        for relative in relative_paths:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / ".codex"
                install_path = self._create_codex_plugin(home)
                (install_path / relative).unlink()

                findings = self._check_codex(home)

                resources = next(item for item in findings if item.item == "Plugin resources")
                self.assertEqual(resources.level, "BLOCKED")
                self.assertIn(relative, resources.message)

    def test_doctor_entrypoint_symlink_is_blocked(self) -> None:
        """未静态自哈希的 doctor 入口也不能借符号链接越出安装目录。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / ".claude"
            install_path = root / "plugin"
            self._create_plugin(install_path)
            target = install_path / "skills" / "jojo-code-guard" / "scripts" / "doctor.py"
            outside = root / "outside-doctor.py"
            outside.write_text("print('outside')\n", encoding="utf-8")
            target.unlink()
            try:
                target.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"当前平台不能创建文件符号链接：{error}")
            self._register_claude_plugin(home, install_path)

            findings = self._check(home)

        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertIn("skills/jojo-code-guard/scripts/doctor.py", integrity.message)

    def test_required_plugin_resource_hard_link_is_blocked(self) -> None:
        """受管资源即使摘要正确，也不能通过外部硬链接在检查后被联动改写。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / ".claude"
            install_path = root / "plugin"
            self._create_plugin(install_path)
            target = install_path / "hooks" / "session-start"
            outside = root / "outside-session-start"
            outside.write_bytes(target.read_bytes())
            target.unlink()
            try:
                os.link(outside, target)
            except OSError as error:
                self.skipTest(f"当前文件系统不能创建硬链接：{error}")
            self._register_claude_plugin(home, install_path)

            findings = self._check(home)

        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertIn("hooks/session-start", integrity.message)

    def test_link_like_plugin_root_is_blocked_before_resource_validation(self) -> None:
        """插件安装根自身是 symlink/junction/reparse point 时不得作为信任根。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            self._create_plugin(root)
            findings: list[doctor.Finding] = []
            real_link_check = doctor._plugin_path_is_link_like

            def link_like(path: Path) -> bool:
                return path == root or real_link_check(path)

            with mock.patch.object(doctor, "_plugin_path_is_link_like", side_effect=link_like):
                result = doctor._check_plugin_resource_integrity(
                    findings,
                    "Claude",
                    root,
                )

        self.assertFalse(result)
        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertRegex(integrity.message, "链接|reparse")

    def test_claude_link_like_install_root_is_blocked_before_manifest_read(self) -> None:
        """Claude 登记根为链接型路径时，caller 不能先读取其 manifest 或 hooks。"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / ".claude"
            install_path = base / "plugin"
            self._create_plugin(install_path)
            self._register_claude_plugin(home, install_path)
            real_link_check = doctor._plugin_path_is_link_like

            def link_like(path: Path) -> bool:
                return path == install_path or real_link_check(path)

            with mock.patch.object(
                doctor,
                "_plugin_path_is_link_like",
                side_effect=link_like,
            ), mock.patch.object(doctor, "_check_installed_plugin_version") as version_check, mock.patch.object(
                doctor,
                "_check_hook_manifest_freshness",
            ) as hook_check:
                findings = self._check(home)

        version_check.assert_not_called()
        hook_check.assert_not_called()
        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")

    def test_codex_link_like_cache_version_is_blocked_before_manifest_read(self) -> None:
        """Codex 缓存版本根为链接型路径时，caller 不能先读取 manifest。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            install_path = self._create_codex_plugin(home)
            real_link_check = doctor._plugin_path_is_link_like

            def link_like(path: Path) -> bool:
                return path == install_path or real_link_check(path)

            with mock.patch.object(
                doctor,
                "_plugin_path_is_link_like",
                side_effect=link_like,
            ), mock.patch.object(doctor, "_manifest_version") as manifest_version:
                findings = self._check_codex(home)

        manifest_version.assert_not_called()
        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")

    def test_tampered_hook_resource_is_blocked(self) -> None:
        """版本和 Hook manifest 相同也不能掩盖被篡改的可执行入口。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".claude"
            install_path = Path(directory) / "plugin"
            self._create_plugin(install_path)
            (install_path / "hooks" / "session-start").write_text(
                "echo MALICIOUS\n", encoding="utf-8"
            )
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

        integrity = next(item for item in findings if item.item == "Plugin resource integrity")
        self.assertEqual(integrity.level, "BLOCKED")
        self.assertIn("hooks/session-start", integrity.message)

    def test_new_remote_version_requires_update(self) -> None:
        """远端版本较新时应提示用户更新，不能声称 Skill 会自行升级。"""
        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor, "_fetch_remote_plugin_version", return_value=("0.2.10", None)):
            doctor._check_plugin_update(findings, "0.2.9")

        result = next(item for item in findings if item.item == "发现新版本")
        self.assertEqual(result.level, "ACTION_REQUIRED")
        self.assertIn("不会自行更新", result.message)
        self.assertIn("codex plugin marketplace upgrade", result.message)
        self.assertIn("codex plugin add", result.message)
        self.assertIn("/plugin marketplace update", result.message)
        self.assertIn("/plugin install", result.message)

    def test_current_remote_version_needs_no_update(self) -> None:
        """当前版本不落后时更新检查应通过。"""
        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor, "_fetch_remote_plugin_version", return_value=("0.2.6", None)):
            doctor._check_plugin_update(findings, "0.2.6")

        self.assertEqual(findings, [doctor.Finding("OK", "插件更新", "远端版本", "当前 0.2.6，远端 0.2.6，无需更新")])

    def test_update_check_network_failure_is_warning(self) -> None:
        """网络不可用不能阻断 doctor 的其他只读诊断。"""
        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor, "_fetch_remote_plugin_version", return_value=(None, "network unavailable")):
            doctor._check_plugin_update(findings, "0.2.6")

        self.assertEqual(findings[0].level, "WARNING")
        self.assertIn("network unavailable", findings[0].message)

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

    def test_every_codex_cache_directory_must_match_its_manifest_version(self) -> None:
        """多缓存时也必须逐个核对版本目录名，不能只检查资源摘要。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            first = self._create_codex_plugin(home)
            second = self._create_codex_plugin(home, version="0.0.1")
            first.rename(first.with_name("bogus-a"))
            second.rename(second.with_name("bogus-b"))
            (home / "config.toml").write_text(
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = true\n',
                encoding="utf-8",
            )

            findings = self._check_codex(home)

        mismatches = [
            item
            for item in findings
            if item.item == "Plugin version" and item.level == "BLOCKED"
        ]
        self.assertEqual(len(mismatches), 2)
        self.assertTrue(all("缓存目录" in item.message for item in mismatches))

    def test_every_codex_cache_is_checked_for_unknown_entrypoints(self) -> None:
        """无法确定加载版本时，也不能跳过任一候选缓存的 MCP/app/Skill 完整性检查。"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            self._create_codex_plugin(home)
            stale = self._create_codex_plugin(home, version="0.0.1")
            (stale / ".mcp.json").write_text("{}\n", encoding="utf-8")
            (home / "config.toml").write_text(
                f'[plugins."{doctor.CODEX_PLUGIN_ID}"]\nenabled = true\n',
                encoding="utf-8",
            )

            findings = self._check_codex(home)

        self.assertTrue(
            any(
                item.item == "Plugin resource integrity"
                and item.level == "BLOCKED"
                and ".mcp.json" in item.message
                for item in findings
            )
        )

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

    def _init_repo(self, repo: Path) -> None:
        """初始化隔离 Git 仓库并关闭平台换行转换。"""
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True)
        subprocess.run(["git", "config", "core.safecrlf", "false"], cwd=repo, check=True)

    def _attribute_findings(self, attributes: str, scripts: dict[str, bytes]) -> list[doctor.Finding]:
        """创建属性与批处理样本并运行仓库诊断。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self._init_repo(repo)
            (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
            for relative, data in scripts.items():
                (repo / relative).write_bytes(data)
            if scripts:
                subprocess.run(["git", "add", "--", *scripts], cwd=repo, check=True)
            findings: list[doctor.Finding] = []
            doctor._check_attributes(findings, repo)
            return findings

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

    def test_any_working_tree_encoding_rule_is_warned(self) -> None:
        """非 UTF-8 等 working-tree-encoding 规则同样会转换工作区字节。"""
        findings = self._attribute_findings(
            "* -text\n*.txt working-tree-encoding=UTF-16LE\n",
            {},
        )

        warning = next(item for item in findings if item.item == ".gitattributes")
        self.assertEqual(warning.level, "WARNING")
        self.assertIn("working-tree-encoding=UTF-16LE", warning.message)

    def test_tracked_batch_without_crlf_attributes_requires_action(self) -> None:
        """只有 * -text 时，仓库中的批处理必须得到明确配置建议。"""
        findings = self._attribute_findings("* -text\n", {"build.bat": b"@echo off\r\n"})

        result = next(item for item in findings if item.item == "批处理 CRLF 属性")
        self.assertEqual(result.level, "ACTION_REQUIRED")
        self.assertIn("text=unset", result.message)
        self.assertIn("eol=unspecified", result.message)

    def test_standard_batch_attributes_and_contents_pass(self) -> None:
        """标准批处理属性与 CRLF 工作区字节应同时通过。"""
        findings = self._attribute_findings(
            "* -text\n*.bat   text eol=crlf\n*.cmd   text eol=crlf\n",
            {"build.bat": b"@echo off\r\n", "run.cmd": b"@echo off\r\n"},
        )

        self.assertTrue(any(item.item == "批处理 CRLF 属性" and item.level == "OK" for item in findings))
        self.assertEqual(len([item for item in findings if item.area == "批处理" and item.level == "OK"]), 2)
        attributes = next(item for item in findings if item.item == ".gitattributes")
        self.assertEqual(attributes.level, "OK")
        self.assertNotIn("可能规范化", attributes.message)

    def test_later_rule_overriding_batch_eol_is_detected(self) -> None:
        """后续更具体规则覆盖 CRLF 时必须报告最终属性。"""
        findings = self._attribute_findings(
            "* -text\n*.bat text eol=crlf\n*.cmd text eol=crlf\n*.bat eol=lf\n",
            {"build.bat": b"@echo off\r\n"},
        )

        result = next(item for item in findings if item.item == "批处理 CRLF 属性")
        self.assertEqual(result.level, "ACTION_REQUIRED")
        self.assertIn("build.bat: text=set, eol=lf", result.message)

    def test_batch_content_problems_are_reported_separately(self) -> None:
        """LF、混合换行、BOM 与非 UTF-8 必须得到具体诊断。"""
        findings = self._attribute_findings(
            "* -text\n",
            {
                "lf.bat": b"@echo off\n",
                "lf.cmd": b"@echo off\n",
                "mixed.bat": b"@echo off\r\necho bad\n",
                "bom.cmd": b"\xef\xbb\xbf@echo off\r\n",
                "legacy.cmd": "echo 中文\r\n".encode("gbk"),
            },
        )
        messages = {item.item: item.message for item in findings if item.area == "批处理"}

        self.assertIn("当前为 LF", messages["lf.bat"])
        self.assertIn("当前为 LF", messages["lf.cmd"])
        self.assertIn("混合换行", messages["mixed.bat"])
        self.assertIn("无 BOM", messages["bom.cmd"])
        self.assertIn("不是 UTF-8", messages["legacy.cmd"])

    def test_repo_without_batch_has_no_content_findings(self) -> None:
        """没有批处理文件的老项目不应产生脚本内容告警。"""
        findings = self._attribute_findings("* -text\n", {})

        self.assertFalse(any(item.area == "批处理" for item in findings))

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
        self.assertIn("* -text", attributes.splitlines())
        self.assertIn("*.bat text eol=crlf", attributes.splitlines())
        self.assertIn("*.cmd text eol=crlf", attributes.splitlines())

    def test_repair_appends_batch_rules_without_touching_index_or_scripts(self) -> None:
        """修复已有属性必须保留内容、脚本和暂存区。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self._init_repo(repo)
            attributes = repo / ".gitattributes"
            script = repo / "build.bat"
            attributes.write_text("# team rule\n* -text\n", encoding="utf-8")
            script.write_bytes(b"@echo off\n")
            (repo / "staged.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.name=jojo-test", "-c", "user.email=jojo@example.com", "commit", "-qm", "base"],
                cwd=repo,
                check=True,
            )
            (repo / "staged.txt").write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "add", "staged.txt"], cwd=repo, check=True)
            before_index = subprocess.run(
                ["git", "diff", "--cached", "--binary"], cwd=repo, stdout=subprocess.PIPE, check=True
            ).stdout
            before_script = script.read_bytes()

            changed = doctor.repair_repo(repo)

            after_index = subprocess.run(
                ["git", "diff", "--cached", "--binary"], cwd=repo, stdout=subprocess.PIPE, check=True
            ).stdout
            content = attributes.read_text(encoding="utf-8")
            self.assertIn("# team rule", content)
            self.assertIn("*.bat text eol=crlf", content)
            self.assertIn("*.cmd text eol=crlf", content)
            self.assertEqual(script.read_bytes(), before_script)
            self.assertEqual(after_index, before_index)
            self.assertTrue(any("未执行 renormalize" in item for item in changed))

    def test_repair_rejects_hardlinked_attributes_before_any_write(self) -> None:
        """仓库规则文件与外部文件共享 inode 时，repair 必须在写入前失败。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            self._init_repo(repo)
            outside = root / "outside-attributes"
            original = b"# outside\n* -text\n"
            outside.write_bytes(original)
            try:
                os.link(outside, repo / ".gitattributes")
            except OSError as error:
                self.skipTest(f"当前文件系统不能创建硬链接：{error}")

            with self.assertRaisesRegex(RuntimeError, "链接|link"):
                doctor.repair_repo(repo)

            self.assertEqual(outside.read_bytes(), original)
            self.assertFalse((repo / ".editorconfig").exists())
            self.assertFalse((repo / ".gitignore").exists())

    def test_repair_rejects_broken_template_symlink_before_any_write(self) -> None:
        """broken symlink 不能被当作缺失模板并在仓库外创建目标。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            self._init_repo(repo)
            outside = root / "outside-editorconfig"
            try:
                os.symlink(outside, repo / ".editorconfig")
            except OSError as error:
                self.skipTest(f"当前平台不能创建文件符号链接：{error}")

            with self.assertRaisesRegex(RuntimeError, "链接|link|reparse"):
                doctor.repair_repo(repo)

            self.assertFalse(outside.exists())
            self.assertFalse((repo / ".gitattributes").exists())
            self.assertFalse((repo / ".gitignore").exists())

    def test_repair_preview_shows_migration_risk(self) -> None:
        """修复前应展示拟议属性差异和迁移风险所需规则。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self._init_repo(repo)
            (repo / ".gitattributes").write_text("# keep\n* -text\n", encoding="utf-8")

            preview = doctor._attributes_repair_preview(repo)

        self.assertIsNotNone(preview)
        self.assertIn("# keep", preview)
        self.assertIn("+*.bat text eol=crlf", preview)
        self.assertIn("+*.cmd text eol=crlf", preview)

    def test_repair_preserves_existing_crlf_attributes(self) -> None:
        """向 CRLF .gitattributes 追加规则时不得制造 LF/CRLF 混合换行。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self._init_repo(repo)
            attributes = repo / ".gitattributes"
            attributes.write_bytes(b"# team rule\r\n* -text\r\n")

            doctor.repair_repo(repo)

            data = attributes.read_bytes()
        self.assertIn(b"*.bat text eol=crlf\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_git_status_failure_is_blocked_not_clean(self) -> None:
        """索引损坏或 Git 失败时不能把空 stdout 当作干净工作区。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self._init_repo(repo)
            (repo / ".git" / "index").write_bytes(b"broken")
            findings: list[doctor.Finding] = []

            doctor._check_worktree_status(findings, repo)

        result = next(item for item in findings if item.item == "未提交修改")
        self.assertEqual(result.level, "BLOCKED")
        self.assertIn("git status 执行失败", result.message)

    def test_install_tools_does_not_create_a_doctor_owned_elevated_payload(self) -> None:
        """doctor 不得把 PATH 或临时目录中的可变载荷带入管理员令牌。"""
        winget = str((ROOT / "test-fixtures" / "winget.exe").resolve())
        available = {
            "winget": winget,
            "pwsh": None,
            "gsudo": "C:/Tools/gsudo.exe",
            "rg": "C:/Tools/rg.exe",
        }
        commands: list[list[str]] = []

        def run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
            del cwd
            commands.append(command)
            return 0, "ok"

        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor.platform, "system", return_value="Windows"), mock.patch.object(
            doctor.shutil, "which", side_effect=lambda name: available.get(name)
        ), mock.patch.object(doctor, "_is_windows_admin", return_value=False, create=True), mock.patch.object(
            doctor, "_run", side_effect=run
        ), mock.patch.object(
            doctor,
            "_run_elevated_install",
            return_value=(False, "unexpected self-elevation"),
            create=True,
        ) as elevated:
            doctor._install_tools(findings)

        elevated.assert_not_called()
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], winget)
        self.assertTrue(Path(commands[0][0]).is_absolute())
        self.assertFalse(any(item.item == "UAC" for item in findings))

    def test_install_tools_only_installs_missing_tools(self) -> None:
        """--install-tools 不得把补齐缺失工具扩大为升级所有已安装工具。"""
        winget = str((ROOT / "test-fixtures" / "winget.exe").resolve())
        available = {
            "winget": winget,
            "pwsh": "pwsh.exe",
            "gsudo": None,
            "rg": "rg.exe",
        }
        commands: list[list[str]] = []

        def run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
            del cwd
            commands.append(command)
            return 0, "ok"

        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor.platform, "system", return_value="Windows"), mock.patch.object(
            doctor.shutil, "which", side_effect=lambda name: available.get(name)
        ), mock.patch.object(doctor, "_run", side_effect=run):
            doctor._install_tools(findings)

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0:2], [winget, "install"])
        self.assertIn("gerardog.gsudo", commands[0])
        self.assertFalse(any("upgrade" in command for command in commands))

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

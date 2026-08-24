# Claude 适配包回归测试：验证同步结果和 SessionStart 调用链。

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
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


def _find_bash() -> str:
    """在 Windows 上不依赖 PATH 定位测试所需的 Git Bash。"""
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")]
    if os.name == "nt":
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            program_files = os.environ.get(variable)
            if program_files:
                candidates.append(str(Path(program_files) / "Git" / "bin" / "bash.exe"))
    candidates.append(shutil.which("bash") or "")
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), "bash")


BASH = _find_bash()

import doctor  # noqa: E402
import sync_claude_plugin  # noqa: E402
import sync_codex_plugin  # noqa: E402


class ClaudeAdapterTests(unittest.TestCase):
    """验证 Claude 插件适配包和 SessionStart 调用链。"""

    def test_main_skill_is_a_small_router_with_progressive_disclosure(self) -> None:
        """每会话加载的入口只保留路由，详细规则按任务进入上下文。"""
        skill_root = ROOT / "skills" / "jojo-code-guard"
        skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")

        routed_references = (
            "references/通用行为规则.md",
            "references/通用文件守护.md",
            "references/C++专项规则.md",
            "references/Git操作规则.md",
        )
        self.assertLess(len(skill_text.encode("utf-8")), 6000)
        for reference in routed_references:
            self.assertIn(reference, skill_text)
            self.assertTrue((skill_root / reference).is_file(), reference)
        self.assertIn("[PowerShell规则.md](PowerShell规则.md)", skill_text)
        self.assertIn("references/usage.md", skill_text)
        for deferred_detail in (
            "JOJO_CODE_GUARD_ALLOW_MIGRATIONS",
            "工程决策原则",
            "Git 提交规范",
        ):
            self.assertNotIn(deferred_detail, skill_text)
        self.assertFalse((skill_root / "通用规则.md").exists())
        self.assertTrue((skill_root / "references" / "自动加载规则.md").is_file())
        self.assertFalse((skill_root / "references" / "全局规则.md").exists())

    def test_all_task_semantics_remain_available_outside_file_write_route(self) -> None:
        """规则拆分后，所有任务适用的工程与沟通边界不能只在写文件时加载。"""
        skill_root = ROOT / "skills" / "jojo-code-guard"
        skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        common_path = skill_root / "references" / "通用行为规则.md"
        autoload_text = (
            skill_root / "references" / "自动加载规则.md"
        ).read_text(encoding="utf-8")

        self.assertTrue(common_path.is_file())
        self.assertIn("[通用行为规则](references/通用行为规则.md)", skill_text)
        self.assertIn("所有任务", skill_text)
        self.assertIn("通用行为规则", autoload_text)
        self.assertIn("所有任务", autoload_text)
        common_text = common_path.read_text(encoding="utf-8")
        semantic_anchors = {
            "工程决策": ("更高优先级规则", "验收条件不是", "项目阶段相称"),
            "用户约束成本": ("常规做法", "可行替代方案", "推荐理由", "未经用户确认"),
            "辅助问题止损": ("阻断主体任务", "30 分钟", "低风险最小替代方案", "长任务"),
            "相称安全": ("输入、权限和数据风险", "基础安全"),
            "授权与诚实": ("外部系统状态", "不虚构", "无法验证"),
            "乱码降级": ("中文乱码", "英文输出", "注释仍使用中文"),
            "代码注释": ("全局变量", "重要局部变量", "关键代码段"),
            "失败沟通": ("失败时", "可行的下一步"),
            "文档组织": ("文档名称使用中文", "实际工作流", "原子化", "Review"),
        }
        for topic, anchors in semantic_anchors.items():
            with self.subTest(topic=topic):
                for anchor in anchors:
                    self.assertIn(anchor, common_text)

    def test_usage_documents_global_rule_sync_safety_contract(self) -> None:
        """doctor 已实现的节级同步保护必须能从工具说明中完整获知。"""
        usage = (
            ROOT / "skills" / "jojo-code-guard" / "references" / "usage.md"
        ).read_text(encoding="utf-8")

        for anchor in (
            "旧标题",
            "保留最靠前",
            "删除其余重复",
            "UTF-8 BOM",
            "混合换行",
            "写入后复核",
            "--sync-global-rules",
            "--yes",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, usage)

    def test_file_guard_keeps_hook_and_editor_safety_contracts(self) -> None:
        """拆分后的写入模块仍应公开 Hook 基线、编辑器和 Git 转换保护。"""
        file_guard = (
            ROOT / "skills" / "jojo-code-guard" / "references" / "通用文件守护.md"
        ).read_text(encoding="utf-8")

        for anchor in (
            "files.trimTrailingWhitespace",
            "files.insertFinalNewline",
            "pre_existing",
            "继续原请求",
            "仓库 local 配置",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, file_guard)

    def test_powershell_scope_preserves_documented_cross_platform_ps7_rules(self) -> None:
        """PowerShell 总体适用范围不能否定同文档中的 PS 7 Unix 规则。"""
        powershell = (
            ROOT / "skills" / "jojo-code-guard" / "PowerShell规则.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn("Windows 平台 ONLY", powershell)
        self.assertNotIn("非 Windows 则禁止生成 .ps1", powershell)
        self.assertIn("非 Windows 仅在目标明确使用 PowerShell 7", powershell)
        self.assertIn("PS 7 (Unix)", powershell)

    def test_public_subskills_load_main_guard_and_use_explicit_default_prompts(self) -> None:
        """独立入口必须自行加载主守护，UI 默认提示必须显式调用对应 Skill。"""
        skill_ids = (
            "jojo-code-guard-doctor",
            "jojo-code-guard-check-diff",
            "jojo-code-guard-help",
        )
        for skill_id in skill_ids:
            with self.subTest(skill_id=skill_id):
                skill_root = ROOT / "skills" / skill_id
                skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
                metadata = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
                self.assertIn("../jojo-code-guard/SKILL.md", skill)
                self.assertIn("完整读取", skill)
                self.assertIn("场景路由", skill)
                self.assertIn(f"${skill_id}", metadata)

    def test_read_only_encoding_and_large_diff_diagnostics_have_an_explicit_route(self) -> None:
        """只读编码/BOM/EOL/异常 diff 诊断不能退化成仅加载 AGENTS.md。"""
        skill = (ROOT / "skills" / "jojo-code-guard" / "SKILL.md").read_text(encoding="utf-8")

        for anchor in ("只读诊断", "编码", "BOM", "换行", "异常 diff", "通用文件守护", "usage.md"):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, skill)

    def test_non_git_projects_still_load_project_level_file_rules(self) -> None:
        """项目不是 Git 仓库时也必须发现 AGENTS、EditorConfig 和编辑器规则。"""
        file_guard = (
            ROOT / "skills" / "jojo-code-guard" / "references" / "通用文件守护.md"
        ).read_text(encoding="utf-8")

        self.assertIn("无论是否属于 Git 仓库", file_guard)
        self.assertIn("只有目标属于 Git 仓库", file_guard)
        self.assertIn(".gitattributes", file_guard)
        self.assertIn("git status --short", file_guard)

        post_write = file_guard.split("## 写入后闭环", 1)[1].split(
            "## Git 与编辑器风险", 1
        )[0]
        self.assertIn("只有目标属于 Git 仓库", post_write)
        self.assertIn("非 Git 项目", post_write)
        self.assertIn("编辑前字节基线", post_write)
        conditional = post_write.index("只有目标属于 Git 仓库时，Hook 未执行才")
        command = post_write.index('python "<jojo-code-guard>/scripts/check_diff.py" --repo .')
        self.assertLess(conditional, command)
        self.assertEqual(post_write.count("check_diff.py"), 1)
        self.assertNotIn("Hook 未执行时，在每次写入后运行", post_write)

    def test_powershell_rules_do_not_prescribe_output_pollution_or_unsafe_wrappers(self) -> None:
        """PowerShell 规则不得要求污染成功流、破坏编码或拼接可注入批处理命令。"""
        powershell = (
            ROOT / "skills" / "jojo-code-guard" / "PowerShell规则.md"
        ).read_text(encoding="utf-8")

        for unsafe in (
            "开头需要添加信息输出",
            "head -c 3 script.ps1 | xxd",
            "printf '\\xef\\xbb\\xbf' | cat - script.ps1 > tmp.ps1",
            '$batContent = "@echo off`r`n`"$exe`" $ExeArgs',
            "Get-Process -Id $p.Id -IncludeUserName | Stop-Process -Force",
            "全部文件 I/O 显式指定了 `-Encoding UTF8`",
            "PS5.1: 文件有 BOM?",
            "`$env:HOME` | ❌ | ✅ (映射到 USERPROFILE)",
            ".StandardOutput.ReadToEnd()",
            ".StandardError.ReadToEnd()",
        ):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, powershell)
        for required in (
            "注释元数据",
            "不得向成功输出流",
            "已有文件保持原始编码",
            "UTF8Encoding($false)",
            "ProcessStartInfo.ArgumentList",
            "不可信参数",
            "仅终止父进程",
            "自动变量 `$HOME`",
            "ReadToEndAsync()",
        ):
            with self.subTest(required=required):
                self.assertIn(required, powershell)

        stdout_start = powershell.index("$stdoutTask = $process.StandardOutput.ReadToEndAsync()")
        stderr_start = powershell.index("$stderrTask = $process.StandardError.ReadToEndAsync()")
        wait = powershell.index("$process.WaitForExit()", stdout_start)
        stdout_result = powershell.index("$stdout = $stdoutTask.GetAwaiter().GetResult()")
        stderr_result = powershell.index("$stderr = $stderrTask.GetAwaiter().GetResult()")
        self.assertLess(stdout_start, stderr_start)
        self.assertLess(stderr_start, wait)
        self.assertLess(wait, stdout_result)
        self.assertLess(stdout_result, stderr_result)

    def test_documented_powershell_dual_stream_capture_does_not_deadlock(self) -> None:
        """文档示例必须能同时排空大 stdout/stderr，不能靠关键词测试掩盖死锁。"""
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("当前环境没有 PowerShell 7")
        powershell = (
            ROOT / "skills" / "jojo-code-guard" / "PowerShell规则.md"
        ).read_text(encoding="utf-8")
        marker = "# PowerShell 7：同时隐藏窗口、捕获输出并保持参数边界。"
        snippet_start = powershell.index(marker)
        snippet_end = powershell.index("\n```", snippet_start)
        snippet = powershell[snippet_start:snippet_end]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "dual_stream_child.py"
            child.write_text(
                "import sys\n"
                "sys.stdout.buffer.write(b'O' * (1024 * 1024))\n"
                "sys.stdout.buffer.flush()\n"
                "sys.stderr.buffer.write(b'E' * (1024 * 1024))\n"
                "sys.stderr.buffer.flush()\n",
                encoding="utf-8",
            )

            def quote(value: object) -> str:
                """按 PowerShell 单引号字面量规则转义测试路径。"""
                return str(value).replace("'", "''")

            script = root / "capture.ps1"
            script.write_text(
                f"$exe = '{quote(sys.executable)}'\n"
                f"$arguments = @('{quote(child)}')\n"
                + snippet
                + "\nif ($stdout.Length -ne 1048576 -or $stderr.Length -ne 1048576) { exit 9 }\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [pwsh, "-NoProfile", "-File", str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def test_doctor_docs_distinguish_each_authorized_mutation(self) -> None:
        """doctor 说明不能把仓库修复、安装和全局节同步混写成“只同步”。"""
        documents = (
            ROOT / "README.md",
            ROOT / "skills" / "jojo-code-guard" / "references" / "usage.md",
            ROOT / "skills" / "jojo-code-guard-help" / "SKILL.md",
        )
        for path in documents:
            with self.subTest(path=str(path)):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("确认后只同步自动加载节", text)
                self.assertNotIn("确认后只新增或更新", text)
                self.assertIn("默认只读", text)
                self.assertIn("同步", text)

    def test_session_start_load_failure_stops_the_whole_task(self) -> None:
        """主 Skill 缺失时 Hook 不能只暂停文件修改后继续其他任务。"""
        hook = (ROOT / "hooks" / "session-start").read_text(encoding="utf-8")

        self.assertIn("暂停当前任务", hook)
        self.assertIn("只报告加载失败", hook)

    def test_runtime_resources_are_covered_by_integrity_manifests(self) -> None:
        """公开运行时资源必须进入对应客户端的 doctor 完整性清单。"""
        shared_required = {
            "hooks/hooks.json",
            "hooks/session-start",
            "hooks/post-write-check",
            "hooks/run-hook.cmd",
            "skills/jojo-code-guard/SKILL.md",
            "skills/jojo-code-guard/references/通用行为规则.md",
            "skills/jojo-code-guard/references/通用文件守护.md",
            "skills/jojo-code-guard/references/C++专项规则.md",
            "skills/jojo-code-guard/references/Git操作规则.md",
            "skills/jojo-code-guard/PowerShell规则.md",
            "skills/jojo-code-guard/references/usage.md",
            "skills/jojo-code-guard/references/自动加载规则.md",
            "skills/jojo-code-guard/scripts/check_diff.py",
            "skills/jojo-code-guard/scripts/guard_core.py",
            "skills/jojo-code-guard/scripts/hook_baseline.py",
            "skills/jojo-code-guard/scripts/hook_check.py",
            "skills/jojo-code-guard/scripts/install_hook.py",
            "skills/jojo-code-guard-doctor/SKILL.md",
            "skills/jojo-code-guard-check-diff/SKILL.md",
            "skills/jojo-code-guard-help/SKILL.md",
            "skills/jojo-code-guard/agents/openai.yaml",
            "skills/jojo-code-guard-doctor/agents/openai.yaml",
            "skills/jojo-code-guard-check-diff/agents/openai.yaml",
            "skills/jojo-code-guard-help/agents/openai.yaml",
        }
        claude_only_required = {
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
            "commands/doctor.md",
            "commands/check-diff.md",
            "commands/help.md",
        }
        codex_only_required = {
            ".codex-plugin/plugin.json",
        }

        self.assertEqual(set(doctor.PLUGIN_RESOURCE_SHA256), shared_required)
        self.assertEqual(set(doctor.CLAUDE_PLUGIN_RESOURCE_SHA256), claude_only_required)
        self.assertEqual(set(doctor.CODEX_PLUGIN_RESOURCE_SHA256), codex_only_required)
        self.assertIn("skills/jojo-code-guard/scripts/doctor.py", doctor.PLUGIN_REQUIRED_FILES)

    def test_sync_refreshes_windows_launcher_and_removes_obsolete_shell_launcher(self) -> None:
        """同步包应刷新 Windows 启动器，并移除已废弃的 shell 启动器。"""
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "adapter"
            manifest_dir = destination / ".claude-plugin"
            manifest_dir.mkdir(parents=True)
            shutil.copy2(ROOT / ".claude-plugin" / "plugin.json", manifest_dir / "plugin.json")
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
            references = destination / "skills" / "jojo-code-guard" / "references"
            obsolete_documents = [
                references / name
                for name in ("兼容性改进计划.md", "生效与验收.md", "全局规则.md")
            ]
            references.mkdir(parents=True)
            for document in obsolete_documents:
                document.write_text("obsolete\n", encoding="utf-8")
            legacy_digest = sync_claude_plugin._tree_content_digest(
                sync_claude_plugin._safe_tree_snapshot(destination)
            )
            version = json.loads((manifest_dir / "plugin.json").read_text(encoding="utf-8"))["version"]
            with mock.patch.dict(
                sync_claude_plugin.LEGACY_PACKAGE_TREE_SHA256,
                {version: frozenset({legacy_digest})},
                clear=True,
            ), mock.patch.dict(os.environ, {"JOJO_CLAUDE_PLUGIN_DIR": str(destination)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = sync_claude_plugin.main()

            self.assertEqual(result, 0)
            self.assertEqual(
                (hooks_dir / "run-hook.cmd").read_bytes(),
                (ROOT / "hooks" / "run-hook.cmd").read_bytes(),
            )
            self.assertFalse((hooks_dir / "run-hook.sh").exists())
            self.assertFalse(
                (destination / "skills" / "jojo-code-guard" / "通用规则.md").exists()
            )
            self.assertFalse(old_skill.exists())
            self.assertFalse(old_commit_skill.exists())
            self.assertTrue(all(not document.exists() for document in obsolete_documents))
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

    def test_sync_build_failure_preserves_existing_install(self) -> None:
        """新适配包完成校验前失败时，旧安装目录必须保持原样。"""
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            destination = parent / "adapter"
            sync_claude_plugin._build_adapter(ROOT, destination)
            marker = destination / sync_claude_plugin.OWNERSHIP_MARKER_NAME
            existing = marker.read_bytes()

            with mock.patch.dict(os.environ, {"JOJO_CLAUDE_PLUGIN_DIR": str(destination)}), mock.patch.object(
                sync_claude_plugin, "_build_adapter", side_effect=RuntimeError("validation failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "validation failed"):
                    sync_claude_plugin.main()

            self.assertEqual(marker.read_bytes(), existing)
            self.assertEqual(
                [path for path in parent.iterdir() if path.name.startswith(".adapter.sync-")],
                [],
            )

    def test_adapter_validation_requires_runtime_entrypoints(self) -> None:
        """适配包不能遗漏主 Skill 路由模块或公开运行入口。"""
        adapters = (
            (sync_claude_plugin._build_adapter, sync_claude_plugin._validate_adapter),
            (sync_codex_plugin._build_adapter, sync_codex_plugin._validate_adapter),
        )
        relative_paths = (
            Path("skills/jojo-code-guard/references/通用行为规则.md"),
            Path("skills/jojo-code-guard/references/通用文件守护.md"),
            Path("skills/jojo-code-guard/references/C++专项规则.md"),
            Path("skills/jojo-code-guard/references/Git操作规则.md"),
            Path("skills/jojo-code-guard/PowerShell规则.md"),
            Path("skills/jojo-code-guard/references/usage.md"),
            Path("skills/jojo-code-guard/references/自动加载规则.md"),
            Path("skills/jojo-code-guard/scripts/doctor.py"),
            Path("skills/jojo-code-guard/scripts/check_diff.py"),
            Path("skills/jojo-code-guard/scripts/guard_core.py"),
            Path("skills/jojo-code-guard/scripts/hook_baseline.py"),
            Path("skills/jojo-code-guard/scripts/hook_check.py"),
            Path("skills/jojo-code-guard/scripts/install_hook.py"),
            Path("skills/jojo-code-guard-doctor/SKILL.md"),
            Path("skills/jojo-code-guard-check-diff/SKILL.md"),
            Path("skills/jojo-code-guard-help/SKILL.md"),
            Path("skills/jojo-code-guard/agents/openai.yaml"),
            Path("skills/jojo-code-guard-doctor/agents/openai.yaml"),
            Path("skills/jojo-code-guard-check-diff/agents/openai.yaml"),
            Path("skills/jojo-code-guard-help/agents/openai.yaml"),
        )
        for builder, validator in adapters:
            for relative in relative_paths:
                with self.subTest(validator=validator.__module__, relative=str(relative)):
                    with tempfile.TemporaryDirectory() as directory:
                        destination = Path(directory) / "adapter"
                        builder(ROOT, destination)
                        (destination / relative).unlink()

                        with self.assertRaises(FileNotFoundError):
                            validator(destination)

    def test_claude_adapter_validation_requires_command_entrypoints(self) -> None:
        """Claude 适配包不能在公开 slash command 缺失时通过校验。"""
        relative_paths = (
            Path("commands/doctor.md"),
            Path("commands/check-diff.md"),
            Path("commands/help.md"),
        )
        for relative in relative_paths:
            with self.subTest(relative=str(relative)), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "adapter"
                sync_claude_plugin._build_adapter(ROOT, destination)
                (destination / relative).unlink()

                with self.assertRaises(FileNotFoundError):
                    sync_claude_plugin._validate_adapter(destination)

    def test_adapter_validation_rejects_unknown_public_entrypoints(self) -> None:
        """真实构建包不能夹带客户端会自动发现的未声明入口。"""
        cases = (
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("commands/unexpected.md"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("commands/nested/unexpected.md"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("skills/unexpected-entry/SKILL.md"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("skills/category/unexpected-entry/SKILL.md"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("skills/unexpected-entry/skill.md"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("SKILL.md"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("agents/unexpected.md"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path(".mcp.json"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path(".lsp.json"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("monitors/monitors.json"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("bin/unexpected-tool"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("settings.json"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("workflows/unexpected.js"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("output-styles/unexpected.md"),
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path("themes/unexpected.json"),
            ),
            (
                sync_codex_plugin._build_adapter,
                sync_codex_plugin._validate_adapter,
                Path("skills/unexpected-entry/SKILL.md"),
            ),
            (
                sync_codex_plugin._build_adapter,
                sync_codex_plugin._validate_adapter,
                Path("skills/category/unexpected-entry/SKILL.md"),
            ),
            (
                sync_codex_plugin._build_adapter,
                sync_codex_plugin._validate_adapter,
                Path("skills/unexpected-entry/skill.md"),
            ),
            (
                sync_codex_plugin._build_adapter,
                sync_codex_plugin._validate_adapter,
                Path(".mcp.json"),
            ),
            (
                sync_codex_plugin._build_adapter,
                sync_codex_plugin._validate_adapter,
                Path(".app.json"),
            ),
        )
        for builder, validator, relative in cases:
            with self.subTest(validator=validator.__module__, relative=str(relative)):
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "adapter"
                    builder(ROOT, destination)
                    unexpected = destination / relative
                    unexpected.parent.mkdir(parents=True, exist_ok=True)
                    unexpected.write_text("unexpected entry\n", encoding="utf-8")

                    with self.assertRaisesRegex(RuntimeError, "未声明公开入口"):
                        validator(destination)

    def test_adapter_validation_rejects_manifest_component_overrides(self) -> None:
        """manifest 不能用自定义路径或内联定义绕过公开入口白名单。"""
        cases = (
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path(".claude-plugin/plugin.json"),
                "commands",
                "./extra/pwn.md",
            ),
            (
                sync_claude_plugin._build_adapter,
                sync_claude_plugin._validate_adapter,
                Path(".claude-plugin/plugin.json"),
                "mcpServers",
                {"unexpected": {"command": "unexpected-tool"}},
            ),
            (
                sync_codex_plugin._build_adapter,
                sync_codex_plugin._validate_adapter,
                Path(".codex-plugin/plugin.json"),
                "mcpServers",
                {"unexpected": {"command": "unexpected-tool"}},
            ),
            (
                sync_codex_plugin._build_adapter,
                sync_codex_plugin._validate_adapter,
                Path(".codex-plugin/plugin.json"),
                "apps",
                "./unexpected-app.json",
            ),
        )
        for builder, validator, manifest_relative, key, value in cases:
            with self.subTest(validator=validator.__module__, key=key):
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "adapter"
                    builder(ROOT, destination)
                    manifest_path = destination / manifest_relative
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest[key] = value
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

                    with self.assertRaisesRegex(RuntimeError, "manifest.*未允许"):
                        validator(destination)

    def test_claude_adapter_validation_rejects_unsafe_marketplace_contract(self) -> None:
        """marketplace 不能补充入口、改换来源或描述另一个插件版本。"""
        cases = (
            "duplicate-plugin",
            "wrong-name",
            "wrong-source",
            "non-strict",
            "wrong-version",
            "owner-not-object",
            "owner-empty-name",
            "inline-hook",
            "inline-mcp",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "adapter"
                sync_claude_plugin._build_adapter(ROOT, destination)
                marketplace_path = destination / ".claude-plugin" / "marketplace.json"
                marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
                plugin = marketplace["plugins"][0]
                if case == "duplicate-plugin":
                    marketplace["plugins"].append(dict(plugin))
                elif case == "wrong-name":
                    plugin["name"] = "another-plugin"
                elif case == "wrong-source":
                    plugin["source"] = "./another-plugin"
                elif case == "non-strict":
                    plugin["strict"] = False
                elif case == "wrong-version":
                    plugin["version"] = "9.9.9"
                elif case == "owner-not-object":
                    marketplace["owner"] = "zackxjxc"
                elif case == "owner-empty-name":
                    marketplace["owner"] = {"name": ""}
                elif case == "inline-hook":
                    plugin["hooks"] = {
                        "SessionStart": [
                            {"hooks": [{"type": "command", "command": "unexpected"}]}
                        ]
                    }
                else:
                    plugin["mcpServers"] = {
                        "unexpected": {"command": "unexpected-tool"}
                    }
                marketplace_path.write_text(
                    json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(RuntimeError, "marketplace"):
                    sync_claude_plugin._validate_adapter(destination)

    def test_copy_tree_rejects_real_source_links_and_hardlinks(self) -> None:
        """复制前必须拒绝真实目录链接和普通文件硬链接，不能先解引用再校验。"""
        synchronizers = (sync_claude_plugin, sync_codex_plugin)
        for synchronizer in synchronizers:
            with self.subTest(synchronizer=synchronizer.__name__, kind="directory-link"):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / "source"
                    external = root / "external"
                    destination = root / "copied"
                    source.mkdir()
                    external.mkdir()
                    (external / "payload.txt").write_text("outside\n", encoding="utf-8")
                    linked = source / "linked"
                    if os.name == "nt":
                        result = subprocess.run(
                            ["cmd.exe", "/c", "mklink", "/J", str(linked), str(external)],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            check=False,
                        )
                        if result.returncode != 0:
                            self.skipTest("当前环境无法创建 Windows junction")
                    else:
                        linked.symlink_to(external, target_is_directory=True)

                    with self.assertRaisesRegex(RuntimeError, "链接|reparse"):
                        synchronizer._copy_tree(source, destination)
                    self.assertFalse(destination.exists())

            with self.subTest(synchronizer=synchronizer.__name__, kind="hardlink"):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / "source"
                    external = root / "external.txt"
                    destination = root / "copied"
                    source.mkdir()
                    external.write_text("outside\n", encoding="utf-8")
                    try:
                        os.link(external, source / "payload.txt")
                    except OSError as error:
                        self.skipTest(f"当前文件系统无法创建硬链接：{error}")

                    with self.assertRaisesRegex(RuntimeError, "硬链接"):
                        synchronizer._copy_tree(source, destination)
                    self.assertFalse(destination.exists())

    def test_adapter_validation_rejects_hardlinked_required_file(self) -> None:
        """staging 内白名单必需文件也不能与目录外文件共享 inode。"""
        adapters = (
            (sync_claude_plugin._build_adapter, sync_claude_plugin._validate_adapter),
            (sync_codex_plugin._build_adapter, sync_codex_plugin._validate_adapter),
        )
        relative = Path("skills/jojo-code-guard/SKILL.md")
        for builder, validator in adapters:
            with self.subTest(validator=validator.__module__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"
                    builder(ROOT, destination)
                    required = destination / relative
                    external = root / "external-skill.md"
                    external.write_bytes(required.read_bytes())
                    required.unlink()
                    try:
                        os.link(external, required)
                    except OSError as error:
                        self.skipTest(f"当前文件系统无法创建硬链接：{error}")

                    with self.assertRaisesRegex(RuntimeError, "硬链接"):
                        validator(destination)

    def test_sync_rejects_unmanaged_existing_destination(self) -> None:
        """环境变量即使误指向普通目录，也不得覆盖并递归删除其中用户数据。"""
        cases = (
            (sync_claude_plugin, "JOJO_CLAUDE_PLUGIN_DIR"),
            (sync_codex_plugin, "JOJO_CODEX_PLUGIN_DIR"),
        )
        for synchronizer, variable in cases:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "user-data"
                    destination.mkdir()
                    marker = destination / "keep.txt"
                    marker.write_text("keep\n", encoding="utf-8")

                    with mock.patch.dict(os.environ, {variable: str(destination)}):
                        with self.assertRaisesRegex(RuntimeError, "受管|manifest"):
                            synchronizer.main()

                    self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
                    self.assertEqual(list(Path(directory).glob(".user-data.backup-*")), [])

    def test_adapter_validation_rejects_link_like_required_file(self) -> None:
        """白名单内的必需文件也不能由 symlink、junction 或 reparse point 提供。"""
        adapters = (
            (sync_claude_plugin._build_adapter, sync_claude_plugin._validate_adapter),
            (sync_codex_plugin._build_adapter, sync_codex_plugin._validate_adapter),
        )
        relative = Path("skills/jojo-code-guard/SKILL.md")
        for builder, validator in adapters:
            with self.subTest(validator=validator.__module__):
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "adapter"
                    builder(ROOT, destination)
                    linked_file = destination / relative
                    original_link_check = validator.__globals__["_path_is_link_like"]

                    def is_link_like(path: Path) -> bool:
                        return path == linked_file or original_link_check(path)

                    with mock.patch.dict(
                        validator.__globals__,
                        {"_path_is_link_like": is_link_like},
                    ):
                        with self.assertRaisesRegex(RuntimeError, "链接型必需资源"):
                            validator(destination)

    def test_replace_directory_rolls_back_on_keyboard_interrupt(self) -> None:
        """第二次目录切换被中断时，旧安装仍必须原位可用。"""
        replacers = (
            sync_claude_plugin._replace_directory,
            sync_codex_plugin._replace_directory,
        )
        for replacer in replacers:
            for interrupt_timing in ("before", "after"):
                with self.subTest(
                    replacer=replacer.__module__,
                    interrupt_timing=interrupt_timing,
                ):
                    self._assert_replace_interrupt_rolls_back(replacer, interrupt_timing)

    def _assert_replace_interrupt_rolls_back(
        self,
        replacer: object,
        interrupt_timing: str,
    ) -> None:
        """在目录切换调用生效前后注入中断，并核对旧安装恢复。"""
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            destination = parent / "adapter"
            staging = parent / "staging"
            destination.mkdir()
            staging.mkdir()
            (destination / "version.txt").write_text("old\n", encoding="utf-8")
            (staging / "version.txt").write_text("new\n", encoding="utf-8")
            real_replace = replacer.__globals__["_move_entry_no_replace"]
            replace_count = 0

            def interrupt_second_replace(source: object, target: object) -> None:
                nonlocal replace_count
                replace_count += 1
                if replace_count == 2:
                    if interrupt_timing == "after":
                        real_replace(source, target)
                    raise KeyboardInterrupt("interrupted")
                real_replace(source, target)

            with mock.patch.dict(
                replacer.__globals__,
                {"_move_entry_no_replace": interrupt_second_replace},
            ):
                with self.assertRaises(KeyboardInterrupt):
                    replacer(staging, destination)

            self.assertEqual(
                (destination / "version.txt").read_text(encoding="utf-8"),
                "old\n",
            )
            self.assertTrue(staging.is_dir())
            self.assertEqual(
                [path for path in parent.iterdir() if ".backup-" in path.name],
                [],
            )

    def test_replace_directory_preserves_every_version_on_concurrent_destination(self) -> None:
        """第二次 rename 前目标被并发创建时，旧、新、并发版本都必须保留并报告。"""
        replacers = (
            sync_claude_plugin._replace_directory,
            sync_codex_plugin._replace_directory,
        )
        for replacer in replacers:
            with self.subTest(replacer=replacer.__module__):
                with tempfile.TemporaryDirectory() as directory:
                    parent = Path(directory)
                    destination = parent / "adapter"
                    staging = parent / "staging"
                    destination.mkdir()
                    staging.mkdir()
                    (destination / "version.txt").write_text("old\n", encoding="utf-8")
                    (staging / "version.txt").write_text("new\n", encoding="utf-8")
                    real_replace = replacer.__globals__["_move_entry_no_replace"]

                    def fail_second_replace(source: object, target: object) -> None:
                        if Path(source) == staging and Path(target) == destination:
                            destination.mkdir()
                            (destination / "version.txt").write_text(
                                "concurrent\n", encoding="utf-8"
                            )
                            raise PermissionError("second rename failed")
                        real_replace(source, target)

                    with mock.patch.dict(
                        replacer.__globals__,
                        {"_move_entry_no_replace": fail_second_replace},
                    ):
                        with self.assertRaises(BaseException) as raised:
                            replacer(staging, destination)
                    self.assertIsInstance(raised.exception, RuntimeError)
                    self.assertRegex(str(raised.exception), "并发|备份仍位于")

                    self.assertEqual(
                        (destination / "version.txt").read_text(encoding="utf-8"),
                        "concurrent\n",
                    )
                    self.assertEqual(
                        (staging / "version.txt").read_text(encoding="utf-8"),
                        "new\n",
                    )
                    backups = list(parent.glob(".adapter.backup-*"))
                    self.assertEqual(len(backups), 1)
                    self.assertEqual(
                        (backups[0] / "version.txt").read_text(encoding="utf-8"),
                        "old\n",
                    )

    def test_replace_directory_reports_committed_install_when_backup_cleanup_fails(self) -> None:
        """提交后的清理失败必须报告新安装已生效，并保留可定位的旧备份。"""
        replacers = (
            sync_claude_plugin._replace_directory,
            sync_codex_plugin._replace_directory,
        )
        for replacer in replacers:
            with self.subTest(replacer=replacer.__module__):
                with tempfile.TemporaryDirectory() as directory:
                    parent = Path(directory)
                    destination = parent / "adapter"
                    staging = parent / "staging"
                    destination.mkdir()
                    staging.mkdir()
                    (destination / "version.txt").write_text("old\n", encoding="utf-8")
                    (staging / "version.txt").write_text("new\n", encoding="utf-8")
                    real_rmtree = replacer.__globals__["shutil"].rmtree

                    def fail_backup_cleanup(path: object) -> None:
                        candidate = Path(path)
                        if candidate.name == "owned" and ".backup-" in candidate.parent.name:
                            raise PermissionError("backup is busy")
                        real_rmtree(path)

                    with mock.patch.object(
                        replacer.__globals__["shutil"],
                        "rmtree",
                        side_effect=fail_backup_cleanup,
                    ):
                        with self.assertRaises(BaseException) as raised:
                            replacer(staging, destination)
                    self.assertIsInstance(raised.exception, RuntimeError)
                    self.assertRegex(str(raised.exception), "新安装已生效.*备份")

                    self.assertEqual(
                        (destination / "version.txt").read_text(encoding="utf-8"),
                        "new\n",
                    )
                    preserved = [
                        path
                        for path in parent.rglob("version.txt")
                        if path != destination / "version.txt"
                        and path.read_text(encoding="utf-8") == "old\n"
                    ]
                    self.assertEqual(len(preserved), 1)
                    self.assertIn("discard", str(preserved[0]))

    def test_sync_recovers_transaction_interrupted_between_directory_renames(self) -> None:
        """进程在两次 rename 间终止后，下次启动必须先恢复旧安装。"""
        cases = (
            (sync_claude_plugin, "JOJO_CLAUDE_PLUGIN_DIR"),
            (sync_codex_plugin, "JOJO_CODEX_PLUGIN_DIR"),
        )
        scripts_path = str(ROOT / "scripts")
        for synchronizer, variable in cases:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    parent = Path(directory)
                    destination = parent / "adapter"
                    synchronizer._build_adapter(ROOT, destination)
                    marker = destination / synchronizer.OWNERSHIP_MARKER_NAME
                    old_install = marker.read_bytes()
                    environment = os.environ.copy()
                    environment[variable] = str(destination)
                    code = (
                        "import os, pathlib, sys\n"
                        f"sys.path.insert(0, {scripts_path!r})\n"
                        f"import {synchronizer.__name__} as sync\n"
                        f"destination = pathlib.Path({str(destination)!r})\n"
                        "real_replace = sync._move_entry_no_replace\n"
                        "def crash_before_install(source, target):\n"
                        "    source_path = pathlib.Path(source)\n"
                        "    target_path = pathlib.Path(target)\n"
                        "    if source_path.name.startswith('.adapter.sync-') and target_path == destination:\n"
                        "        os._exit(73)\n"
                        "    real_replace(source, target)\n"
                        "sync._move_entry_no_replace = crash_before_install\n"
                        "sync.main()\n"
                    )
                    crashed = subprocess.run(
                        [sys.executable, "-c", code],
                        cwd=ROOT,
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                    self.assertEqual(
                        crashed.returncode,
                        73,
                        crashed.stderr.decode("utf-8", errors="replace"),
                    )
                    self.assertFalse(destination.exists())

                    with mock.patch.dict(os.environ, {variable: str(destination)}), mock.patch.object(
                        synchronizer,
                        "_build_adapter",
                        side_effect=RuntimeError("stop after recovery"),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "stop after recovery"):
                            synchronizer.main()

                    self.assertTrue(marker.is_file())
                    self.assertEqual(marker.read_bytes(), old_install)
                    self.assertFalse((parent / ".adapter.sync-transaction.json").exists())
                    self.assertEqual(list(parent.glob(".adapter.backup-*")), [])

    def test_replace_directory_rejects_link_like_destination_and_parent(self) -> None:
        """同步不能经由链接型安装目录或其父目录写入、备份或清理外部目标。"""
        replacers = (
            sync_claude_plugin._replace_directory,
            sync_codex_plugin._replace_directory,
        )
        for replacer in replacers:
            for linked_part in ("destination", "parent"):
                with self.subTest(replacer=replacer.__module__, linked_part=linked_part):
                    with tempfile.TemporaryDirectory() as directory:
                        parent = Path(directory)
                        destination = parent / "adapter"
                        staging = parent / "staging"
                        destination.mkdir()
                        staging.mkdir()
                        old_marker = destination / "version.txt"
                        new_marker = staging / "version.txt"
                        old_marker.write_text("old\n", encoding="utf-8")
                        new_marker.write_text("new\n", encoding="utf-8")
                        linked_path = destination if linked_part == "destination" else parent
                        original_link_check = replacer.__globals__["_path_is_link_like"]

                        def is_link_like(path: Path) -> bool:
                            return path == linked_path or original_link_check(path)

                        with mock.patch.dict(
                            replacer.__globals__,
                            {"_path_is_link_like": is_link_like},
                        ), mock.patch.object(replacer.__globals__["shutil"], "rmtree") as rmtree:
                            with self.assertRaisesRegex(RuntimeError, "链接型"):
                                replacer(staging, destination)

                        self.assertEqual(old_marker.read_text(encoding="utf-8"), "old\n")
                        self.assertEqual(new_marker.read_text(encoding="utf-8"), "new\n")
                        rmtree.assert_not_called()

    def test_replace_directory_rejects_ordinary_parent_identity_swap(self) -> None:
        """父目录即使被换成普通目录，也不能让已校验路径落到另一棵树。"""
        synchronizers = (sync_claude_plugin, sync_codex_plugin)
        for synchronizer in synchronizers:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    container = Path(directory)
                    parent = container / "install-root"
                    parent.mkdir()
                    destination = parent / "adapter"
                    staging = parent / "staging"
                    destination.mkdir()
                    staging.mkdir()
                    (destination / "version.txt").write_text("old\n", encoding="utf-8")
                    (staging / "version.txt").write_text("new\n", encoding="utf-8")
                    parent_snapshot = synchronizer._capture_ancestor_snapshot(parent)
                    moved_parent = container / "original-install-root"
                    os.replace(parent, moved_parent)
                    parent.mkdir()

                    with self.assertRaisesRegex(RuntimeError, "父目录.*身份"):
                        synchronizer._replace_directory(
                            parent / "staging",
                            parent / "adapter",
                            expected_parent_snapshot=parent_snapshot,
                        )

                    self.assertEqual(
                        (moved_parent / "adapter" / "version.txt").read_text(
                            encoding="utf-8"
                        ),
                        "old\n",
                    )
                    self.assertEqual(
                        (moved_parent / "staging" / "version.txt").read_text(
                            encoding="utf-8"
                        ),
                        "new\n",
                    )

    def test_claude_adapter_validation_rejects_symlinked_discovery_roots(self) -> None:
        """Claude 自动发现根和未知 Skill 符号链接不能绕过入口枚举。"""
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
                destination = Path(directory) / "adapter"
                sync_claude_plugin._build_adapter(ROOT, destination)
                target = destination / relative
                if not target.exists():
                    target.mkdir(parents=True)

                def is_symlink(path: Path, target: Path = target) -> bool:
                    return path == target or original_is_symlink(path)

                with mock.patch.object(Path, "is_symlink", autospec=True, side_effect=is_symlink):
                    with self.assertRaisesRegex(RuntimeError, "未声明公开入口"):
                        sync_claude_plugin._validate_adapter(destination)

    def test_codex_adapter_validation_rejects_unknown_symlinked_skill(self) -> None:
        """Codex 未知顶层 Skill 符号链接不能绕过入口枚举。"""
        relative_paths = (Path("skills"), Path("skills/unexpected-link"))
        original_is_symlink = Path.is_symlink
        for relative in relative_paths:
            with self.subTest(relative=str(relative)), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "adapter"
                sync_codex_plugin._build_adapter(ROOT, destination)
                target = destination / relative
                if relative == Path("skills/unexpected-link"):
                    target.mkdir()

                def is_symlink(path: Path, target: Path = target) -> bool:
                    return path == target or original_is_symlink(path)

                with mock.patch.object(Path, "is_symlink", autospec=True, side_effect=is_symlink):
                    with self.assertRaisesRegex(RuntimeError, "未声明公开入口"):
                        sync_codex_plugin._validate_adapter(destination)

    @unittest.skipUnless(sys.platform == "win32", "Windows junction 专项回归测试")
    def test_claude_adapter_validation_rejects_empty_nested_command_junction(self) -> None:
        """空 junction 也必须立即阻断，不能在校验后再向其目标注入 command。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "adapter"
            external = root / "external-commands"
            junction = destination / "commands" / "nested-junction"
            sync_claude_plugin._build_adapter(ROOT, destination)
            external.mkdir()
            result = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(junction), str(external)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest("当前环境无法创建 Windows junction")
            try:
                with self.assertRaisesRegex(RuntimeError, "未声明公开入口"):
                    sync_claude_plugin._validate_adapter(destination)
            finally:
                os.rmdir(junction)

    def test_codex_sync_removes_obsolete_skill(self) -> None:
        """Codex 同步包应包含主 Skill，且不得保留旧 Skill。"""
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "adapter"
            manifest_dir = destination / ".codex-plugin"
            manifest_dir.mkdir(parents=True)
            shutil.copy2(ROOT / ".codex-plugin" / "plugin.json", manifest_dir / "plugin.json")
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
            (old_hooks / "run-hook.cmd").write_text("old launcher\n", encoding="utf-8")
            (old_hooks / "run-hook.sh").write_text("old launcher\n", encoding="utf-8")
            references = destination / "skills" / "jojo-code-guard" / "references"
            obsolete_documents = [
                references / name
                for name in ("兼容性改进计划.md", "生效与验收.md", "全局规则.md")
            ]
            references.mkdir(parents=True)
            for document in obsolete_documents:
                document.write_text("obsolete\n", encoding="utf-8")
            legacy_digest = sync_codex_plugin._tree_content_digest(
                sync_codex_plugin._safe_tree_snapshot(destination)
            )
            version = json.loads(
                (destination / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
            )["version"]
            with mock.patch.dict(
                sync_codex_plugin.LEGACY_PACKAGE_TREE_SHA256,
                {version: frozenset({legacy_digest})},
                clear=True,
            ), mock.patch.dict(os.environ, {"JOJO_CODEX_PLUGIN_DIR": str(destination)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = sync_codex_plugin.main()

            self.assertEqual(result, 0)
            self.assertFalse(old_skill.exists())
            self.assertFalse(old_commit_skill.exists())
            self.assertTrue(all(not document.exists() for document in obsolete_documents))
            self.assertTrue((destination / "skills" / "jojo-code-guard-doctor" / "SKILL.md").is_file())
            self.assertFalse((destination / "commands" / "check-diff.md").exists())
            self.assertFalse((destination / "commands" / "commit.md").exists())
            self.assertEqual(
                (destination / "hooks" / "run-hook.cmd").read_bytes(),
                (ROOT / "hooks" / "run-hook.cmd").read_bytes(),
            )
            self.assertFalse((destination / "hooks" / "run-hook.sh").exists())
            for relative in doctor.CODEX_PLUGIN_REQUIRED_FILES:
                self.assertTrue((destination / relative).is_file(), relative)
            self.assertTrue((destination / "hooks" / "hooks.json").is_file())
            self.assertEqual(
                (destination / "hooks" / "hooks.json").read_bytes(),
                (ROOT / "hooks" / "hooks.json").read_bytes(),
            )
            self.assertTrue((destination / "hooks" / "post-write-check").is_file())
            if os.name != "nt":
                self.assertTrue((destination / "hooks" / "post-write-check").stat().st_mode & stat.S_IXUSR)

    def test_manifest_covers_fork_and_uses_bounded_session_launcher(self) -> None:
        """SessionStart 应覆盖 fork，并以秒为单位设置短超时。"""
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        entry = data["hooks"]["SessionStart"][0]
        handler = entry["hooks"][0]

        self.assertEqual(entry["matcher"], "startup|resume|clear|compact|fork")
        self.assertNotIn("shell", handler)
        self.assertIn("${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}", handler["command"])
        self.assertIn("/hooks/session-start", handler["command"])
        self.assertIn("%PLUGIN_ROOT%", handler["commandWindows"])
        self.assertIn("run-hook.cmd", handler["commandWindows"])
        self.assertNotIn("bash", handler["commandWindows"].lower())
        self.assertEqual(handler["timeout"], 10)
        self.assertFalse(handler["async"])

    def test_manifest_runs_checks_for_edit_shell_and_stop_events(self) -> None:
        """共享 manifest 应在文件、shell 写入后检查，并在回合结束前兜底。"""
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        entries = data["hooks"]["PostToolUse"]
        prompt_entry = data["hooks"]["UserPromptSubmit"][0]
        prompt_handler = prompt_entry["hooks"][0]

        self.assertNotIn("matcher", prompt_entry)
        self.assertIn("post-write-check", prompt_handler["command"])
        self.assertEqual(prompt_handler["timeout"], 60)

        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0]["matcher"],
            "apply_patch|Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell",
        )
        handler = entries[0]["hooks"][0]
        self.assertNotIn("shell", handler)
        self.assertIn("${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}", handler["command"])
        self.assertIn("post-write-check", handler["command"])
        self.assertIn("run-hook.cmd", handler["commandWindows"])
        self.assertNotIn("bash", handler["commandWindows"].lower())
        self.assertEqual(handler["timeout"], 60)
        self.assertFalse(handler["async"])
        stop_entry = data["hooks"]["Stop"][0]
        self.assertNotIn("matcher", stop_entry)
        stop_handler = stop_entry["hooks"][0]
        self.assertIn("post-write-check", stop_handler["command"])
        self.assertEqual(stop_handler["command"], handler["command"])
        self.assertEqual(stop_handler["commandWindows"], handler["commandWindows"])
        self.assertEqual(stop_handler["timeout"], 60)

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
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            output = result.stdout.decode("utf-8", errors="replace")
            payload = json.loads(output)
            self.assertTrue(payload["continue"])
            self.assertEqual(payload["decision"], "block")
            self.assertIn("reason", payload)
            self.assertNotIn("stopReason", payload)
            self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("PURE_TEXT_REWRITE", context)
            self.assertIn("已有修改不等于本轮污染", context)
            self.assertIn("不得自动恢复、覆盖或删除来源不明", context)
            self.assertNotIn("修复污染", payload["reason"])

    def test_post_write_check_skips_non_git_project(self) -> None:
        """非 Git 项目不应因缺少仓库基线而报告 Hook 错误。"""
        with tempfile.TemporaryDirectory(prefix="jojo non-git ") as directory:
            project = Path(directory)
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
            environment["CLAUDE_PROJECT_DIR"] = str(project)

            result = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
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
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertTrue(payload["continue"])
        self.assertEqual(payload["decision"], "block")
        self.assertIn("无法解析", payload["hookSpecificOutput"]["additionalContext"])

    def test_post_write_check_reports_checker_failure_to_model(self) -> None:
        """检查程序自身失败时应返回可修复的结构化阻断，而不是丢失诊断。"""
        with tempfile.TemporaryDirectory(prefix="jojo failed hook ") as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            fake_script = root / "skills" / "jojo-code-guard" / "scripts" / "check_diff.py"
            fake_script.parent.mkdir(parents=True)
            fake_script.write_text(
                'import sys\nprint("checker failed", file=sys.stderr)\nraise SystemExit(2)\n',
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(root)
            result = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps({"cwd": str(project)}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(payload["decision"], "block")
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("差异检查执行失败", context)
        self.assertIn("checker failed", context)
        self.assertIn("不得自动恢复、覆盖或删除来源不明", context)

    def test_warning_diagnostics_do_not_block_or_rewake_stop(self) -> None:
        """非阻断诊断可反馈给写后检查，但不能让 Stop 无故继续一轮。"""
        with tempfile.TemporaryDirectory(prefix="jojo warning hook ") as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            fake_script = root / "skills" / "jojo-code-guard" / "scripts" / "check_diff.py"
            fake_script.parent.mkdir(parents=True)
            fake_script.write_text(
                'import json\nprint(json.dumps([{"level": "WARNING", "message": "review"}]))\n',
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(root)
            hook_input = json.dumps({"cwd": str(project), "stop_hook_active": False}).encode("utf-8")
            post = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=hook_input,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            stop = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps(
                    {"cwd": str(project), "hook_event_name": "Stop", "stop_hook_active": False}
                ).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(post.returncode, 0, post.stderr.decode("utf-8", errors="replace"))
        post_payload = json.loads(post.stdout.decode("utf-8"))
        self.assertTrue(post_payload["continue"])
        self.assertNotIn("decision", post_payload)
        self.assertIn("review", post_payload["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(stop.returncode, 0, stop.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(stop.stdout, b"")

    def test_stop_check_blocks_missed_shell_write(self) -> None:
        """回合结束检查应发现 shell 等路径遗漏的换行污染并要求继续修复。"""
        with tempfile.TemporaryDirectory(prefix="jojo stop hook ") as directory:
            project = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=project, check=True)
            source = project / "example.cpp"
            source.write_bytes(b"int main() { return 0; }\n")
            subprocess.run(["git", "add", "example.cpp"], cwd=project, check=True)
            subprocess.run(
                ["git", "-c", "user.name=jojo-test", "-c", "user.email=jojo@example.com", "commit", "-qm", "base"],
                cwd=project,
                check=True,
            )
            # 模拟未被编辑工具 matcher 捕获的外部脚本写入
            source.write_bytes(b"int main() { return 0; }\r\n")
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(ROOT)
            result = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps(
                    {
                        "cwd": str(project),
                        "hook_event_name": "Stop",
                        "stop_hook_active": False,
                        "last_assistant_message": "ORIGINAL_AUDIT_ANSWER",
                    }
                ).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertTrue(payload["continue"])
        self.assertEqual(payload["decision"], "block")
        self.assertIn("PURE_TEXT_REWRITE", payload["reason"])
        self.assertIn("已有修改不等于本轮污染", payload["reason"])
        self.assertIn("不得自动恢复、覆盖或删除来源不明", payload["reason"])
        self.assertIn("ORIGINAL_AUDIT_ANSWER", payload["reason"])
        self.assertIn("不得只回复守护修复结果", payload["reason"])
        self.assertNotIn("hookSpecificOutput", payload)

    def test_preexisting_batch_problem_does_not_block_read_only_turn(self) -> None:
        """回合开始前已有的批处理问题只能反馈为警告，不能阻断只读答案。"""
        with tempfile.TemporaryDirectory(prefix="jojo preexisting stop ") as directory:
            project = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=project, check=True)
            (project / ".gitattributes").write_text(
                "* -text\n*.bat text eol=crlf\n", encoding="utf-8"
            )
            (project / "legacy.bat").write_bytes(b"@echo off\necho legacy\n")
            subprocess.run(["git", "add", "."], cwd=project, check=True)
            subprocess.run(
                [
                    "git", "-c", "user.name=jojo-test", "-c", "user.email=jojo@example.com",
                    "commit", "-qm", "base",
                ],
                cwd=project,
                check=True,
            )
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(ROOT)
            environment["JOJO_CODE_GUARD_BASELINE_DIR"] = str(project / ".git" / "jojo-baselines")
            common_input = {
                "cwd": str(project),
                "session_id": "session-preexisting",
                "turn_id": "turn-read-only",
            }
            baseline = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps({**common_input, "hook_event_name": "UserPromptSubmit"}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            baseline_files = list((project / ".git" / "jojo-baselines").glob("*.json"))
            self.assertEqual(len(baseline_files), 1, baseline.stderr.decode("utf-8", errors="replace"))
            post = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps({**common_input, "hook_event_name": "PostToolUse"}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            stop = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps(
                    {
                        **common_input,
                        "hook_event_name": "Stop",
                        "stop_hook_active": False,
                        "last_assistant_message": "READ_ONLY_AUDIT_ANSWER",
                    }
                ).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(baseline.returncode, 0, baseline.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(baseline.stdout, b"")
        self.assertEqual(post.returncode, 0, post.stderr.decode("utf-8", errors="replace"))
        post_payload = json.loads(post.stdout.decode("utf-8"))
        self.assertNotIn("decision", post_payload)
        context = post_payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn('"origin": "pre_existing"', context)
        self.assertIn('"introduced_by_current_turn": false', context)
        self.assertEqual(stop.returncode, 0, stop.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(stop.stdout, b"")

    def test_changed_preexisting_problem_remains_blocking(self) -> None:
        """本轮改动历史问题文件但仍保留问题时，文件指纹变化必须继续阻断。"""
        with tempfile.TemporaryDirectory(prefix="jojo worsened stop ") as directory:
            project = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=project, check=True)
            (project / ".gitattributes").write_text(
                "* -text\n*.bat text eol=crlf\n", encoding="utf-8"
            )
            script = project / "legacy.bat"
            script.write_bytes(b"@echo off\necho legacy\n")
            subprocess.run(["git", "add", "."], cwd=project, check=True)
            subprocess.run(
                [
                    "git", "-c", "user.name=jojo-test", "-c", "user.email=jojo@example.com",
                    "commit", "-qm", "base",
                ],
                cwd=project,
                check=True,
            )
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(ROOT)
            environment["JOJO_CODE_GUARD_BASELINE_DIR"] = str(project / ".git" / "jojo-baselines")
            common_input = {
                "cwd": str(project),
                "session_id": "session-worsened",
                "turn_id": "turn-edit",
            }
            baseline = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps({**common_input, "hook_event_name": "UserPromptSubmit"}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(baseline.returncode, 0, baseline.stderr.decode("utf-8", errors="replace"))
            script.write_bytes(b"@echo off\necho changed this turn\n")
            stop = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps(
                    {
                        **common_input,
                        "hook_event_name": "Stop",
                        "stop_hook_active": False,
                        "last_assistant_message": "EDIT_RESULT",
                    }
                ).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(stop.returncode, 0, stop.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(stop.stdout.decode("utf-8"))
        self.assertEqual(payload["decision"], "block")
        self.assertIn("BATCH_EOL", payload["reason"])
        self.assertNotIn('"origin": "pre_existing"', payload["reason"])

    def test_stop_check_reentry_is_silent(self) -> None:
        """Stop 已经要求继续一次后必须静默放行，避免形成无限循环。"""
        environment = os.environ.copy()
        environment["PLUGIN_ROOT"] = str(ROOT / "missing-plugin")
        result = subprocess.run(
            [BASH, str(ROOT / "hooks" / "post-write-check")],
            cwd=ROOT,
            env=environment,
            input=b'{"hook_event_name": "Stop", "stop_hook_active": true}',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_stop_check_clean_repo_is_silent(self) -> None:
        """回合结束时没有诊断不输出内容，不向模型添加上下文。"""
        with tempfile.TemporaryDirectory(prefix="jojo clean stop ") as directory:
            project = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=project, check=True)
            source = project / "example.cpp"
            source.write_bytes(b"int main() { return 0; }\n")
            subprocess.run(["git", "add", "example.cpp"], cwd=project, check=True)
            subprocess.run(
                ["git", "-c", "user.name=jojo-test", "-c", "user.email=jojo@example.com", "commit", "-qm", "base"],
                cwd=project,
                check=True,
            )
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(ROOT)
            result = subprocess.run(
                [BASH, str(ROOT / "hooks" / "post-write-check")],
                cwd=project,
                env=environment,
                input=json.dumps(
                    {"cwd": str(project), "hook_event_name": "Stop", "stop_hook_active": False}
                ).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(result.stdout, b"")

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

    def test_hook_manifest_covers_session_write_and_stop_events(self) -> None:
        """Codex/Claude 共用的标准 Hook 清单应覆盖完整生命周期。"""
        data = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
        groups = data["hooks"]

        self.assertIn("SessionStart", groups)
        self.assertIn("PostToolUse", groups)
        self.assertIn("Stop", groups)
        post_entry = groups["PostToolUse"][0]
        self.assertEqual(
            post_entry["matcher"],
            "apply_patch|Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell",
        )
        command = post_entry["hooks"][0]["command"]
        self.assertIn("${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}", command)
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
                [BASH, "-c", session_command],
                cwd=project,
                env=environment,
                input=json.dumps({"cwd": str(project)}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(session.returncode, 0, session.stderr.decode("utf-8", errors="replace"))
            session_payload = json.loads(session.stdout.decode("utf-8"))
            session_context = session_payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("<JOJO_CODE_GUARD_LOAD_INSTRUCTION>", session_context)
            self.assertNotIn("候选项目规则：", session_context)
            self.assertNotIn("AGENTS.md", session_context)
            self.assertNotIn("Codex 项目规则", session_context)
            self.assertLess(len(session_context.encode("utf-8")), 2500)

            post = subprocess.run(
                [BASH, "-c", post_command],
                cwd=ROOT,
                env=environment,
                input=json.dumps({"cwd": str(project)}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(post.returncode, 0, post.stderr.decode("utf-8", errors="replace"))
            payload = json.loads(post.stdout.decode("utf-8"))
            self.assertTrue(payload["continue"])
            self.assertEqual(payload["decision"], "block")
            self.assertIn("PURE_TEXT_REWRITE", payload["hookSpecificOutput"]["additionalContext"])

    @unittest.skipUnless(os.name == "nt", "仅 Windows 需要验证 commandWindows")
    def test_codex_windows_commands_support_spaces_and_chinese(self) -> None:
        """Codex Windows 命令应在插件和项目路径含空格、中文时正确定位脚本。"""
        with tempfile.TemporaryDirectory(prefix="jojo windows ") as directory:
            root = Path(directory)
            project = root / "业务仓库"
            plugin_root = root / "插件 root"
            project.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=project, check=True)
            (project / "AGENTS.md").write_text("Windows 项目规则\n", encoding="utf-8")
            source = project / "example.cpp"
            source.write_bytes(b"int main() { return 0; }\n")
            subprocess.run(["git", "add", "."], cwd=project, check=True)
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
                cwd=project,
                check=True,
            )
            source.write_bytes(b"int main() { return 0; }\r\n")
            with mock.patch.dict(os.environ, {"JOJO_CODEX_PLUGIN_DIR": str(plugin_root)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    sync_codex_plugin.main()
            manifest = json.loads((plugin_root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
            session_command = manifest["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
            post_command = manifest["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"]
            stop_command = manifest["hooks"]["Stop"][0]["hooks"][0]["commandWindows"]
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(plugin_root)
            environment["CLAUDE_CODE_GIT_BASH_PATH"] = str(Path(BASH).resolve())
            bash_directory = os.path.normcase(str(Path(BASH).resolve().parent))
            environment["PATH"] = os.pathsep.join(
                entry
                for entry in environment.get("PATH", "").split(os.pathsep)
                if entry and os.path.normcase(str(Path(entry.strip('"')).resolve())) != bash_directory
            )
            for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
                environment[variable] = str(root / "missing-program-files")

            session = subprocess.run(
                "cmd.exe /D /S /C " + session_command,
                cwd=project,
                env=environment,
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(session.returncode, 0, session.stderr.decode("utf-8", errors="replace"))
            session_payload = json.loads(session.stdout.decode("utf-8"))
            session_context = session_payload["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("候选项目规则：", session_context)
            self.assertNotIn("AGENTS.md", session_context)
            self.assertNotIn("Windows 项目规则", session_context)
            self.assertLess(len(session_context.encode("utf-8")), 2500)

            post = subprocess.run(
                "cmd.exe /D /S /C " + post_command,
                cwd=project,
                env=environment,
                input=json.dumps({"cwd": str(project)}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(post.returncode, 0, post.stderr.decode("utf-8", errors="replace"))
            post_payload = json.loads(post.stdout.decode("utf-8"))
            self.assertEqual(post_payload["decision"], "block")
            self.assertIn("PURE_TEXT_REWRITE", post_payload["hookSpecificOutput"]["additionalContext"])

            stop = subprocess.run(
                "cmd.exe /D /S /C " + stop_command,
                cwd=project,
                env=environment,
                input=json.dumps(
                    {"cwd": str(project), "hook_event_name": "Stop", "stop_hook_active": False}
                ).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(stop.returncode, 0, stop.stderr.decode("utf-8", errors="replace"))
            stop_payload = json.loads(stop.stdout.decode("utf-8"))
            self.assertEqual(stop_payload["decision"], "block")
            self.assertIn("PURE_TEXT_REWRITE", stop_payload["reason"])

    def test_windows_launcher_searches_known_bash_locations_and_uses_own_root(self) -> None:
        """Windows 启动器应按安全顺序定位 Git Bash，并从自身路径重建插件根。"""
        launcher = (ROOT / "hooks" / "run-hook.cmd").read_text(encoding="utf-8")

        env_index = launcher.index("CLAUDE_CODE_GIT_BASH_PATH")
        program_files_index = launcher.index("%ProgramFiles%\\Git\\bin\\bash.exe")
        path_index = launcher.index("%%~$PATH:I")
        self.assertLess(env_index, program_files_index)
        self.assertLess(program_files_index, path_index)
        self.assertIn('if /I "%JOJO_HOOK_NAME%"=="session-start"', launcher)
        self.assertIn('if /I "%JOJO_HOOK_NAME%"=="post-write-check"', launcher)
        self.assertIn('for %%I in ("%~dp0..") do set "PLUGIN_ROOT=%%~fI"', launcher)
        self.assertIn('set "CLAUDE_PLUGIN_ROOT=%PLUGIN_ROOT%"', launcher)

    def test_ci_checks_committed_range_and_tracked_head(self) -> None:
        """CI 不得只检查干净工作树，应检查事件提交范围及 HEAD 跟踪内容。"""
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn('git diff --check "$diff_base" HEAD', workflow)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("github.event.before", workflow)
        self.assertIn("--tracked-revision HEAD", workflow)

    def test_codex_marketplace_uses_local_source_schema(self) -> None:
        """Codex marketplace 应使用当前 CLI 可安装的本地源格式。"""
        data = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        source = data["plugins"][0]["source"]

        self.assertEqual(source, {"source": "local", "path": "./"})

    def test_release_repository_attributes_checkout_batch_as_crlf(self) -> None:
        """发布仓库应统一普通文本为 LF，并把批处理检出为 CRLF。"""
        lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()

        self.assertIn("* text=auto eol=lf", lines)
        self.assertIn("*.bat   text eol=crlf", lines)
        self.assertIn("*.cmd   text eol=crlf", lines)
        self.assertIn("hooks/session-start text eol=lf", lines)
        self.assertIn("hooks/post-write-check text eol=lf", lines)

        launcher = (ROOT / "hooks" / "run-hook.cmd").read_bytes()
        self.assertFalse(launcher.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(launcher.endswith(b"\r\n"))
        self.assertNotIn(b"\n", launcher.replace(b"\r\n", b""))

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

    def test_session_start_references_rules_without_inlining_large_content(self) -> None:
        """SessionStart 应给出绝对规则路径，不内联可能溢出的规则正文。"""
        with tempfile.TemporaryDirectory(prefix="jojo project ") as directory:
            project = Path(directory)
            plugin_root = project / "插件 root"
            rules = "PROJECT_RULE_SENTINEL\n" + ("大" * 20000)
            (project / "AGENTS.md").write_text(rules, encoding="utf-8")
            with mock.patch.dict(os.environ, {"JOJO_CLAUDE_PLUGIN_DIR": str(plugin_root)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    sync_claude_plugin.main()
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
            environment["CLAUDE_PROJECT_DIR"] = str(project)
            manifest = json.loads((plugin_root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
            command = manifest["hooks"]["SessionStart"][0]["hooks"][0]["command"]

            result = subprocess.run(
                [BASH, "-c", command],
                cwd=str(project),
                env=environment,
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertTrue(context.startswith("<JOJO_CODE_GUARD_LOAD_INSTRUCTION>"))
            self.assertIn("必需路由 Skill：", context)
            self.assertIn("skills/jojo-code-guard/SKILL.md", context.replace("\\", "/"))
            self.assertIn(
                "skills/jojo-code-guard/references/通用行为规则.md",
                context.replace("\\", "/"),
            )
            self.assertNotIn("通用规则", context)
            self.assertIn("候选项目规则：", context)
            self.assertIn("AGENTS.md", context)
            self.assertNotIn("PROJECT_RULE_SENTINEL", context)
            self.assertNotIn("name: jojo-code-guard", context)
            references = [
                line.split("：", 1)[1]
                for line in context.splitlines()
                if line.startswith(("1. ", "2. ", "3. "))
            ]
            self.assertEqual(len(references), 3)
            for reference in references:
                if os.name == "nt":
                    self.assertRegex(reference, r"^[A-Za-z]:/")
                else:
                    self.assertTrue(reference.startswith("/"), reference)
            self.assertLess(len(context.encode("utf-8")), 2500)
            self.assertLess(len(context), 10000)
            self.assertTrue(context.endswith("</JOJO_CODE_GUARD_LOAD_INSTRUCTION>"))

    def test_session_start_without_explicit_project_does_not_bind_cwd_rules(self) -> None:
        """纯会话不能把客户端碰巧提供的当前目录误认为用户项目。"""
        with tempfile.TemporaryDirectory(prefix="jojo chat cwd ") as directory:
            root = Path(directory)
            project = root / "incidental-repo"
            plugin_root = root / "plugin"
            project.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
            (project / "AGENTS.md").write_text("INCIDENTAL_PROJECT_RULE\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"JOJO_CLAUDE_PLUGIN_DIR": str(plugin_root)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    sync_claude_plugin.main()
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
            environment.pop("CLAUDE_PROJECT_DIR", None)

            result = subprocess.run(
                [BASH, str(plugin_root / "hooks" / "session-start")],
                cwd=project,
                env=environment,
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            payload = json.loads(result.stdout.decode("utf-8"))
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("skills/jojo-code-guard/SKILL.md", context.replace("\\", "/"))
            self.assertNotIn("候选项目规则：", context)
            self.assertNotIn("AGENTS.md", context)
            self.assertNotIn("INCIDENTAL_PROJECT_RULE", context)

    def test_session_start_reports_missing_skill_to_model(self) -> None:
        """Skill 资源缺失时应向模型注入暂停要求，不能静默继续。"""
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["CLAUDE_PLUGIN_ROOT"] = directory

            script_path = str(ROOT / "hooks" / "session-start").replace("\\", "/")

            result = subprocess.run(
                [
                    BASH,
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
            payload = json.loads(result.stdout.decode("utf-8"))
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("JOJO_CODE_GUARD_LOAD_FAILED", context)
            self.assertIn("暂停当前任务", context)
            self.assertIn("只报告加载失败", context)
            self.assertIn("无法读取 Skill", result.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

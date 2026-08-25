# 规则语义与入口回归测试：防止渐进拆分再次缩小通用规则的适用范围。

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "jojo-code-guard"
REFERENCES = SKILL_ROOT / "references"


def _read(path: Path) -> str:
    """按发布编码读取规则资源。"""
    return path.read_text(encoding="utf-8")


def _markdown_h2_section(text: str, heading: str) -> str:
    """提取一个二级标题正文，便于把语义断言限制在对应规则段。"""
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"缺少规则节：{heading}")
    return match.group("body")


def _collapse_whitespace(text: str) -> str:
    """折叠 Markdown 排版空白，只比较规则节中的连续语义。"""
    return re.sub(r"\s+", " ", text).strip()


def _bash_executable() -> str | None:
    """优先使用 PATH，并兼容 Windows 的标准 Git for Windows 安装位置。"""
    executable = shutil.which("bash")
    if executable or os.name != "nt":
        return executable
    candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
    return str(candidate) if candidate.is_file() else None


class RuleSemanticTests(unittest.TestCase):
    """验证拆分后的规则仍保留跨任务语义，专项文件不重复通用基线。"""

    def test_mandatory_common_rule_keeps_implementation_and_comment_semantics(self) -> None:
        """所有任务基线必须保留角色、实现与跨语言注释语义。"""
        common = _read(REFERENCES / "通用行为规则.md")

        self.assertRegex(common, r"资深[^\n]*全栈(?:工程师|开发)")
        self.assertRegex(common, r"不省略关键实现(?:逻辑)?")
        self.assertIn("不随意偏离项目结构", common)
        self.assertRegex(common, r"变量的短注释[^\n]*(?:同行|同一行)")
        self.assertIn("`//`", common)
        self.assertIn("`///`", common)
        self.assertRegex(common, r"普通行注释[^\n]*`//`")
        self.assertRegex(common, r"`///`[^\n]*只用于[^\n]*文档注释")

    def test_common_rule_keeps_cost_scope_and_stop_loss_semantics(self) -> None:
        """高成本约束、辅助问题门槛和止损规则不能在压缩时再次丢失。"""
        common = _read(REFERENCES / "通用行为规则.md")
        decisions = _collapse_whitespace(_markdown_h2_section(common, "优先级与工程决策"))
        scope = _collapse_whitespace(_markdown_h2_section(common, "范围控制与复盘"))

        for anchor in ("具体规则", "困难", "常规做法", "可行替代方案", "推荐理由"):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, decisions)
        self.assertRegex(decisions, r"未经用户确认.*?不得主动放弃")
        self.assertRegex(scope, r"只有缺失后.*?(?:无法运行|结果不可信|严重安全|不可恢复).*?才能阻断")
        self.assertRegex(scope, r"30 分钟.*?停止当前方向")
        self.assertRegex(scope, r"低风险最小替代方案.*?继续主体任务")
        self.assertRegex(scope, r"长任务.*?复盘目标、进度、风险和实现方向")

    def test_common_rule_keeps_security_failure_and_document_workflow_semantics(self) -> None:
        """风险相称安全、乱码降级、失败说明和文档工作流必须保持为所有任务基线。"""
        common = _read(REFERENCES / "通用行为规则.md")
        security = _collapse_whitespace(_markdown_h2_section(common, "鲁棒性、安全与授权"))
        artifacts = _collapse_whitespace(_markdown_h2_section(common, "代码、注释与文档"))
        delivery = _collapse_whitespace(_markdown_h2_section(common, "验证与沟通"))

        self.assertRegex(security, r"输入、权限和数据风险相称.*?基础安全")
        self.assertRegex(artifacts, r"脚本输出.*?中文乱码.*?英文输出")
        self.assertRegex(artifacts, r"文档名称使用中文.*?工作流")
        self.assertRegex(artifacts, r"原子化.*?便于 Review")
        self.assertRegex(delivery, r"失败时说明原因和可行的下一步")

    def test_router_distinguishes_mandatory_baseline_from_optional_specialists(self) -> None:
        """主入口或通用基线缺失必须停整个任务，专项缺失才只停受影响操作。"""
        router = _read(SKILL_ROOT / "SKILL.md")
        auto_load = _read(REFERENCES / "自动加载规则.md")

        self.assertRegex(router, r"主 Skill[^\n]*通用行为规则[^\n]*暂停当前任务")
        self.assertRegex(router, r"命中[^\n]*专项资源[^\n]*暂停受影响的操作")
        self.assertRegex(auto_load, r"主 Skill[^\n]*通用行为规则[^\n]*暂停当前任务")
        self.assertRegex(auto_load, r"按需专项规则[^\n]*暂停受影响的操作")

    def test_cpp_comment_rule_keeps_specialist_elaboration(self) -> None:
        """C/C++ 专项可细化通用的行注释与文档注释约束。"""
        cpp = _read(REFERENCES / "C++专项规则.md")

        self.assertIn("`//`", cpp)
        self.assertIn("`///`", cpp)
        self.assertRegex(cpp, r"`///`[^\n]*(?:文档注释|项目约定)")

    def test_file_guard_contains_diagnostic_and_write_specific_sections(self) -> None:
        """文件守护必须提供只读诊断，同时不重复纯行为基线。"""
        router = _read(SKILL_ROOT / "SKILL.md")
        file_guard = _read(REFERENCES / "通用文件守护.md")
        usage = _read(REFERENCES / "usage.md")
        readme = _read(ROOT / "README.md")
        headings = set(re.findall(r"^## (.+)$", file_guard, flags=re.MULTILINE))

        self.assertNotIn("通用实现边界", headings)
        self.assertNotIn("通用代码与注释", headings)
        self.assertNotIn("安全、验证与交付", headings)
        self.assertTrue(
            {"只读诊断基线", "修改前基线", "写入规则", "写入后闭环", "Git 与编辑器风险"}
            <= headings
        )
        self.assertRegex(file_guard, r"只读诊断[^\n]*(?:编码|字符编码)[^\n]*BOM[^\n]*(?:EOL|换行)[^\n]*diff")
        self.assertRegex(file_guard, r"只读诊断基线[\s\S]*不授权任何写入")
        self.assertRegex(usage, r"只读诊断[^\n]*(?:编码|字符编码)[^\n]*BOM[^\n]*(?:EOL|换行)[^\n]*diff")
        self.assertRegex(readme, r"只读诊断[^\n]*(?:编码|字符编码)[^\n]*BOM[^\n]*(?:EOL|换行)[^\n]*diff")
        self.assertRegex(
            router,
            re.compile(
                r"^\|[^\n]*只读诊断[^\n]*编码[^\n]*BOM[^\n]*换行[^\n]*异常 diff[^\n]*\|"
                r"[^\n]*\[通用文件守护\]\(references/通用文件守护\.md\)"
                r"[^\n]*\[使用与工具说明\]\(references/usage\.md\)[^\n]*\|$",
                re.MULTILINE,
            ),
        )

    def test_powershell_encoding_and_stop_process_guidance_is_consistent(self) -> None:
        """PowerShell 速查、正文和清单必须遵循同一编码及终止语义。"""
        powershell = _read(SKILL_ROOT / "PowerShell规则.md")
        usage = _read(REFERENCES / "usage.md")
        readme = _read(ROOT / "README.md")
        encoding = _markdown_h2_section(powershell, "1. 文件编码")
        stop_process = _markdown_h2_section(
            powershell,
            "8. Stop-Process 行为 (Windows vs Unix)",
        )
        quick_reference = _markdown_h2_section(powershell, "速查表: 规则 × 平台/版本")

        self.assertRegex(encoding, r"已有文件[^\n]*编码[^\n]*BOM[^\n]*换行")
        self.assertRegex(
            encoding,
            r"新建 `\.ps1/\.psm1/\.psd1`[^\n]*默认[^\n]*UTF-8 无 BOM[\s\S]*?"
            r"Windows PowerShell 5\.1[^\n]*(?:非 ASCII|非ASCII)[\s\S]*?UTF-8 BOM",
        )
        self.assertRegex(encoding, r"Unix shebang `\.ps1`[^\n]*禁止 BOM")
        self.assertNotIn("仍建议保留以兼容 PS 5.1", encoding)
        self.assertRegex(
            stop_process,
            r"(?m)^\| Unix \| 7 \| `\.NET Process\.Kill` \| 强制终止[^\n]*\|$",
        )
        self.assertNotIn("SIGTERM", stop_process)
        self.assertNotIn("优雅退出", stop_process)
        self.assertRegex(
            quick_reference,
            r"(?m)^\| 8 \| Stop-Process \|[^\n]*Process\.Kill[^\n]*强制终止[^\n]*\|$",
        )
        self.assertNotRegex(powershell, re.compile(r"\$pid\b", re.IGNORECASE))
        self.assertIn("$targetProcessId", powershell)
        self.assertNotIn("新增 `.ps1` 默认使用 UTF-8 无 BOM", usage)
        self.assertRegex(usage, r"PS 5\.1[^\n]*(?:非 ASCII|非ASCII)[^\n]*BOM")
        self.assertNotIn("新增 `.ps1` 默认采用 UTF-8 无 BOM", readme)
        self.assertRegex(readme, r"PS 5\.1[^\n]*(?:非 ASCII|非ASCII)[^\n]*BOM")

    def test_openai_agent_prompt_loads_the_all_task_common_baseline(self) -> None:
        """主 Agent 的默认提示不能把所有任务通用基线降为按需资源。"""
        agent = _read(SKILL_ROOT / "agents" / "openai.yaml")

        self.assertRegex(agent, r"所有任务[^\n]*通用(?:行为)?基线")

    def test_usage_keeps_partial_staging_fail_closed_semantics(self) -> None:
        """同步构建失败时不能把不确定所有权的 partial staging 静默清理。"""
        usage = _collapse_whitespace(_read(REFERENCES / "usage.md"))

        self.assertRegex(
            usage,
            r"无 marker 的 partial staging.*?不做递归清理.*?保留并报告精确路径",
        )

    def test_long_task_output_control_is_progressively_routed(self) -> None:
        """大量日志控制必须按需加载，且不得以节省上下文为由弱化验证。"""
        router = _read(SKILL_ROOT / "SKILL.md")
        guidance_path = REFERENCES / "长任务输出控制.md"

        self.assertTrue(guidance_path.is_file())
        guidance = _collapse_whitespace(_read(guidance_path))

        self.assertRegex(
            router,
            r"长时间构建、测试、反复诊断[^\n]*"
            r"\[长任务输出控制\]\(references/长任务输出控制\.md\)",
        )
        for anchor in (
            "200 行",
            "16 KiB",
            "操作系统临时",
            "真实退出码",
            "密钥、令牌或用户数据",
            "前 20 个",
            "连续三次同类尝试",
            "不得成为跳过编译、测试、警告审查或必要验证的理由",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, guidance)


class RuleEntrypointTests(unittest.TestCase):
    """验证 SessionStart 和 Claude 命令都会引导读取完整的强制基线。"""

    def _run_hook(self, *, include_common: bool) -> tuple[int, str, str]:
        """在隔离插件根目录运行真实 SessionStart Hook。"""
        bash = _bash_executable()
        if bash is None:
            self.skipTest("当前环境没有可用的 bash")
        with tempfile.TemporaryDirectory() as directory:
            plugin_root = Path(directory)
            skill_root = plugin_root / "skills" / "jojo-code-guard"
            (skill_root / "references").mkdir(parents=True)
            (skill_root / "SKILL.md").write_bytes(b"# Test Skill\n")
            if include_common:
                (skill_root / "references" / "通用行为规则.md").write_bytes(
                    "# 通用行为规则\n".encode("utf-8")
                )
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(plugin_root)
            environment.pop("CLAUDE_PROJECT_DIR", None)
            result = subprocess.run(
                [bash, str(ROOT / "hooks" / "session-start")],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
        output = result.stdout.strip()
        try:
            payload = json.loads(output)
            context = payload["hookSpecificOutput"]["additionalContext"]
        except (json.JSONDecodeError, KeyError, TypeError):
            context = output
        return result.returncode, context, result.stderr

    def test_session_start_emits_main_skill_and_mandatory_common_rule(self) -> None:
        """正常入口必须把主 Skill 与所有任务基线作为两个显式必读资源。"""
        returncode, context, stderr = self._run_hook(include_common=True)

        self.assertEqual(returncode, 0, stderr)
        self.assertIn("skills/jojo-code-guard/SKILL.md", context.replace("\\", "/"))
        self.assertIn("skills/jojo-code-guard/references/通用行为规则.md", context.replace("\\", "/"))
        self.assertRegex(context, r"所有任务.*(?:必须|必需).*(?:读取|加载)")

    def test_session_start_blocks_whole_task_when_common_rule_is_missing(self) -> None:
        """强制通用基线缺失时不能只阻断文件或仓库操作。"""
        returncode, context, stderr = self._run_hook(include_common=False)

        self.assertEqual(returncode, 0, stderr)
        self.assertIn("JOJO_CODE_GUARD_LOAD_FAILED", context)
        self.assertIn("通用行为规则.md", context)
        self.assertIn("暂停当前任务", context)

    def test_claude_commands_explicitly_bootstrap_main_and_common_rules(self) -> None:
        """从任一公开命令进入时都必须先加载主路由与强制通用基线。"""
        for name in ("doctor.md", "check-diff.md", "help.md"):
            with self.subTest(command=name):
                command = _read(ROOT / "commands" / name)
                self.assertIn("skills/jojo-code-guard/SKILL.md", command)
                self.assertIn("skills/jojo-code-guard/references/通用行为规则.md", command)
                self.assertRegex(command, r"(?:先|首先)[^\n]*(?:完整读取|加载)")

    def test_doctor_command_does_not_promise_a_mutable_elevated_payload(self) -> None:
        """安装说明必须与安全实现一致：doctor 不生成临时提权脚本。"""
        command = _read(ROOT / "commands" / "doctor.md")

        self.assertNotIn("生成临时 PowerShell 安装脚本", command)
        self.assertRegex(command, r"包管理器[^\n]*绝对路径")
        self.assertRegex(command, r"安装器[^\n]*UAC")


if __name__ == "__main__":
    unittest.main()

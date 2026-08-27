from __future__ import annotations

import hashlib
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "jojo-code-guard"


class PreservedNaturalLanguageTests(unittest.TestCase):
    """防止优化 Hook 时顺手压缩未授权的专项规则正文。"""

    EXPECTED_HASHES = {
        "PowerShell规则.md": "a915ddd7ac368303a6d97bec8ef7b896e139514a37f5aa948d994ab721e4090e",
        "references/通用行为规则.md": "7c57deb8b0d3a10434eeaa400997b8290985de9448aae090e7017a8854db424c",
        "references/长任务输出控制.md": "d0b11c6534eded9805df02acd34c354b19f5fe78fca95ab1e75ce5d8f463fd4b",
        # 只将“所有任务必读”改为原生项目规则，其余 C++ 语义保持不变。
        "references/C++专项规则.md": "d535ab030e4f091f9f8d8eb330b01c84a48a81eeeb832875e4390534bd60092b",
        "references/Git操作规则.md": "a42256a13ddc2fb5409ec4caac8a8cda03f8ec2765cc6edcd94833c450add01e",
    }

    def test_unrelated_rule_files_remain_byte_identical(self) -> None:
        for relative, expected in self.EXPECTED_HASHES.items():
            with self.subTest(relative=relative):
                actual = hashlib.sha256((SKILL_ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)


class LeanSkillSemanticsTests(unittest.TestCase):
    def test_upgrade_guide_covers_upgrade_migration_and_behavioral_checks(self) -> None:
        content = (ROOT / "升级后验证指南.md").read_text(encoding="utf-8")
        for required in (
            "codex plugin marketplace upgrade jojo-code-guard",
            "codex plugin add jojo-code-guard@jojo-code-guard",
            "0.2.16",
            "SessionStart",
            "UserPromptSubmit",
            "tests.test_claude_adapter",
            "check_diff.py --repo . --tracked-revision HEAD",
            "新会话行为烟测",
        ):
            self.assertIn(required, content)

    def test_main_skill_is_file_specific_and_has_no_all_task_bootstrap(self) -> None:
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = content.split("---", 2)[1]
        self.assertIn("encoding", frontmatter)
        self.assertIn("do not use for pure chat", frontmatter)
        for obsolete in ("暗号检测", "所有任务基线", "每次会话加载", "通用行为规则.md"):
            self.assertNotIn(obsolete, content)
        self.assertIn("通用文件守护", content)
        self.assertIn("已有未提交修改不等于污染", content)

    def test_native_discovery_rule_rejects_repeated_session_injection(self) -> None:
        content = (SKILL_ROOT / "references" / "自动加载规则.md").read_text(encoding="utf-8")
        self.assertIn("原生发现", content)
        self.assertIn("不再要求", content)
        self.assertIn("SessionStart", content)
        self.assertIn("source: compact", content)
        self.assertIn("用户级旧版 jojo Hook 应移除", content)

    def test_file_guard_describes_pre_post_and_stateful_stop(self) -> None:
        content = (SKILL_ROOT / "references" / "通用文件守护.md").read_text(encoding="utf-8")
        self.assertIn("`PreToolUse`", content)
        self.assertIn("工作区真实变化后才", content)
        self.assertIn("纯聊天", content)
        self.assertIn("不复制完整工具输出或待交付答案", content)
        self.assertIn("上下文压缩", content)
        self.assertNotIn("`UserPromptSubmit` 记录回合基线", content)

    def test_usage_keeps_core_migration_and_encoding_contracts(self) -> None:
        content = (SKILL_ROOT / "references" / "usage.md").read_text(encoding="utf-8")
        for required in (
            "已有文件保持编辑前编码、BOM、换行和末尾换行",
            "--allow-initial-baseline",
            "--allow-migration KIND:PATH",
            "JOJO_CODE_GUARD_ALLOW_MIGRATIONS",
            "git add --renormalize",
            "pre-commit",
            "Python 3",
        ):
            self.assertIn(required, content)
        for obsolete in ("用户级自动加载节同步", "ownership marker", "天王盖地虎"):
            self.assertNotIn(obsolete, content)

    def test_public_subskills_load_only_relevant_main_and_usage(self) -> None:
        for name in ("jojo-code-guard-doctor", "jojo-code-guard-check-diff"):
            content = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("../jojo-code-guard/SKILL.md", content)
            self.assertIn("usage.md", content)
            self.assertNotIn("通用行为规则.md", content)

    def test_hook_sources_cannot_request_skill_reload_or_copy_final_answer(self) -> None:
        combined = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "hooks").glob("*"))
        for forbidden in (
            "JOJO_CODE_GUARD_LOAD_INSTRUCTION",
            "SKILL.md",
            "通用行为规则.md",
            "last_assistant_message",
            "JOJO_PENDING_FINAL_RESPONSE",
        ):
            self.assertNotIn(forbidden, combined)

    def test_openai_prompt_does_not_bootstrap_all_task_rules(self) -> None:
        content = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("$jojo-code-guard", content)
        self.assertIn("编码、BOM、换行", content)
        self.assertNotIn("所有任务通用行为基线", content)


if __name__ == "__main__":
    unittest.main()

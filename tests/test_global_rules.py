# 全局规则同步回归测试：验证自动加载节的增改、保真和写入保护。

from __future__ import annotations

import contextlib
import errno
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


# 测试直接复用 doctor 中的自动加载节同步实现
ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "jojo-code-guard" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import doctor  # noqa: E402


SOURCE_TEXT = (
    "## jojo-code-guard 自动加载（必须严格遵守）\n"
    "\n"
    "- 加载当前 Skill。\n"
    "- 应用当前通用规则。\n"
)


class GlobalRuleSyncTests(unittest.TestCase):
    """验证两个用户级全局文件只增改 jojo-code-guard 自动加载节。"""

    def _paths(self, directory: str) -> tuple[Path, list[Path]]:
        """创建隔离的节源路径和两个固定目标路径。"""
        root = Path(directory)
        source = root / "自动加载规则.md"
        source.write_bytes(SOURCE_TEXT.encode("utf-8"))
        targets = [root / ".claude" / "CLAUDE.md", root / ".codex" / "AGENTS.md"]
        return source, targets

    def _sync(self, source: Path, targets: list[Path]) -> list[str]:
        """在隔离路径中运行自动加载节同步。"""
        with mock.patch.object(doctor, "_global_rule_section_source_path", return_value=source):
            with mock.patch.object(doctor, "_global_rule_target_paths", return_value=targets):
                return doctor._sync_global_rules()

    def _check(
        self,
        source: Path,
        targets: list[Path],
        *,
        preview: bool,
    ) -> list[doctor.Finding]:
        """在隔离路径中运行自动加载节预览。"""
        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor, "_global_rule_section_source_path", return_value=source):
            with mock.patch.object(doctor, "_global_rule_target_paths", return_value=targets):
                doctor._check_global_rules(findings, preview=preview)
        return findings

    def test_missing_targets_create_plain_title_and_section(self) -> None:
        """目标不存在时应创建普通标题和当前自动加载节。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)

            changed = self._sync(source, targets)

            expected = ("# 全局规则\n\n" + SOURCE_TEXT).encode("utf-8")
            self.assertEqual(set(changed), {str(path) for path in targets})
            self.assertTrue(all(target.read_bytes() == expected for target in targets))

    def test_macos_system_aliases_are_distinguished_from_user_links(self) -> None:
        """只有 macOS 固定根目录别名可穿过，/var 下的用户链接仍不可信。"""
        with mock.patch.object(doctor.sys, "platform", "darwin"):
            self.assertTrue(doctor._is_macos_system_path_alias(Path("/var")))
            self.assertTrue(doctor._is_macos_system_path_alias(Path("/tmp")))
            self.assertTrue(doctor._is_macos_system_path_alias(Path("/etc")))
            self.assertFalse(doctor._is_macos_system_path_alias(Path("/var/folders")))

    def test_no_replace_capability_is_probed_before_any_target_write(self) -> None:
        """目标卷不支持 no-clobber rename 时，两个缺失目标都必须保持不存在。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            calls = 0

            def failing_second_probe(source_path: Path, destination_path: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("simulated unsupported no-replace rename")
                os.replace(source_path, destination_path)

            with mock.patch.object(
                doctor,
                "_move_global_rule_file_no_replace",
                new=failing_second_probe,
            ):
                with self.assertRaisesRegex(RuntimeError, "unsupported|失败"):
                    self._sync(source, targets)

            self.assertGreaterEqual(calls, 2)
            self.assertFalse(any(target.exists() for target in targets))

    def test_existing_content_without_title_is_preserved_when_appending(self) -> None:
        """无标题的已有文件应原样保留正文并只追加自动加载节。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = "用户自己的规则\nkeep: true"
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original + "\n\n"))
                self.assertNotIn("# 全局规则\n", text)
                self.assertEqual(text.count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_existing_empty_file_does_not_gain_title(self) -> None:
        """已存在的空文件也不得被补写全局标题。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(b"")

            self._sync(source, targets)

            self.assertTrue(all(target.read_bytes() == SOURCE_TEXT.encode("utf-8") for target in targets))

    def test_existing_bom_crlf_and_custom_title_are_preserved(self) -> None:
        """追加节时应保留已有标题、BOM、CRLF 和自定义正文。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = b"\xef\xbb\xbf# My Rules\r\n\r\nkeep: true\r\n"
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original)

            self._sync(source, targets)

            for target in targets:
                data = target.read_bytes()
                self.assertTrue(data.startswith(original))
                self.assertIn(doctor.GLOBAL_RULE_SECTION_HEADING.encode("utf-8"), data)
                self.assertNotIn(b"\n", data[3:].replace(b"\r\n", b""))

    def test_legacy_section_is_replaced_without_touching_neighbors(self) -> None:
        """旧标题所在整节应被更新，前后用户内容保持原样。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "# 用户标题\n\n"
                "前置规则\n\n"
                "## jojo-code-guard 自动加载\n\n"
                "- 旧内容\n\n"
                "## 用户规则\n\n"
                "后置规则\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("# 用户标题\n\n前置规则\n\n"))
                self.assertTrue(text.endswith("## 用户规则\n\n后置规则\n"))
                self.assertNotIn("- 旧内容", text)
                self.assertEqual(text.count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_duplicate_jojo_sections_are_consolidated(self) -> None:
        """多个新旧自动加载节应合并到最靠前的位置。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载\n\n"
                "- 旧内容一\n\n"
                "## 用户规则\n\n"
                "keep: true\n\n"
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 旧内容二\n\n"
                "## 其他规则\n\n"
                "remain: true\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertEqual(len(doctor.GLOBAL_RULE_SECTION_PATTERN.findall(text)), 1)
                self.assertIn("## 用户规则\n\nkeep: true", text)
                self.assertIn("## 其他规则\n\nremain: true", text)
                self.assertNotIn("旧内容一", text)
                self.assertNotIn("旧内容二", text)

    def test_heading_inside_fenced_code_block_is_never_managed(self) -> None:
        """代码示例里的同名标题不是受管节，任何示例和后续正文都必须保留。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "# 用户规则\n\n"
                "```md\n"
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 这只是示例。\n"
                "```\n\n"
                "tail: preserve\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original))
                self.assertIn("- 这只是示例。\n```\n\ntail: preserve", text)
                self.assertEqual(len(doctor._global_rule_section_ranges(text)), 1)

    def test_invalid_backtick_fence_info_never_hides_user_heading(self) -> None:
        """反引号信息串非法时必须拒写，不能把后续用户标题吞进受管节。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 旧内容。\n\n"
                "```bad`info\n"
                "## 用户规则\n\n"
                "keep: true\n"
                "```\n"
            ).encode("utf-8")
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original)

            with self.assertRaisesRegex(RuntimeError, "反引号"):
                self._sync(source, targets)

            self.assertTrue(all(target.read_bytes() == original for target in targets))

    def test_tab_indented_fence_never_hides_user_heading(self) -> None:
        """Tab 缩进的是代码块而非围栏，不能遮蔽后续顶层用户标题。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 旧内容。\n\n"
                "\t```md\n"
                "## 用户规则\n\n"
                "KEEP_ME\n"
                "\t```\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("- 旧内容。", text)
                self.assertIn("## 用户规则\n\nKEEP_ME\n\t```\n", text)

    def test_non_ascii_fence_suffix_never_exposes_managed_heading(self) -> None:
        """NBSP 等非空格后缀不能关闭围栏并暴露代码示例中的受管标题。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "```md\n"
                "example\n"
                "```\u00a0\n"
                "## jojo-code-guard 自动加载（必须严格遵守）\n"
                "USER_OWNED\n"
                "```\n"
                "```\n"
            ).encode("utf-8")
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original)

            with self.assertRaisesRegex(RuntimeError, "fenced code block"):
                self._sync(source, targets)

            self.assertTrue(all(target.read_bytes() == original for target in targets))

    def test_unclosed_inline_html_comment_never_hides_user_heading(self) -> None:
        """块内未闭合的行内注释必须拒写，不能把状态带到后续用户标题。"""
        for prefix in ("### 说明 <!--", "普通段落 <!--"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                original = (
                    "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                    "- 旧内容。\n\n"
                    f"{prefix}\n"
                    "## 用户规则\n\n"
                    "KEEP_ME\n"
                    "-->\n"
                ).encode("utf-8")
                for target in targets:
                    target.parent.mkdir(parents=True)
                    target.write_bytes(original)

                with self.assertRaisesRegex(RuntimeError, "行内 HTML 注释"):
                    self._sync(source, targets)

                self.assertTrue(all(target.read_bytes() == original for target in targets))

    def test_multiline_html_tag_opener_never_exposes_managed_heading(self) -> None:
        """行首未闭合 HTML tag 必须拒写，不能接管其块内形似受管节的 H2。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "<div class=\"rules\"\n"
                "## jojo-code-guard 自动加载（必须严格遵守）\n"
                "USER_OWNED\n\n"
            ).encode("utf-8")
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original)

            with self.assertRaisesRegex(RuntimeError, "HTML"):
                self._sync(source, targets)

            self.assertTrue(all(target.read_bytes() == original for target in targets))

    def test_heading_inside_html_comment_is_never_managed(self) -> None:
        """HTML 注释里的同名标题不是受管节，注释和后续正文必须原样保留。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "# 用户规则\n\n"
                "<!--\n"
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 这只是注释示例。\n"
                "-->\n\n"
                "tail: preserve\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original))
                self.assertIn("- 这只是注释示例。\n-->\n\ntail: preserve", text)
                self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_setext_heading_terminates_managed_section(self) -> None:
        """Setext 用户标题也是节边界，旧受管节不得吞掉它及其正文。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 旧内容\n\n"
                "User Rules\n"
                "----------\n"
                "KEEP_ME\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("- 旧内容", text)
                self.assertIn("User Rules\n----------\nKEEP_ME\n", text)
                self.assertEqual(text.count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_thematic_breaks_do_not_terminate_managed_section(self) -> None:
        """连续 thematic break 不是 Setext 标题，不能让旧受管正文残留。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 旧内容。\n\n"
                "***\n"
                "---\n"
                "SHOULD_BE_REPLACED\n\n"
                "## 用户规则\n\n"
                "KEEP_ME\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("- 旧内容。", text)
                self.assertNotIn("SHOULD_BE_REPLACED", text)
                self.assertIn("## 用户规则\n\nKEEP_ME\n", text)

    def test_link_reference_definition_never_becomes_setext_boundary(self) -> None:
        """链接引用定义的多行语法含糊时必须拒写，不能误切受管节。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 旧内容。\n\n"
                "[reference]: https://example.invalid\n"
                "---\n"
                "SHOULD_BE_REPLACED\n\n"
                "## 用户规则\n\n"
                "KEEP_ME\n"
            ).encode("utf-8")
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original)

            with self.assertRaisesRegex(RuntimeError, "链接引用定义"):
                self._sync(source, targets)

            self.assertTrue(all(target.read_bytes() == original for target in targets))

    def test_unambiguous_link_reference_definition_is_preserved(self) -> None:
        """与标题边界无关的链接引用定义不应阻断安全的节追加。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "# 用户规则\n\n"
                "[reference]: https://example.invalid\n\n"
                "KEEP_ME\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original))
                self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_unicode_separators_cannot_manufacture_managed_heading(self) -> None:
        """Markdown 只按 CR/LF 分行，Unicode 分隔符不能凭空制造受管 H2。"""
        for separator in ("\u2028", "\x0b"):
            with self.subTest(separator=repr(separator)), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                original = (
                    f"USER_PREFIX{separator}"
                    "## jojo-code-guard 自动加载（必须严格遵守）\n"
                    "USER_OWNED\n"
                )
                for target in targets:
                    target.parent.mkdir(parents=True)
                    target.write_bytes(original.encode("utf-8"))

                self._sync(source, targets)

                for target in targets:
                    text = target.read_text(encoding="utf-8")
                    self.assertTrue(text.startswith(original))
                    self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_unicode_spaces_do_not_turn_user_heading_into_managed_heading(self) -> None:
        """ATX 可剥离尾随空白仅含空格和 Tab，Unicode 空白属于用户标题正文。"""
        for unicode_space in ("\u00a0", "\u2007", "\u202f"):
            with self.subTest(space=repr(unicode_space)), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                original = (
                    f"## jojo-code-guard 自动加载（必须严格遵守）{unicode_space}\n"
                    "USER_OWNED\n"
                )
                for target in targets:
                    target.parent.mkdir(parents=True)
                    target.write_bytes(original.encode("utf-8"))

                self._sync(source, targets)

                for target in targets:
                    text = target.read_text(encoding="utf-8")
                    self.assertTrue(text.startswith(original))
                    self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_unicode_space_can_start_user_setext_heading(self) -> None:
        """Unicode 空白不是 CommonMark 缩进，不能让 Setext 用户边界被受管节吞掉。"""
        for unicode_space in ("\u00a0", "\u2007", "\u202f"):
            with self.subTest(space=repr(unicode_space)), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                original = (
                    "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                    "- 旧内容\n\n"
                    f"{unicode_space}User Rules\n"
                    "----------\n"
                    "KEEP_ME\n"
                )
                for target in targets:
                    target.parent.mkdir(parents=True)
                    target.write_bytes(original.encode("utf-8"))

                self._sync(source, targets)

                for target in targets:
                    text = target.read_text(encoding="utf-8")
                    self.assertNotIn("- 旧内容", text)
                    self.assertIn(f"{unicode_space}User Rules\n----------\nKEEP_ME\n", text)

    def test_vertical_tab_or_form_feed_alone_cannot_start_setext_heading(self) -> None:
        """CommonMark non-whitespace 不含 VT/FF，单独一行不能制造 Setext 用户边界。"""
        for whitespace in ("\x0b", "\x0c"):
            with self.subTest(whitespace=repr(whitespace)), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                original = (
                    "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                    "- 旧内容\n\n"
                    f"{whitespace}\n"
                    "----------\n"
                    "SHOULD_BE_REPLACED\n"
                )
                for target in targets:
                    target.parent.mkdir(parents=True)
                    target.write_bytes(original.encode("utf-8"))

                self._sync(source, targets)

                for target in targets:
                    text = target.read_text(encoding="utf-8")
                    self.assertNotIn("SHOULD_BE_REPLACED", text)

    def test_matching_setext_heading_is_user_owned(self) -> None:
        """同名 Setext H2 只作为边界，不属于 doctor 承诺管理的 ATX H2。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "jojo-code-guard 自动加载（必须严格遵守）\n"
                "------------------------------------------\n"
                "USER_OWNED\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original))
                self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_h1_with_managed_title_is_user_owned(self) -> None:
        """同名 H1 不是 doctor 管理的 H2，必须连同其正文原样保留。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "# jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "USER_OWNED\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original))
                self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_inline_html_comment_does_not_hide_user_heading_boundary(self) -> None:
        """标题中的已闭合行内注释可被忽略，但标题本身仍须终止受管节。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 旧内容\n\n"
                "# User <!-- note -->\n\n"
                "KEEP_ME\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("- 旧内容", text)
                self.assertIn("# User <!-- note -->\n\nKEEP_ME\n", text)
                self.assertEqual(text.count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_comment_after_hashes_cannot_manufacture_managed_heading(self) -> None:
        """原始 closing hashes 后还有 HTML 注释时，hashes 属于用户标题正文。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守） ### <!-- note -->\n"
                "USER_OWNED\n"
                "## Keep\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original))
                self.assertIn("USER_OWNED", text)
                self.assertEqual(text.count(doctor.GLOBAL_RULE_SECTION_HEADING), 2)

    def test_html_comments_cannot_manufacture_a_managed_atx_prefix(self) -> None:
        """删除注释后看似 ATX 的原始普通文本不得被 doctor 接管。"""
        originals = (
            "<!-- example --> ## jojo-code-guard 自动加载（必须严格遵守）\nUSER_OWNED\n",
            "#<!-- split --># jojo-code-guard 自动加载（必须严格遵守）\nUSER_OWNED\n",
        )
        for original in originals:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                for target in targets:
                    target.parent.mkdir(parents=True)
                    target.write_bytes(original.encode("utf-8"))

                self._sync(source, targets)

                for target in targets:
                    text = target.read_text(encoding="utf-8")
                    self.assertTrue(text.startswith(original))
                    self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_line_start_html_comment_cannot_manufacture_setext_boundary(self) -> None:
        """行首 HTML 注释的同行尾随文本不得变成 Setext 标题并留下旧规则。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "OLD\n\n"
                "<!-- closed -->User Rules\n"
                "---\n"
                "STALE_MANAGED\n\n"
                "## Keep\n"
                "KEEP_ME\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("STALE_MANAGED", text)
                self.assertNotIn("<!-- closed -->User Rules", text)
                self.assertIn("## Keep\nKEEP_ME\n", text)

    def test_indented_code_cannot_become_a_managed_setext_section(self) -> None:
        """四空格代码即使后接横线，也不得被解释成受管标题并删除。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "    jojo-code-guard 自动加载（必须严格遵守）\n"
                "---\n"
                "USER_OWNED\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original))
                self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_atx_subheading_followed_by_rule_stays_inside_managed_section(self) -> None:
        """受管节内 H3 后的横线不是 H2 边界，旧子节内容必须一并替换。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "### Managed subsection\n"
                "---\n"
                "STALE_MANAGED\n\n"
                "## User Rules\n"
                "KEEP_ME\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("STALE_MANAGED", text)
                self.assertIn("## User Rules\nKEEP_ME\n", text)
                self.assertEqual(text.count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_lazy_setext_after_container_blocks_all_writes(self) -> None:
        """列表或引用块的 lazy continuation 不得冒充顶层 Setext 边界。"""
        ambiguous = (
            (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- old list item\n"
                "lazy continuation\n"
                "---\n"
                "STALE_MANAGED\n"
            ),
            (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "> old quote\n"
                "lazy continuation\n"
                "---\n"
                "STALE_MANAGED\n"
            ),
        )
        for first_text in ambiguous:
            with self.subTest(first_text=first_text), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                originals = [first_text.encode("utf-8"), b"second: preserve\n"]
                for target, data in zip(targets, originals):
                    target.parent.mkdir(parents=True)
                    target.write_bytes(data)

                findings = self._check(source, targets, preview=True)
                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

                self.assertTrue(any(item.level == "BLOCKED" for item in findings))
                self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_bare_list_marker_does_not_create_setext_boundary(self) -> None:
        """裸列表标记后接横线仍属于旧受管节，不能让旧内容逃过替换。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "-\n"
                "---\n"
                "STALE_MANAGED\n\n"
                "## User Rules\n"
                "KEEP_ME\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("STALE_MANAGED", text)
                self.assertIn("## User Rules\nKEEP_ME\n", text)

    def test_ambiguous_indented_heading_after_list_blocks_all_writes(self) -> None:
        """列表层级无法可靠归属缩进标题时应整次拒写，不能猜测顶层边界。"""
        ambiguous = (
            (
                "- Example:\n\n"
                "  ## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "  USER_OWNED\n"
            ),
            (
                "- outer\n"
                "  - nested\n\n"
                "  ## jojo-code-guard 自动加载（必须严格遵守）\n"
                "  USER_OWNED\n"
            ),
            (
                "-    item\n\n"
                "  ## jojo-code-guard 自动加载（必须严格遵守）\n"
                "  USER_OWNED\n"
            ),
            (
                "- item\n\n"
                "  User Rules\n"
                "  ----------\n"
                "  USER_OWNED\n"
            ),
        )
        for first_text in ambiguous:
            with self.subTest(first_text=first_text), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                originals = [first_text.encode("utf-8"), b"second: preserve\n"]
                for target, data in zip(targets, originals):
                    target.parent.mkdir(parents=True)
                    target.write_bytes(data)

                findings = self._check(source, targets, preview=True)
                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

                self.assertTrue(any(item.level == "BLOCKED" for item in findings))
                self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_raw_html_block_blocks_sync_without_writing(self) -> None:
        """原始 HTML 块语义含糊时应拒写，不能接管块内的示例标题。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            raw_blocks = (
                (
                    "<pre>\n"
                    "## jojo-code-guard 自动加载（必须严格遵守）\n"
                    "USER_OWNED\n"
                    "</pre>\n"
                ),
                (
                    "<pre\nclass=x>\n"
                    "## jojo-code-guard 自动加载（必须严格遵守）\n"
                    "USER_OWNED\n"
                    "    </pre>\n"
                ),
                (
                    "<script attr\n>\n"
                    "## jojo-code-guard 自动加载（必须严格遵守）\n"
                    "USER_OWNED\n"
                ),
            )
            for first_text in raw_blocks:
                with self.subTest(first_text=first_text):
                    originals = [first_text.encode("utf-8"), b"second: preserve\n"]
                    for target, data in zip(targets, originals):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)

                    findings = self._check(source, targets, preview=True)
                    with self.assertRaises(RuntimeError):
                        self._sync(source, targets)

                    self.assertTrue(any(item.level == "BLOCKED" for item in findings))
                    self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_indented_managed_heading_is_updated_in_place(self) -> None:
        """Markdown 允许的 1 至 3 个前导空格不得导致旧节漏判并重复追加。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "# 用户规则\n\n"
                "  ## jojo-code-guard 自动加载（必须严格遵守）\n\n"
                "- 旧内容\n\n"
                "## 用户自有规则\n\n"
                "keep: true\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("- 旧内容", text)
                self.assertEqual(text.count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)
                self.assertIn("## 用户自有规则\n\nkeep: true", text)

    def test_adjacent_hashes_are_part_of_user_heading_text(self) -> None:
        """未由空白分隔的尾随 # 属于标题正文，不得误当 closing sequence 后接管。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = (
                "## jojo-code-guard 自动加载（必须严格遵守）###\n"
                "USER OWNED\n"
                "## 用户规则\n"
                "keep: true\n"
            )
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original.encode("utf-8"))

            self._sync(source, targets)

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(original))
                self.assertEqual(text[len(original):].count(doctor.GLOBAL_RULE_SECTION_HEADING), 1)

    def test_unclosed_fenced_code_block_blocks_both_targets(self) -> None:
        """无法确定 fenced code block 边界时应拒写，避免把受管节追加到代码块中。"""
        for opening in ("```md\n", "~~~~markdown\n"):
            with self.subTest(opening=opening), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                originals = [
                    ("# 用户规则\n\n" + opening + "## 示例标题\n").encode("utf-8"),
                    b"second: preserve\n",
                ]
                for target, data in zip(targets, originals):
                    target.parent.mkdir(parents=True)
                    target.write_bytes(data)

                findings = self._check(source, targets, preview=True)
                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

                self.assertTrue(any(item.level == "BLOCKED" for item in findings))
                self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_current_section_is_idempotent(self) -> None:
        """当前自动加载节已存在时不得改写任何字节。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            current = ("用户规则\n\n" + SOURCE_TEXT).encode("utf-8")
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(current)

            changed = self._sync(source, targets)

            self.assertEqual(changed, [])
            self.assertTrue(all(target.read_bytes() == current for target in targets))

    def test_mixed_eol_preflight_prevents_partial_write(self) -> None:
        """任一目标使用混合换行时，两个目标都不得发生部分写入。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first\n", b"second\r\nline\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)

            findings = self._check(source, targets, preview=True)
            with self.assertRaises(RuntimeError):
                self._sync(source, targets)

            self.assertTrue(any(item.level == "BLOCKED" for item in findings))
            self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_preview_shows_proposed_section_change_without_writing(self) -> None:
        """预览应展示当前文件到拟议节级更新的差异且保持只读。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            original = b"keep: true\n"
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original)

            findings = self._check(source, targets, preview=True)

            warnings = [item for item in findings if item.level == "WARNING"]
            self.assertEqual(len(warnings), 2)
            self.assertTrue(all("（拟议）" in item.message for item in warnings))
            self.assertTrue(all(target.read_bytes() == original for target in targets))

    def test_yaml_front_matter_blocks_sync_without_writing(self) -> None:
        """front matter 内的形似 H2 不是文档级受管节，两个目标都必须保持原字节。"""
        cases = (
            (
                "---\n"
                "template: |\n"
                "  ## jojo-code-guard 自动加载（必须严格遵守）\n"
                "  USER_OWNED\n"
                "---\n# User\nKEEP\n"
            ),
            (
                "---\n"
                "## jojo-code-guard 自动加载\n"
                "custom: keep\n"
                "---\n# User\nKEEP\n"
            ),
        )
        for text in cases:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                original = text.encode("utf-8")
                for target in targets:
                    target.parent.mkdir(parents=True)
                    target.write_bytes(original)

                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

                self.assertTrue(all(target.read_bytes() == original for target in targets))

    def test_invalid_source_with_title_is_rejected(self) -> None:
        """内置源若含全局标题或其他节，应在写入前阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            source.write_bytes(("# 全局规则\n\n" + SOURCE_TEXT).encode("utf-8"))

            with self.assertRaises(RuntimeError):
                self._sync(source, targets)

            self.assertTrue(all(not target.exists() for target in targets))

    def test_source_with_setext_h1_or_h2_is_rejected(self) -> None:
        """内置源中的 Setext H1/H2 也是额外顶级节，必须在任何写入前阻断。"""
        for underline in ("=====", "-----"):
            with self.subTest(underline=underline), tempfile.TemporaryDirectory() as directory:
                source, targets = self._paths(directory)
                source.write_bytes(
                    (SOURCE_TEXT + f"\nUnexpected Section\n{underline}\nDO_NOT_COPY\n").encode("utf-8")
                )

                with self.assertRaisesRegex(RuntimeError, "一级或二级标题"):
                    self._sync(source, targets)

                self.assertTrue(all(not target.exists() for target in targets))

    def test_write_failure_rolls_back_first_target(self) -> None:
        """第二个目标写入失败时应恢复第一个目标。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first\n", b"second\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)

            original_write = doctor._write_global_rule_file
            failed = False

            def guarded_write(
                path: Path,
                data: bytes,
                expected: doctor._GlobalRuleSnapshot,
                *,
                mode: int | None = None,
            ) -> doctor._GlobalRuleSnapshot:
                """仅让第二个目标的首次新内容写入失败。"""
                nonlocal failed
                if path == targets[1] and data != originals[1] and not failed:
                    failed = True
                    raise OSError("simulated failure")
                return original_write(path, data, expected, mode=mode)

            with mock.patch.object(doctor, "_write_global_rule_file", new=guarded_write):
                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

            self.assertTrue(failed)
            self.assertEqual([target.read_bytes() for target in targets], originals)

    @unittest.skipUnless(sys.platform == "win32", "Windows junction 专项回归测试")
    def test_parent_junction_is_not_followed_on_windows(self) -> None:
        """旧版 Python 无 Path.is_junction 时仍须阻断 junction。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            root = Path(directory)
            real_parent = root / "junction-real"
            junction_parent = root / "junction-home"
            real_parent.mkdir()
            result = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(junction_parent), str(real_parent)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest("当前环境无法创建 Windows junction")
            targets[0] = junction_parent / "CLAUDE.md"

            try:
                with mock.patch.object(Path, "is_junction", None, create=True):
                    with self.assertRaises(RuntimeError):
                        self._sync(source, targets)

                self.assertFalse((real_parent / "CLAUDE.md").exists())
            finally:
                os.rmdir(junction_parent)

    def test_non_junction_windows_reparse_point_is_blocked(self) -> None:
        """新版 Path.is_junction 返回 false 时，其他 reparse point 仍不能被当作普通路径。"""
        candidate = mock.Mock()
        candidate.is_junction.return_value = False
        fake_stat = mock.Mock(st_file_attributes=0x400)
        with (
            mock.patch.object(doctor.os, "name", "nt"),
            mock.patch.object(doctor.os, "lstat", return_value=fake_stat),
        ):
            self.assertTrue(doctor._global_rule_path_is_junction(candidate))

    def test_hard_link_target_never_changes_the_other_link(self) -> None:
        """目标若是硬链接，同步不得连带改写另一个路径的用户文件。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            outside = Path(directory) / "outside-user-rules.md"
            original = b"outside: preserve\n"
            outside.write_bytes(original)
            targets[0].parent.mkdir(parents=True)
            try:
                os.link(outside, targets[0])
            except OSError as error:
                self.skipTest(f"当前文件系统无法创建硬链接：{error}")
            targets[1].parent.mkdir(parents=True)
            targets[1].write_bytes(b"second: preserve\n")

            second_original = targets[1].read_bytes()
            with self.assertRaises(RuntimeError):
                self._sync(source, targets)

            self.assertEqual(outside.read_bytes(), original)
            self.assertEqual(targets[0].read_bytes(), original)
            self.assertEqual(targets[1].read_bytes(), second_original)

    @unittest.skipUnless(sys.platform == "win32", "Windows ADS 专项回归测试")
    def test_windows_alternate_data_stream_is_preserved(self) -> None:
        """原子替换不能静默删除 NTFS alternate data stream。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(b"user: preserve\n")
            stream_path = Path(str(targets[0]) + ":jojo-audit")
            try:
                stream_path.write_bytes(b"alternate-user-data")
            except OSError as error:
                self.skipTest(f"当前文件系统不支持 ADS：{error}")

            self._sync(source, targets)

            self.assertEqual(stream_path.read_bytes(), b"alternate-user-data")

    @unittest.skipUnless(sys.platform == "win32", "Windows ACL 专项回归测试")
    def test_windows_custom_acl_is_preserved(self) -> None:
        """受保护 DACL 不能在替换后退化为临时文件继承的目录 ACL。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(b"user: preserve\n")
            result = subprocess.run(
                ["icacls.exe", str(targets[0]), "/inheritance:d"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest("当前环境无法设置自定义 DACL")

            before = subprocess.run(
                ["icacls.exe", str(targets[0])],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout.replace(str(targets[0]).encode(), b"<target>")

            self._sync(source, targets)

            after = subprocess.run(
                ["icacls.exe", str(targets[0])],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout.replace(str(targets[0]).encode(), b"<target>")
            self.assertEqual(after, before)

    @unittest.skipUnless(hasattr(os, "setxattr"), "当前平台没有 xattr API")
    def test_posix_xattrs_are_preserved(self) -> None:
        """POSIX 扩展属性属于用户元数据，替换时必须保留。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(b"user: preserve\n")
            try:
                os.setxattr(targets[0], b"user.jojo_audit", b"preserve")
            except OSError as error:
                self.skipTest(f"当前文件系统不支持 user xattr：{error}")

            self._sync(source, targets)

            self.assertEqual(os.getxattr(targets[0], b"user.jojo_audit"), b"preserve")

    @unittest.skipUnless(sys.platform == "darwin", "仅 macOS 提供扩展 ACL 与 st_flags")
    def test_darwin_acl_and_file_flags_are_preserved(self) -> None:
        """macOS 原子交换必须保留扩展 ACL 与非阻断文件 flags。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(b"user: preserve\n")
            result = subprocess.run(
                ["chmod", "+a", "everyone allow read", str(targets[0])],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest("当前 macOS 文件系统无法设置测试 ACL")
            flag = getattr(stat, "UF_NODUMP", 0)
            if not flag or not hasattr(os, "chflags"):
                self.skipTest("当前 Python/文件系统不支持 UF_NODUMP")
            os.chflags(targets[0], os.lstat(targets[0]).st_flags | flag)
            acl_before = doctor._darwin_global_rule_acl(targets[0])
            flags_before = os.lstat(targets[0]).st_flags

            self._sync(source, targets)

            self.assertEqual(doctor._darwin_global_rule_acl(targets[0]), acl_before)
            self.assertEqual(os.lstat(targets[0]).st_flags, flags_before)

    def test_target_changed_after_preflight_is_not_overwritten(self) -> None:
        """两目标规划完成前出现的并发修改必须触发冲突，不能按旧快照覆盖。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
            concurrent = b"first: concurrent-user-edit\n"
            original_upsert = doctor._upsert_global_rule_section
            call_count = 0

            def mutating_upsert(*args: object, **kwargs: object) -> bytes:
                """第二个目标完成转换时模拟用户改动第一个目标。"""
                nonlocal call_count
                call_count += 1
                result = original_upsert(*args, **kwargs)
                if call_count == 2:
                    targets[0].write_bytes(concurrent)
                return result

            with mock.patch.object(doctor, "_upsert_global_rule_section", new=mutating_upsert):
                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

            self.assertEqual([target.read_bytes() for target in targets], [concurrent, originals[1]])

    def test_edit_in_final_replace_window_is_recovered_not_overwritten(self) -> None:
        """最后一次快照检查后的原子替换也必须捕获并恢复实际被置换的用户版本。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
            concurrent = b"first: final-window-user-edit\n"
            raced = False

            def racing_replace(target: Path, temporary: Path, backup: Path) -> None:
                """在提交原语内部模拟检查后、置换前的原子编辑器保存。"""
                nonlocal raced
                if target == targets[0] and not raced:
                    editor_save = target.with_name(target.name + ".editor-save")
                    editor_save.write_bytes(concurrent)
                    os.replace(editor_save, target)
                    raced = True
                os.replace(target, backup)
                os.replace(temporary, target)

            with mock.patch.object(
                doctor,
                "_replace_global_rule_file_with_backup",
                new=racing_replace,
                create=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "并发"):
                    self._sync(source, targets)

            self.assertTrue(raced)
            self.assertEqual(targets[0].read_bytes(), concurrent)
            self.assertEqual(targets[1].read_bytes(), originals[1])

    def test_second_edit_during_final_window_recovery_is_never_lost(self) -> None:
        """恢复第一次竞态版本时再次保存，最新版本也必须留在目标或明确的隔离文件。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
            first_edit = b"first: final-window-edit\n"
            second_edit = b"first: recovery-window-edit\n"
            real_replace = os.replace
            exchange_calls = 0
            first_backup: Path | None = None
            injected_second = False

            def racing_exchange(target: Path, temporary: Path, backup: Path) -> Path:
                """两次 exchange 前分别模拟编辑器的第一次和第二次原子保存。"""
                nonlocal exchange_calls, first_backup
                exchange_calls += 1
                editor_save = target.with_name(target.name + f".editor-{exchange_calls}")
                if exchange_calls == 1:
                    editor_save.write_bytes(first_edit)
                    real_replace(editor_save, target)
                    first_backup = backup
                else:
                    editor_save.write_bytes(second_edit)
                    real_replace(editor_save, target)
                real_replace(target, backup)
                real_replace(temporary, target)
                return backup

            def unsafe_restore(source_path: os.PathLike[str] | str, destination: os.PathLike[str] | str) -> None:
                """旧实现裸替换第一次备份前注入第二次保存，用于证明数据丢失窗口。"""
                nonlocal injected_second
                source_value = Path(source_path)
                destination_value = Path(destination)
                if (
                    not injected_second
                    and first_backup is not None
                    and source_value == first_backup
                    and destination_value == targets[0]
                ):
                    editor_save = targets[0].with_name(targets[0].name + ".editor-unsafe")
                    editor_save.write_bytes(second_edit)
                    real_replace(editor_save, targets[0])
                    injected_second = True
                real_replace(source_value, destination_value)

            with (
                mock.patch.object(
                    doctor,
                    "_replace_global_rule_file_with_backup",
                    new=racing_exchange,
                ),
                mock.patch.object(doctor.os, "replace", new=unsafe_restore),
            ):
                with self.assertRaisesRegex(RuntimeError, "并发|隔离"):
                    self._sync(source, targets)

            preserved_payloads = [
                path.read_bytes()
                for path in Path(directory).rglob("*")
                if path.is_file()
            ]
            self.assertIn(first_edit, preserved_payloads)
            self.assertIn(second_edit, preserved_payloads)
            self.assertEqual(targets[1].read_bytes(), originals[1])

    def test_partial_replace_failure_restores_missing_target(self) -> None:
        """ReplaceFileW 1177 类状态不得让原目标只留在随机 backup 路径。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)

            def partial_replace(target: Path, temporary: Path, backup: Path) -> Path:
                os.replace(target, backup)
                error = OSError(22, "simulated ERROR_UNABLE_TO_MOVE_REPLACEMENT_2")
                error.winerror = 1177  # type: ignore[attr-defined]
                raise error

            with mock.patch.object(
                doctor,
                "_replace_global_rule_file_with_backup",
                new=partial_replace,
            ):
                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

            self.assertEqual(targets[0].read_bytes(), originals[0])
            self.assertEqual(targets[1].read_bytes(), originals[1])

    def test_post_exchange_failure_preserves_displaced_concurrent_version(self) -> None:
        """POSIX swap 成功后的登记失败不得清理 temporary 中的实际用户版本。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
            concurrent = b"first: exchange-window-edit\n"

            def interrupted_after_exchange(target: Path, temporary: Path, backup: Path) -> Path:
                editor_save = target.with_name(target.name + ".editor-save")
                editor_save.write_bytes(concurrent)
                os.replace(editor_save, target)
                swap = target.with_name(target.name + ".swap")
                os.replace(target, swap)
                os.replace(temporary, target)
                os.replace(swap, temporary)
                raise PermissionError("simulated post-exchange registration failure")

            with mock.patch.object(
                doctor,
                "_replace_global_rule_file_with_backup",
                new=interrupted_after_exchange,
            ):
                with self.assertRaisesRegex(RuntimeError, "失败|并发"):
                    self._sync(source, targets)

            preserved_payloads = [
                path.read_bytes()
                for path in Path(directory).rglob("*")
                if path.is_file()
            ]
            self.assertIn(concurrent, preserved_payloads)
            self.assertEqual(targets[1].read_bytes(), originals[1])

    def test_post_exchange_verification_failure_restores_the_original(self) -> None:
        """交换已生效但提交身份尚未返回时，writer 必须自行恢复原版本。"""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "AGENTS.md"
            original_data = b"user: original\n"
            target.write_bytes(original_data)
            expected = doctor._global_rule_snapshot(target)
            real_snapshot = doctor._global_rule_snapshot
            real_exchange = doctor._replace_global_rule_file_with_backup
            failed_once = False
            published = False

            def exchange_then_arm(
                path: Path,
                replacement: Path,
                backup: Path,
            ) -> Path:
                nonlocal published
                result = real_exchange(path, replacement, backup)
                published = True
                return result

            def fail_first_backup_snapshot(path: Path) -> doctor._GlobalRuleSnapshot:
                """在交换成功后的首次 backup 复核处注入一次读取失败。"""
                nonlocal failed_once
                if (
                    published
                    and not failed_once
                    and path != target
                    and path.name.startswith(f".{target.name}.jojo-")
                    and path.exists()
                ):
                    failed_once = True
                    raise OSError("simulated post-commit verification failure")
                return real_snapshot(path)

            with mock.patch.object(
                doctor,
                "_replace_global_rule_file_with_backup",
                side_effect=exchange_then_arm,
            ), mock.patch.object(
                doctor,
                "_global_rule_snapshot",
                side_effect=fail_first_backup_snapshot,
            ):
                with self.assertRaises(OSError):
                    doctor._write_global_rule_file(target, b"doctor: intended\n", expected)

            self.assertTrue(failed_once)
            self.assertEqual(target.read_bytes(), original_data)
            self.assertEqual(list(Path(directory).glob(".AGENTS.md.jojo-*")), [])

    def test_new_target_equal_content_atomic_save_is_not_claimed(self) -> None:
        """首次发布后同字节外部 atomic-save 也不能被认作本轮提交。"""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "AGENTS.md"
            original = doctor._global_rule_snapshot(target)
            intended = b"doctor: intended\n"
            real_install = doctor._install_new_global_rule_file
            external: list[doctor._GlobalRuleSnapshot] = []

            def install_then_editor_save(path: Path, temporary: Path) -> None:
                real_install(path, temporary)
                editor = path.with_name(path.name + ".editor-save")
                editor.write_bytes(intended)
                os.replace(editor, path)
                external.append(doctor._global_rule_snapshot(path))

            with mock.patch.object(
                doctor,
                "_install_new_global_rule_file",
                new=install_then_editor_save,
            ):
                with self.assertRaisesRegex(RuntimeError, "并发|身份|替换"):
                    doctor._write_global_rule_file(target, intended, original)

            self.assertEqual(len(external), 1)
            self.assertEqual(doctor._global_rule_snapshot(target), external[0])

    @unittest.skipUnless(os.name == "nt", "Windows DACL 并发专项回归测试")
    def test_dacl_change_before_current_snapshot_is_not_overwritten(self) -> None:
        """ReplaceFileW 后、发布快照前出现的 ACL 内容修改也必须阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "AGENTS.md"
            original_data = b"user: original\n"
            intended = b"doctor: intended\n"
            target.write_bytes(original_data)
            expected = doctor._global_rule_snapshot(target)
            self.assertIsNotNone(expected.metadata)
            self.assertIsNotNone(expected.metadata.windows_dacl)
            descriptor = expected.metadata.windows_dacl or b""
            dacl_offset = int.from_bytes(descriptor[16:20], "little")
            ace_count = int.from_bytes(
                descriptor[dacl_offset + 4:dacl_offset + 6],
                "little",
            ) if dacl_offset else 0
            if not dacl_offset or not ace_count:
                self.skipTest("当前目标 DACL 没有可用于注入访问掩码变化的 ACE")

            real_exchange = doctor._replace_global_rule_file_with_backup
            real_snapshot = doctor._global_rule_snapshot
            published = False
            injected = False

            def exchange_then_arm(
                path: Path,
                replacement: Path,
                backup: Path,
            ) -> Path | None:
                nonlocal published
                result = real_exchange(path, replacement, backup)
                published = True
                return result

            def inject_dacl_before_current_snapshot(path: Path) -> doctor._GlobalRuleSnapshot:
                nonlocal injected
                snapshot = real_snapshot(path)
                if published and path == target and snapshot.data == intended and not injected:
                    metadata = snapshot.metadata
                    self.assertIsNotNone(metadata)
                    dacl = bytearray(metadata.windows_dacl or b"")
                    offset = int.from_bytes(dacl[16:20], "little")
                    ace = offset + 8
                    dacl[ace + 4] ^= 0x01  # 修改首个 ACE 的访问掩码，而不是继承标志
                    snapshot = replace(
                        snapshot,
                        metadata=replace(metadata, windows_dacl=bytes(dacl)),
                    )
                    injected = True
                return snapshot

            with mock.patch.object(
                doctor,
                "_replace_global_rule_file_with_backup",
                side_effect=exchange_then_arm,
            ), mock.patch.object(
                doctor,
                "_global_rule_snapshot",
                side_effect=inject_dacl_before_current_snapshot,
            ), mock.patch.object(
                doctor,
                "_reapply_windows_global_rule_dacl_pinned",
            ) as reapply:
                with self.assertRaisesRegex(RuntimeError, "并发|DACL|变化"):
                    doctor._write_global_rule_file(target, intended, expected)

            self.assertTrue(injected)
            reapply.assert_not_called()
            self.assertEqual(target.read_bytes(), original_data)

    @unittest.skipUnless(os.name == "nt", "Windows DACL 并发专项回归测试")
    def test_concurrent_dacl_change_before_reapply_is_not_overwritten(self) -> None:
        """钉住句柄后出现的外部 DACL 修改不能被旧描述符覆盖。"""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "AGENTS.md"
            original_data = b"user: original\n"
            target.write_bytes(original_data)
            expected = doctor._global_rule_snapshot(target)
            real_reapply = doctor._reapply_windows_global_rule_dacl_pinned
            changed = False

            def edit_acl_then_reapply(
                path: Path,
                descriptor: bytes,
                expected_object: doctor._GlobalRuleSnapshot,
            ) -> None:
                nonlocal changed
                metadata = expected_object.metadata
                self.assertIsNotNone(metadata)
                concurrent_metadata = replace(
                    metadata,
                    windows_dacl=(metadata.windows_dacl or b"") + b"concurrent",
                )
                concurrent_snapshot = replace(
                    expected_object,
                    metadata=concurrent_metadata,
                )
                changed = True
                with mock.patch.object(
                    doctor,
                    "_global_rule_snapshot",
                    return_value=concurrent_snapshot,
                ):
                    real_reapply(path, descriptor, expected_object)

            with mock.patch.object(
                doctor,
                "_reapply_windows_global_rule_dacl_pinned",
                new=edit_acl_then_reapply,
            ):
                with self.assertRaisesRegex(RuntimeError, "DACL|元数据|变化"):
                    doctor._write_global_rule_file(
                        target,
                        b"doctor: intended\n",
                        expected,
                    )

            self.assertTrue(changed)
            self.assertEqual(target.read_bytes(), original_data)
            self.assertEqual(
                doctor._windows_global_rule_dacl(target),
                expected.metadata.windows_dacl,
            )

    def test_verified_recovery_cleanup_never_deletes_a_racing_replacement(self) -> None:
        """恢复路径在删除窗口被替换时，外部对象必须继续存在。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / "recovery"
            external = root / "external"
            preserved = root / "preserved"
            recovery.write_bytes(b"expected\n")
            external.write_bytes(b"external\n")
            expected = doctor._global_rule_snapshot(recovery)
            real_move = doctor._move_global_rule_file_no_replace
            raced = False

            def racing_move(source: Path, destination: Path) -> None:
                """在 recovery 移入私有隔离目录前注入外部替换。"""
                nonlocal raced
                if source == recovery and not raced:
                    os.replace(recovery, preserved)
                    os.replace(external, recovery)
                    raced = True
                real_move(source, destination)

            with mock.patch.object(
                doctor,
                "_move_global_rule_file_no_replace",
                side_effect=racing_move,
            ):
                with self.assertRaises(RuntimeError):
                    doctor._unlink_verified_global_rule_recovery(recovery, expected)

            self.assertTrue(raced)
            payloads = [path.read_bytes() for path in root.iterdir() if path.is_file()]
            self.assertIn(b"expected\n", payloads)
            self.assertIn(b"external\n", payloads)

    def test_recovery_cleanup_failure_restores_the_visible_path(self) -> None:
        """私有隔离后的读取失败应优先把对象安全放回原恢复路径。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / "recovery"
            recovery.write_bytes(b"user-secret\n")
            expected = doctor._global_rule_snapshot(recovery)
            real_snapshot = doctor._global_rule_snapshot

            def deny_quarantine_snapshot(path: Path) -> doctor._GlobalRuleSnapshot:
                if path.name == "owned" and ".jojo-discard-" in path.parent.name:
                    raise PermissionError("simulated isolated snapshot denial")
                return real_snapshot(path)

            with mock.patch.object(
                doctor,
                "_global_rule_snapshot",
                side_effect=deny_quarantine_snapshot,
            ):
                with self.assertRaisesRegex(PermissionError, "snapshot denial"):
                    doctor._unlink_verified_global_rule_recovery(recovery, expected)

            self.assertEqual(recovery.read_bytes(), b"user-secret\n")
            self.assertEqual(list(root.glob(".recovery.jojo-discard-*")), [])

    def test_recovery_cleanup_reports_quarantine_when_restore_fails(self) -> None:
        """隔离对象无法放回时，异常必须给出仍可恢复的精确位置。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recovery = root / "recovery"
            recovery.write_bytes(b"user-secret\n")
            expected = doctor._global_rule_snapshot(recovery)
            real_snapshot = doctor._global_rule_snapshot
            real_move = doctor._move_global_rule_file_no_replace
            moves = 0

            def deny_quarantine_snapshot(path: Path) -> doctor._GlobalRuleSnapshot:
                if path.name == "owned" and ".jojo-discard-" in path.parent.name:
                    raise PermissionError("simulated isolated snapshot denial")
                return real_snapshot(path)

            def fail_restore(source: Path, destination: Path) -> None:
                nonlocal moves
                moves += 1
                if moves == 2:
                    raise PermissionError("simulated no-clobber restore denial")
                real_move(source, destination)

            with mock.patch.object(
                doctor,
                "_global_rule_snapshot",
                side_effect=deny_quarantine_snapshot,
            ), mock.patch.object(
                doctor,
                "_move_global_rule_file_no_replace",
                side_effect=fail_restore,
            ):
                with self.assertRaisesRegex(RuntimeError, "对象保留在：.*owned"):
                    doctor._unlink_verified_global_rule_recovery(recovery, expected)

            quarantined = list(root.glob(".recovery.jojo-discard-*/owned"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), b"user-secret\n")

    def test_old_windows_python_is_rejected_for_private_quarantine(self) -> None:
        """旧补丁级 Windows Python 不能创建具备私有 DACL 的 0700 隔离目录。"""
        with mock.patch.object(doctor.os, "name", "nt"), mock.patch.object(
            doctor.sys,
            "version_info",
            (3, 9, 19),
        ):
            with self.assertRaisesRegex(RuntimeError, r"3\.9\.20|DACL"):
                doctor._require_secure_private_directory_support()

        with mock.patch.object(doctor.os, "name", "nt"), mock.patch.object(
            doctor.sys,
            "version_info",
            (3, 9, 20),
        ):
            doctor._require_secure_private_directory_support()

    def test_private_directory_guard_fails_before_global_rule_writes(self) -> None:
        """私有目录能力不足时必须在任何全局规则写入或 backup 之前失败。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)

            with mock.patch.object(
                doctor,
                "_require_secure_private_directory_support",
                side_effect=RuntimeError("simulated vulnerable Windows Python"),
            ) as secure_guard, mock.patch.object(
                doctor,
                "_write_global_rule_file",
                wraps=doctor._write_global_rule_file,
            ) as writer:
                with self.assertRaisesRegex(RuntimeError, "vulnerable Windows Python"):
                    self._sync(source, targets)

            secure_guard.assert_called_once_with()
            writer.assert_not_called()
            self.assertEqual([target.read_bytes() for target in targets], originals)
            self.assertEqual(
                [path for target in targets for path in target.parent.glob(".*.jojo-*")],
                [],
            )

    def test_idempotent_sync_does_not_require_private_directory_support(self) -> None:
        """没有写入计划时无需因私有目录版本门槛阻断只读幂等检查。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            self._sync(source, targets)

            with mock.patch.object(
                doctor,
                "_require_secure_private_directory_support",
                side_effect=RuntimeError("must not be called"),
            ) as secure_guard:
                changed = self._sync(source, targets)

            self.assertEqual(changed, [])
            secure_guard.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "仅 Windows 的只读属性会阻止删除")
    def test_failed_replace_cleans_its_read_only_staging_file(self) -> None:
        """替换失败时也必须安全清理继承 READONLY 属性的自有暂存。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "AGENTS.md"
            target.write_bytes(b"user: original\n")
            os.chmod(target, stat.S_IREAD)
            try:
                expected = doctor._global_rule_snapshot(target)
                with mock.patch.object(
                    doctor,
                    "_replace_global_rule_file_with_backup",
                    side_effect=PermissionError("simulated replace failure"),
                ):
                    with self.assertRaises(PermissionError):
                        doctor._write_global_rule_file(target, b"doctor: intended\n", expected)

                self.assertEqual(target.read_bytes(), b"user: original\n")
                self.assertEqual(list(root.glob(".AGENTS.md.jojo-sync-*")), [])
            finally:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)

    def test_rollback_of_new_file_does_not_unlink_a_final_window_edit(self) -> None:
        """回滚本轮新建目标时也要原子捕获实际移走版本，不能检查后裸 unlink。"""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "AGENTS.md"
            original = doctor._global_rule_snapshot(target)
            intended = b"doctor-created\n"
            target.write_bytes(intended)
            committed = doctor._global_rule_snapshot(target)
            record = doctor._GlobalRuleWrite(target, original, intended, committed)
            concurrent = b"final-window-user-edit\n"
            raced = False

            def racing_move(source: Path, quarantine: Path) -> None:
                nonlocal raced
                if not raced:
                    editor_save = source.with_name(source.name + ".editor-save")
                    editor_save.write_bytes(concurrent)
                    os.replace(editor_save, source)
                    raced = True
                os.rename(source, quarantine)

            with mock.patch.object(
                doctor,
                "_move_global_rule_file_no_replace",
                new=racing_move,
                create=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "并发"):
                    doctor._restore_global_rule_write(record)

            self.assertTrue(raced)
            self.assertEqual(target.read_bytes(), concurrent)

    def test_uncommitted_record_never_claims_an_external_equal_content_save(self) -> None:
        """writer 未返回已提交身份时，相同字节的外部原子保存也不属于本轮事务。"""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "AGENTS.md"
            original_data = b"user: original\n"
            intended = b"doctor: intended\n"
            target.write_bytes(original_data)
            original = doctor._global_rule_snapshot(target)
            editor_save = target.with_name(target.name + ".editor-save")
            editor_save.write_bytes(intended)
            os.replace(editor_save, target)
            external = doctor._global_rule_snapshot(target)
            record = doctor._GlobalRuleWrite(target, original, intended)

            with self.assertRaisesRegex(RuntimeError, "无法确认|并发"):
                doctor._restore_global_rule_write(record)

            self.assertEqual(doctor._global_rule_snapshot(target), external)

    def test_already_restored_snapshot_ignores_only_rename_ctime(self) -> None:
        """POSIX 自恢复可忽略 rename 的 ctime，但 mtime 变化仍必须视为并发修改。"""
        identity = (1, 2, 8, 100, 200)
        original = doctor._GlobalRuleSnapshot(True, b"restored", identity, 0o600, None)
        ctime_only = replace(original, identity=(1, 2, 8, 100, 201))
        mtime_changed = replace(original, identity=(1, 2, 8, 101, 201))

        self.assertTrue(
            doctor._global_rule_snapshots_equivalent(
                ctime_only,
                original,
                require_same_object=True,
            )
        )
        self.assertFalse(
            doctor._global_rule_snapshots_equivalent(
                mtime_changed,
                original,
                require_same_object=True,
            )
        )
        record = doctor._GlobalRuleWrite(Path("unused"), original, b"intended")
        with mock.patch.object(doctor, "_global_rule_snapshot", return_value=ctime_only):
            doctor._restore_global_rule_write(record)

    def test_windows_dacl_comparison_ignores_representation_only_changes(self) -> None:
        """ReplaceFileW 后 DACL 的布局和默认继承标志变化不能误报并发写入。"""

        def descriptor(control: int, dacl_offset: int) -> bytes:
            payload = bytearray(dacl_offset + 8)
            payload[0] = 1
            payload[2:4] = control.to_bytes(2, "little")
            payload[16:20] = dacl_offset.to_bytes(4, "little")
            payload[dacl_offset] = 2
            payload[dacl_offset + 2:dacl_offset + 4] = (8).to_bytes(2, "little")
            return bytes(payload)

        base_dacl = descriptor(0x8004, 20)
        replacement_dacl = descriptor(0x850C, 24)
        metadata = doctor._GlobalRuleMetadata(
            windows_attributes=0,
            windows_dacl=base_dacl,
            alternate_streams=(),
            xattrs=(),
            uid=None,
            gid=None,
            file_flags=None,
            darwin_acl=None,
        )
        expected = doctor._GlobalRuleSnapshot(
            exists=True,
            data=b"doctor: intended\n",
            identity=(1, 2, 3, 4, 5),
            mode=0o600,
            metadata=metadata,
        )
        actual = replace(
            expected,
            metadata=replace(metadata, windows_dacl=replacement_dacl),
        )

        self.assertTrue(doctor._global_rule_same_object_except_windows_dacl(actual, expected))

    def test_windows_dacl_comparison_collapses_only_exact_repeated_ace_sequence(self) -> None:
        """ReplaceFileW 的整组重复 ACE 等价；任一重复项改写仍必须可见。"""

        def descriptor(masks: tuple[int, ...]) -> bytes:
            acl_size = 8 + 8 * len(masks)
            payload = bytearray(20 + acl_size)
            payload[0] = 1
            payload[2:4] = (0x8004).to_bytes(2, "little")
            payload[16:20] = (20).to_bytes(4, "little")
            payload[20] = 2
            payload[22:24] = acl_size.to_bytes(2, "little")
            payload[24:26] = len(masks).to_bytes(2, "little")
            for index, mask in enumerate(masks):
                offset = 28 + index * 8
                payload[offset + 2:offset + 4] = (8).to_bytes(2, "little")
                payload[offset + 4:offset + 8] = mask.to_bytes(4, "little")
            return bytes(payload)

        expected = descriptor((0x11, 0x22, 0x33))
        duplicated = descriptor((0x11, 0x22, 0x33, 0x11, 0x22, 0x33))
        altered = descriptor((0x11, 0x22, 0x33, 0x11, 0x22, 0x34))

        self.assertEqual(
            doctor._normalize_windows_global_rule_dacl_for_replace(duplicated),
            doctor._normalize_windows_global_rule_dacl_for_replace(expected),
        )
        self.assertNotEqual(
            doctor._normalize_windows_global_rule_dacl_for_replace(altered),
            doctor._normalize_windows_global_rule_dacl_for_replace(expected),
        )

    def test_windows_dacl_comparison_keeps_protection_bit_significant(self) -> None:
        """DACL 保护位影响后续继承语义，不能随表示差异一并忽略。"""

        def descriptor(control: int) -> bytes:
            payload = bytearray(28)
            payload[0] = 1
            payload[2:4] = control.to_bytes(2, "little")
            payload[16:20] = (20).to_bytes(4, "little")
            payload[20] = 2
            payload[22:24] = (8).to_bytes(2, "little")
            return bytes(payload)

        metadata = doctor._GlobalRuleMetadata(
            windows_attributes=0,
            windows_dacl=descriptor(0x8004),
            alternate_streams=(),
            xattrs=(),
            uid=None,
            gid=None,
            file_flags=None,
            darwin_acl=None,
        )
        expected = doctor._GlobalRuleSnapshot(
            exists=True,
            data=b"doctor: intended\n",
            identity=(1, 2, 3, 4, 5),
            mode=0o600,
            metadata=metadata,
        )
        actual = replace(
            expected,
            metadata=replace(metadata, windows_dacl=descriptor(0x9004)),
        )

        self.assertFalse(doctor._global_rule_same_object_except_windows_dacl(actual, expected))

    def test_windows_dacl_comparison_ignores_file_inheritance_propagation_flags(self) -> None:
        """ReplaceFileW 重算普通文件后代传播位时，不得误判为并发 ACL 改写。"""

        def descriptor(ace_flags: int) -> bytes:
            payload = bytearray(36)
            payload[0] = 1
            payload[2:4] = (0x8004).to_bytes(2, "little")
            payload[16:20] = (20).to_bytes(4, "little")
            payload[20] = 2
            payload[22:24] = (16).to_bytes(2, "little")
            payload[24:26] = (1).to_bytes(2, "little")
            payload[29] = ace_flags
            payload[30:32] = (8).to_bytes(2, "little")
            payload[32:36] = (0x11).to_bytes(4, "little")
            return bytes(payload)

        metadata = doctor._GlobalRuleMetadata(
            windows_attributes=0,
            windows_dacl=descriptor(0),
            alternate_streams=(),
            xattrs=(),
            uid=None,
            gid=None,
            file_flags=None,
            darwin_acl=None,
        )
        expected = doctor._GlobalRuleSnapshot(
            exists=True,
            data=b"doctor: intended\n",
            identity=(1, 2, 3, 4, 5),
            mode=0o600,
            metadata=metadata,
        )
        actual = replace(
            expected,
            metadata=replace(metadata, windows_dacl=descriptor(0x17)),
        )

        self.assertTrue(doctor._global_rule_same_object_except_windows_dacl(actual, expected))

    def test_windows_dacl_comparison_keeps_inherit_only_significant(self) -> None:
        """INHERIT_ONLY 会改变当前文件的有效权限，不能视为传播噪声。"""

        def descriptor(ace_flags: int) -> bytes:
            payload = bytearray(36)
            payload[0] = 1
            payload[2:4] = (0x8004).to_bytes(2, "little")
            payload[16:20] = (20).to_bytes(4, "little")
            payload[20] = 2
            payload[22:24] = (16).to_bytes(2, "little")
            payload[24:26] = (1).to_bytes(2, "little")
            payload[29] = ace_flags
            payload[30:32] = (8).to_bytes(2, "little")
            payload[32:36] = (0x11).to_bytes(4, "little")
            return bytes(payload)

        metadata = doctor._GlobalRuleMetadata(
            windows_attributes=0,
            windows_dacl=descriptor(0),
            alternate_streams=(),
            xattrs=(),
            uid=None,
            gid=None,
            file_flags=None,
            darwin_acl=None,
        )
        expected = doctor._GlobalRuleSnapshot(
            exists=True,
            data=b"doctor: intended\n",
            identity=(1, 2, 3, 4, 5),
            mode=0o600,
            metadata=metadata,
        )
        actual = replace(
            expected,
            metadata=replace(metadata, windows_dacl=descriptor(0x08)),
        )

        self.assertFalse(doctor._global_rule_same_object_except_windows_dacl(actual, expected))

    def test_windows_dacl_comparison_keeps_inherited_ace_mask_significant(self) -> None:
        """inherited ACE 的访问掩码仍会影响权限，不能被当作纯表示差异。"""

        def descriptor(explicit_mask: int, inherited_mask: int) -> bytes:
            payload = bytearray(44)
            payload[0] = 1
            payload[2:4] = (0x8004).to_bytes(2, "little")
            payload[16:20] = (20).to_bytes(4, "little")
            payload[20] = 2
            payload[22:24] = (24).to_bytes(2, "little")
            payload[24:26] = (2).to_bytes(2, "little")
            payload[30:32] = (8).to_bytes(2, "little")
            payload[32:36] = explicit_mask.to_bytes(4, "little")
            payload[37] = 0x10
            payload[38:40] = (8).to_bytes(2, "little")
            payload[40:44] = inherited_mask.to_bytes(4, "little")
            return bytes(payload)

        metadata = doctor._GlobalRuleMetadata(
            windows_attributes=0,
            windows_dacl=descriptor(0x11, 0x01),
            alternate_streams=(),
            xattrs=(),
            uid=None,
            gid=None,
            file_flags=None,
            darwin_acl=None,
        )
        expected = doctor._GlobalRuleSnapshot(
            exists=True,
            data=b"doctor: intended\n",
            identity=(1, 2, 3, 4, 5),
            mode=0o600,
            metadata=metadata,
        )
        actual = replace(
            expected,
            metadata=replace(metadata, windows_dacl=descriptor(0x11, 0x02)),
        )

        self.assertFalse(doctor._global_rule_same_object_except_windows_dacl(actual, expected))

    def test_windows_dacl_comparison_keeps_explicit_ace_significant(self) -> None:
        """显式 ACE 的访问掩码变化仍必须阻断后续 DACL 重应用。"""

        def descriptor(explicit_mask: int) -> bytes:
            payload = bytearray(36)
            payload[0] = 1
            payload[2:4] = (0x8004).to_bytes(2, "little")
            payload[16:20] = (20).to_bytes(4, "little")
            payload[20] = 2
            payload[22:24] = (16).to_bytes(2, "little")
            payload[24:26] = (1).to_bytes(2, "little")
            payload[30:32] = (8).to_bytes(2, "little")
            payload[32:36] = explicit_mask.to_bytes(4, "little")
            return bytes(payload)

        metadata = doctor._GlobalRuleMetadata(
            windows_attributes=0,
            windows_dacl=descriptor(0x11),
            alternate_streams=(),
            xattrs=(),
            uid=None,
            gid=None,
            file_flags=None,
            darwin_acl=None,
        )
        expected = doctor._GlobalRuleSnapshot(
            exists=True,
            data=b"doctor: intended\n",
            identity=(1, 2, 3, 4, 5),
            mode=0o600,
            metadata=metadata,
        )
        actual = replace(
            expected,
            metadata=replace(metadata, windows_dacl=descriptor(0x12)),
        )

        self.assertFalse(doctor._global_rule_same_object_except_windows_dacl(actual, expected))

    def test_macos_missing_extended_acl_is_treated_as_empty(self) -> None:
        """macOS 无扩展 ACL 是正常状态，不得被误报为目标文件消失。"""
        with mock.patch.object(
            doctor,
            "_darwin_global_rule_acl",
            side_effect=FileNotFoundError(errno.ENOENT, "no extended ACL"),
        ):
            self.assertEqual(doctor._darwin_global_rule_acl_or_empty(Path("unused")), b"")

    def test_macos_empty_extended_acl_uses_initialized_acl(self) -> None:
        """空扩展 ACL 必须构造合法 ACL，不能交给不接受空文本的解析器。"""
        libc = mock.MagicMock()
        libc.acl_init.return_value = 123
        libc.acl_set_file.return_value = 0

        with mock.patch.object(doctor.ctypes, "CDLL", return_value=libc):
            doctor._set_darwin_global_rule_acl(Path("unused"), b"")

        libc.acl_init.assert_called_once_with(0)
        libc.acl_from_text.assert_not_called()
        libc.acl_set_file.assert_called_once()
        libc.acl_free.assert_called_once_with(123)

    def test_final_transaction_check_rejects_equal_content_atomic_save(self) -> None:
        """两目标写完后的同字节 atomic-save 也不能被当成本轮提交。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)

            original_write = doctor._write_global_rule_file
            writes = 0
            external: doctor._GlobalRuleSnapshot | None = None

            def equal_content_save_after_second_write(
                path: Path,
                data: bytes,
                expected: doctor._GlobalRuleSnapshot,
                *,
                mode: int | None = None,
            ) -> doctor._GlobalRuleSnapshot:
                """第二目标提交后，用不同 inode 原子保存第一目标的相同字节。"""
                nonlocal writes, external
                committed = original_write(path, data, expected, mode=mode)
                writes += 1
                if writes == 2:
                    editor_save = targets[0].with_name(targets[0].name + ".editor-save")
                    editor_save.write_bytes(targets[0].read_bytes())
                    os.replace(editor_save, targets[0])
                    external = doctor._global_rule_snapshot(targets[0])
                return committed

            with mock.patch.object(
                doctor,
                "_write_global_rule_file",
                new=equal_content_save_after_second_write,
            ):
                with self.assertRaisesRegex(RuntimeError, "并发|复核"):
                    self._sync(source, targets)

            self.assertIsNotNone(external)
            self.assertEqual(doctor._global_rule_snapshot(targets[0]), external)
            self.assertEqual(targets[1].read_bytes(), originals[1])

    def test_concurrent_edit_after_write_is_not_overwritten_by_rollback(self) -> None:
        """写后复核发现并发内容时，回滚不得再用旧快照覆盖该内容。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
            concurrent = b"first: concurrent-after-write\n"
            original_read = Path.read_bytes
            original_write = Path.write_bytes
            heading = doctor.GLOBAL_RULE_SECTION_HEADING.encode("utf-8")
            injected = False

            def guarded_read(path: Path) -> bytes:
                """两个写入都完成后、第一次读回前模拟用户改动第一个目标。"""
                nonlocal injected
                if path == targets[0] and not injected and heading in original_read(targets[1]):
                    original_write(targets[0], concurrent)
                    injected = True
                return original_read(path)

            with mock.patch.object(Path, "read_bytes", new=guarded_read):
                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

            self.assertTrue(injected)
            self.assertEqual(targets[0].read_bytes(), concurrent)
            self.assertEqual(targets[1].read_bytes(), originals[1])

    def test_keyboard_interrupt_never_leaves_partial_target_updates(self) -> None:
        """第二目标阶段收到 Ctrl+C 时，第一个目标也必须恢复原字节。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
            original_write = doctor._write_global_rule_file
            interrupted = False

            def interrupting_write(
                path: Path,
                data: bytes,
                expected: doctor._GlobalRuleSnapshot,
                *,
                mode: int | None = None,
            ) -> doctor._GlobalRuleSnapshot:
                """第一个目标已写后，在第二目标落盘阶段模拟 Ctrl+C。"""
                nonlocal interrupted
                if path == targets[1] and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt
                return original_write(path, data, expected, mode=mode)

            with mock.patch.object(doctor, "_write_global_rule_file", new=interrupting_write):
                with self.assertRaises(KeyboardInterrupt):
                    self._sync(source, targets)

            self.assertTrue(interrupted)
            self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_rollback_is_verified_before_reporting_success(self) -> None:
        """回滚静默未生效时必须报告回滚失败，不能声称两个目标均已恢复。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            originals = [b"first: original\n", b"second: original\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)
            original_write = doctor._write_global_rule_file
            first_was_written = False
            second_failed = False

            def guarded_write(
                path: Path,
                data: bytes,
                expected: doctor._GlobalRuleSnapshot,
                *,
                mode: int | None = None,
            ) -> doctor._GlobalRuleSnapshot:
                """第二目标失败，并让第一目标的回滚写入静默成为 no-op。"""
                nonlocal first_was_written, second_failed
                if path == targets[0] and data != originals[0]:
                    first_was_written = True
                    return original_write(path, data, expected, mode=mode)
                if path == targets[1] and data != originals[1] and not second_failed:
                    second_failed = True
                    raise OSError("simulated second-target failure")
                if path == targets[0] and data == originals[0] and first_was_written:
                    return expected
                return original_write(path, data, expected, mode=mode)

            message = ""
            with mock.patch.object(doctor, "_write_global_rule_file", new=guarded_write):
                with self.assertRaises(RuntimeError) as raised:
                    self._sync(source, targets)
                message = str(raised.exception)

            restored = [target.read_bytes() for target in targets] == originals
            self.assertTrue(second_failed)
            self.assertTrue(restored or "回滚失败" in message)

    def test_failed_sync_removes_newly_created_empty_parent(self) -> None:
        """第二目标父路径无效时，不得遗留第一目标同步新建的空目录。"""
        with tempfile.TemporaryDirectory() as directory:
            source, _ = self._paths(directory)
            root = Path(directory)
            first_parent = root / "new-claude-home"
            first = first_parent / "CLAUDE.md"
            blocker = root / "not-a-directory"
            blocker.write_bytes(b"preserve")
            second = blocker / "AGENTS.md"

            with self.assertRaises(RuntimeError):
                self._sync(source, [first, second])

            self.assertFalse(first.exists())
            self.assertFalse(first_parent.exists())
            self.assertEqual(blocker.read_bytes(), b"preserve")

    def test_partial_parent_creation_failure_cleans_earlier_directories(self) -> None:
        """同一父目录链创建到一半失败时，也必须清理本轮已创建的上级空目录。"""
        with tempfile.TemporaryDirectory() as directory:
            source, _ = self._paths(directory)
            root = Path(directory)
            outer = root / "new-home"
            inner = outer / "nested"
            first = inner / "CLAUDE.md"
            second = root / "existing" / "AGENTS.md"
            second.parent.mkdir()
            second.write_bytes(b"second: preserve\n")
            original_mkdir = Path.mkdir

            def failing_mkdir(path: Path, *args: object, **kwargs: object) -> None:
                """创建外层目录后，在内层目录处模拟权限或 I/O 失败。"""
                if path == inner:
                    raise OSError("simulated nested mkdir failure")
                original_mkdir(path, *args, **kwargs)

            with mock.patch.object(Path, "mkdir", new=failing_mkdir):
                with self.assertRaises(RuntimeError):
                    self._sync(source, [first, second])

            self.assertFalse(outer.exists())
            self.assertEqual(second.read_bytes(), b"second: preserve\n")

    def test_cli_requires_yes_before_sync(self) -> None:
        """doctor 选择自动加载节同步但没有 --yes 时不得调用写入函数。"""
        with mock.patch.object(doctor, "_sync_global_rules") as sync:
            with mock.patch.object(doctor, "_check_claude_hooks"):
                with mock.patch.object(doctor, "_check_codex_plugin"):
                    with mock.patch.object(doctor, "_check_global_rules"):
                        with contextlib.redirect_stdout(io.StringIO()):
                            result = doctor.main(["--repo", str(ROOT), "--sync-global-rules"])

        self.assertEqual(result, 0)
        sync.assert_not_called()

    def test_codex_global_rule_target_respects_custom_codex_home(self) -> None:
        """自定义 CODEX_HOME 时必须把 AGENTS.md 写入同一目录。"""
        with tempfile.TemporaryDirectory() as directory:
            custom_home = Path(directory) / "custom-codex"
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(custom_home)}):
                targets = doctor._global_rule_target_paths()

        self.assertEqual(targets[1], custom_home / "AGENTS.md")


if __name__ == "__main__":
    unittest.main()

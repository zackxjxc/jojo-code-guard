# 全局规则同步回归测试：验证自动加载节的增改、保真和写入保护。

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
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

    def test_invalid_source_with_title_is_rejected(self) -> None:
        """内置源若含全局标题或其他节，应在写入前阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            source.write_bytes(("# 全局规则\n\n" + SOURCE_TEXT).encode("utf-8"))

            with self.assertRaises(RuntimeError):
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

            original_write = Path.write_bytes
            failed = False

            def guarded_write(path: Path, data: bytes) -> int:
                """仅让第二个目标的首次新内容写入失败。"""
                nonlocal failed
                if path == targets[1] and data != originals[1] and not failed:
                    failed = True
                    raise OSError("simulated failure")
                return original_write(path, data)

            with mock.patch.object(Path, "write_bytes", new=guarded_write):
                with self.assertRaises(RuntimeError):
                    self._sync(source, targets)

            self.assertTrue(failed)
            self.assertEqual([target.read_bytes() for target in targets], originals)

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


if __name__ == "__main__":
    unittest.main()

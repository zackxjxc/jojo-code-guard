# 全局规则同步回归测试：覆盖 doctor 的覆盖、合并和写入保护。

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


# 测试直接复用 doctor 中的全局规则实现
ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "jojo-code-guard" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import doctor  # noqa: E402


class GlobalRuleSyncTests(unittest.TestCase):
    """验证两个全局规则目标的同步模式和安全边界。"""

    def _paths(self, directory: str) -> tuple[Path, list[Path]]:
        """创建隔离的源路径和两个固定目标路径。"""
        root = Path(directory)
        source = root / "source.md"
        targets = [root / ".claude" / "CLAUDE.md", root / ".codex" / "AGENTS.md"]
        return source, targets

    def _sync(self, source: Path, targets: list[Path], mode: str) -> list[str]:
        """在隔离路径中运行 doctor 全局规则同步。"""
        with mock.patch.object(doctor, "_global_rule_source_path", return_value=source):
            with mock.patch.object(doctor, "_global_rule_target_paths", return_value=targets):
                return doctor._sync_global_rules(mode)

    def _check(self, source: Path, targets: list[Path], mode: str | None) -> list[doctor.Finding]:
        """在隔离路径中运行 doctor 全局规则预览。"""
        findings: list[doctor.Finding] = []
        with mock.patch.object(doctor, "_global_rule_source_path", return_value=source):
            with mock.patch.object(doctor, "_global_rule_target_paths", return_value=targets):
                doctor._check_global_rules(findings, mode=mode)
        return findings

    def test_overwrite_makes_both_targets_identical(self) -> None:
        """覆盖模式应让两个目标与源文件逐字节一致。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            source_data = "# 新规则\n中文内容\n".encode("utf-8")
            source.write_bytes(source_data)
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(b"old\r\n")

            changed = self._sync(source, targets, "overwrite")

            self.assertEqual(set(changed), {str(path) for path in targets})
            self.assertTrue(all(target.read_bytes() == source_data for target in targets))

    def test_merge_preserves_bom_crlf_and_is_idempotent(self) -> None:
        """合并模式应保留原文、BOM 和 CRLF，并能安全重复执行。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            source.write_text("# 新规则\n中文内容\n", encoding="utf-8")
            original = b"\xef\xbb\xbf# Existing\r\nkeep: true\r\n"
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(original)

            first_changed = self._sync(source, targets, "merge")
            first_data = [target.read_bytes() for target in targets]
            second_changed = self._sync(source, targets, "merge")

            self.assertEqual(set(first_changed), {str(path) for path in targets})
            self.assertEqual(second_changed, [])
            for target, data in zip(targets, first_data):
                self.assertEqual(target.read_bytes(), data)
                self.assertTrue(data.startswith(original))
                self.assertIn(doctor.GLOBAL_RULE_START_MARKER.encode("utf-8"), data)
                self.assertIn(b"\r\n", data)
                self.assertNotIn(b"\n", data[3:].replace(b"\r\n", b""))

    def test_merge_updates_existing_managed_block(self) -> None:
        """源规则更新后应原位替换受管块，不重复追加。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            source.write_text("version: one\n", encoding="utf-8")
            self._sync(source, targets, "merge")
            source.write_text("version: two\n", encoding="utf-8")

            self._sync(source, targets, "merge")

            for target in targets:
                text = target.read_text(encoding="utf-8")
                self.assertNotIn("version: one", text)
                self.assertEqual(text.count("version: two"), 1)
                self.assertEqual(text.count(doctor.GLOBAL_RULE_START_MARKER), 1)

    def test_merge_preflight_prevents_partial_write(self) -> None:
        """任一目标使用混合换行时，两个目标都不得发生部分写入。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            source.write_text("new rules\n", encoding="utf-8")
            originals = [b"first\n", b"second\r\nline\n"]
            for target, data in zip(targets, originals):
                target.parent.mkdir(parents=True)
                target.write_bytes(data)

            findings = self._check(source, targets, "merge")
            with self.assertRaises(RuntimeError):
                self._sync(source, targets, "merge")

            self.assertTrue(any(item.level == "BLOCKED" for item in findings))
            self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_merge_rejects_reversed_managed_markers(self) -> None:
        """结束标记位于开始标记之前时应明确阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            source.write_text("new rules\n", encoding="utf-8")
            malformed = (
                f"{doctor.GLOBAL_RULE_END_MARKER}\n"
                f"old rules\n"
                f"{doctor.GLOBAL_RULE_START_MARKER}\n"
            ).encode("utf-8")
            for target in targets:
                target.parent.mkdir(parents=True)
                target.write_bytes(malformed)

            with self.assertRaises(RuntimeError):
                self._sync(source, targets, "merge")

            self.assertTrue(all(target.read_bytes() == malformed for target in targets))

    def test_write_failure_rolls_back_first_target(self) -> None:
        """第二个目标写入失败时应恢复第一个目标。"""
        with tempfile.TemporaryDirectory() as directory:
            source, targets = self._paths(directory)
            source.write_text("new rules\n", encoding="utf-8")
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
                    self._sync(source, targets, "overwrite")

            self.assertTrue(failed)
            self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_cli_requires_yes_before_sync(self) -> None:
        """doctor 选择同步模式但没有 --yes 时不得调用写入函数。"""
        with mock.patch.object(doctor, "_sync_global_rules") as sync:
            with mock.patch.object(doctor, "_check_claude_hooks"):
                with mock.patch.object(doctor, "_check_codex_plugin"):
                    with mock.patch.object(doctor, "_check_global_rules"):
                        with contextlib.redirect_stdout(io.StringIO()):
                            result = doctor.main(["--repo", str(ROOT), "--sync-global-rules", "merge"])

        self.assertEqual(result, 0)
        sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()

# 字节守护回归测试：覆盖编码、BOM、换行和新增文件规则。

from __future__ import annotations

import sys
import unittest
from pathlib import Path


# 直接导入发布 Skill 中的字节检查核心
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "jojo-code-guard" / "scripts"))

from guard_core import check_new, compare_existing, inspect_bytes  # noqa: E402


class InspectBytesTests(unittest.TestCase):
    """验证文本字节属性识别。"""

    def test_utf8_lf_without_bom(self) -> None:
        """普通 UTF-8 LF 文件应被准确识别。"""
        info = inspect_bytes("中文\n第二行\n".encode("utf-8"))

        self.assertEqual(info.encoding, "utf-8")
        self.assertEqual(info.bom, "none")
        self.assertEqual(info.eol, "lf")
        self.assertTrue(info.final_newline)
        self.assertFalse(info.binary)

    def test_utf8_bom_and_crlf(self) -> None:
        """UTF-8 BOM 与 CRLF 应分别保留。"""
        info = inspect_bytes(b"\xef\xbb\xbfline1\r\nline2\r\n")

        self.assertEqual(info.encoding, "utf-8")
        self.assertEqual(info.bom, "utf-8")
        self.assertEqual(info.eol, "crlf")

    def test_mixed_line_endings(self) -> None:
        """混合换行不能被归类为单一换行。"""
        info = inspect_bytes(b"line1\r\nline2\n")

        self.assertEqual(info.eol, "mixed")

    def test_nul_bytes_are_binary(self) -> None:
        """包含 NUL 的普通字节流应识别为二进制。"""
        info = inspect_bytes(b"text\x00value")

        self.assertTrue(info.binary)
        self.assertEqual(info.encoding, "binary")


class TextPolicyTests(unittest.TestCase):
    """验证已有文件保真和新增文件默认规范。"""

    def test_pure_eol_rewrite_is_blocked(self) -> None:
        """内容不变但整体改写换行时必须阻断。"""
        diagnostics = compare_existing("example.cpp", b"a\r\nb\r\n", b"a\nb\n")

        self.assertIn("PURE_TEXT_REWRITE", {item.code for item in diagnostics})

    def test_new_shell_script_requires_final_lf(self) -> None:
        """新增 shell 脚本必须使用 LF 且以换行结束。"""
        diagnostics = check_new("script.sh", b"#!/bin/sh")

        self.assertIn("NEW_FINAL_NEWLINE", {item.code for item in diagnostics})

    def test_new_cmd_accepts_utf8_crlf(self) -> None:
        """新增 CMD 脚本的 UTF-8 无 BOM 与 CRLF 组合应通过。"""
        diagnostics = check_new("script.cmd", b"@echo off\r\n")

        self.assertEqual(diagnostics, [])

    def test_new_cmd_rejects_lf(self) -> None:
        """新增 CMD 脚本不能误用 LF。"""
        diagnostics = check_new("script.cmd", b"@echo off\n")

        self.assertIn("NEW_EOL", {item.code for item in diagnostics})


if __name__ == "__main__":
    unittest.main()

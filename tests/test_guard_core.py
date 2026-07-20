# 字节守护回归测试：覆盖编码、BOM、换行和新增文件规则。

from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


# 直接导入发布 Skill 中的字节检查核心
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "jojo-code-guard" / "scripts"))

from guard_core import (  # noqa: E402
    check_changes,
    check_filemode_changes,
    check_new,
    compare_existing,
    inspect_bytes,
)


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

    def test_existing_final_newline_change_is_blocked(self) -> None:
        """已有文件的末尾换行状态变化必须阻断。"""
        diagnostics = compare_existing("example.cpp", b"a\n", b"changed")

        result = {item.code: item.level for item in diagnostics}
        self.assertEqual(result["FINAL_NEWLINE_CHANGED"], "BLOCKED")

    def test_existing_replacement_character_is_blocked(self) -> None:
        """已有文件不能新增 U+FFFD 替换字符。"""
        diagnostics = compare_existing(
            "example.cpp",
            "正常\n".encode("utf-8"),
            ("异常" + "\ufffd" + "\n").encode("utf-8"),
        )

        self.assertIn("REPLACEMENT_CHARACTER", {item.code for item in diagnostics})

    def test_existing_mixed_eol_profile_change_is_blocked(self) -> None:
        """已有混合换行文件即使每行内容都改动，也不能悄悄换成另一种整体类型。"""
        diagnostics = compare_existing(
            "example.cpp",
            b"a\nb\r\n",
            b"x\r\ny\n",
        )

        self.assertIn("EOL_CHANGED", {item.code for item in diagnostics})

    def test_existing_repeated_bom_is_blocked(self) -> None:
        """已有文件不能新增隐藏的正文 BOM 字符。"""
        diagnostics = compare_existing(
            "example.cpp",
            "正常\n".encode("utf-8"),
            ("正常\ufeff\n").encode("utf-8"),
        )

        self.assertIn("REPEATED_BOM", {item.code for item in diagnostics})

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

    def test_unknown_text_suffix_uses_new_file_policy(self) -> None:
        """未知后缀的可识别文本也必须执行新增文件规则。"""
        diagnostics = check_new("notes.custom", "中文".encode("cp936"))

        self.assertIn("NEW_ENCODING", {item.code for item in diagnostics})

    def test_unknown_invalid_bytes_are_blocked(self) -> None:
        """未知后缀的不可解码字节不能被静默忽略。"""
        diagnostics = check_new("notes.custom", b"\xff\xfe\xfa")

        self.assertIn("UNKNOWN_ENCODING", {item.code for item in diagnostics})

    def test_unknown_control_bytes_are_blocked(self) -> None:
        """未知后缀中的源码控制字符不能被静默跳过。"""
        diagnostics = check_new("notes.custom", b"hello\x01world\n")

        self.assertIn("CONTROL_CHARACTER", {item.code for item in diagnostics})

    def test_known_binary_suffix_is_ignored(self) -> None:
        """常见二进制资源不能被未知文本推断误报。"""
        diagnostics = check_new("archive.zip", b"PK\x03\x04binary")

        self.assertEqual(diagnostics, [])

    def test_new_tool_file_checks_final_newline_and_replacement(self) -> None:
        """新增工具文件也必须有末尾换行且不能含替换字符。"""
        diagnostics = check_new("view.svg", b"<svg>" + "\ufffd".encode("utf-8") + b"</svg>")

        codes = {item.code for item in diagnostics}
        self.assertIn("NEW_FINAL_NEWLINE", codes)
        self.assertIn("REPLACEMENT_CHARACTER", codes)

    def test_new_tool_file_rejects_non_utf8_and_bom(self) -> None:
        """新增工具文件的编码和 BOM 错误必须阻断，不能只提示后放行。"""
        diagnostics = check_new("view.xml", b"\xef\xbb\xbf<view/>\n")

        result = {item.code: item.level for item in diagnostics}
        self.assertEqual(result["NEW_BOM"], "BLOCKED")
        self.assertNotIn("NEW_BOM_REVIEW", result)

        diagnostics = check_new("view.xml", "<视图/>\n".encode("cp936"))
        result = {item.code: item.level for item in diagnostics}
        self.assertEqual(result["NEW_ENCODING"], "BLOCKED")

    def test_new_tool_file_rejects_crlf(self) -> None:
        """新增工具文件默认使用 LF，不能静默接受 CRLF。"""
        diagnostics = check_new("view.xml", b"<view/>\r\n")

        self.assertEqual(
            {item.code: item.level for item in diagnostics}["NEW_EOL"],
            "BLOCKED",
        )

    def test_new_file_rejects_repeated_bom(self) -> None:
        """新增文本文件不能把 BOM 写进正文。"""
        diagnostics = check_new("notes.txt", "标题\ufeff\n".encode("utf-8"))

        self.assertIn("REPEATED_BOM", {item.code for item in diagnostics})

    def test_unborn_repo_is_strict_by_default(self) -> None:
        """首个提交默认也必须阻断错误编码和换行。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / "bad.cpp").write_bytes("中文\r\n".encode("cp936"))
            subprocess.run(["git", "add", "bad.cpp"], cwd=repo, check=True)

            diagnostics = check_changes(repo, staged=True)

        self.assertTrue(any(item.level == "BLOCKED" for item in diagnostics))
        self.assertIn("NEW_ENCODING", {item.code for item in diagnostics})

    def test_unborn_repo_can_explicitly_keep_legacy_baseline(self) -> None:
        """显式导入历史基线时才把首个提交问题降为警告。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / "bad.cpp").write_bytes("中文\r\n".encode("cp936"))
            subprocess.run(["git", "add", "bad.cpp"], cwd=repo, check=True)

            diagnostics = check_changes(repo, staged=True, allow_initial_baseline=True)

        self.assertFalse(any(item.level == "BLOCKED" for item in diagnostics))
        self.assertIn("INITIAL_NEW_ENCODING", {item.code for item in diagnostics})

    def test_initial_baseline_does_not_allow_unreadable_bytes(self) -> None:
        """历史基线例外不能放过不可解码字节。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / "bad.custom").write_bytes(b"\xff\xfe\xfa")
            subprocess.run(["git", "add", "bad.custom"], cwd=repo, check=True)

            diagnostics = check_changes(repo, staged=True, allow_initial_baseline=True)

        self.assertTrue(any(item.level == "BLOCKED" for item in diagnostics))
        self.assertIn("UNKNOWN_ENCODING", {item.code for item in diagnostics})

    def test_existing_filemode_change_is_blocked(self) -> None:
        """已有文件权限位变化不能被空内容 diff 静默带入提交。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            (repo / "sample.cpp").write_text("int main() {}\n", encoding="utf-8")
            subprocess.run(["git", "add", "sample.cpp"], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.name=jojo-test", "-c", "user.email=jojo@example.com", "commit", "-qm", "基线"],
                cwd=repo,
                check=True,
            )
            subprocess.run(["git", "update-index", "--chmod=+x", "sample.cpp"], cwd=repo, check=True)

            diagnostics = check_filemode_changes(repo, staged=True)

        self.assertIn("FILEMODE_CHANGED", {item.code for item in diagnostics})


if __name__ == "__main__":
    unittest.main()

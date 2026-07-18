#!/usr/bin/env python3
"""同步 Claude 与 Codex 共用的全局 AI 规则文件。"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import pathlib
import sys


TARGET_RELATIVE_PATHS = (
    pathlib.Path(".claude") / "CLAUDE.md",
    pathlib.Path(".codex") / "AGENTS.md",
)


def _source_path() -> pathlib.Path:
    """定位 Skill 内置的全局规则源文件。"""
    return pathlib.Path(__file__).resolve().parents[1] / "references" / "全局规则.md"


def _target_paths() -> list[pathlib.Path]:
    """生成固定的两个用户级目标路径，不判断当前 AI 客户端。"""
    home = pathlib.Path.home()
    return [home / relative for relative in TARGET_RELATIVE_PATHS]


def _text_info(data: bytes) -> dict[str, str | int]:
    """提取文件字节、编码标记和换行摘要。"""
    if data.startswith(b"\xef\xbb\xbf"):
        bom = "utf-8"
        payload = data[3:]
    else:
        bom = "none"
        payload = data
    text = payload.decode("utf-8", errors="replace")
    crlf = text.count("\r\n")
    lf_only = text.replace("\r\n", "").count("\n")
    cr_only = text.replace("\r\n", "").count("\r")
    if crlf and (lf_only or cr_only):
        eol = "mixed"
    elif crlf:
        eol = "crlf"
    elif lf_only:
        eol = "lf"
    elif cr_only:
        eol = "cr"
    else:
        eol = "none"
    return {
        "bytes": len(data),
        "bom": bom,
        "eol": eol,
        "sha256": hashlib.sha256(data).hexdigest()[:16],
        "text": text,
    }


def _describe(path: pathlib.Path, data: bytes) -> str:
    """格式化文件摘要，供用户确认覆盖范围。"""
    info = _text_info(data)
    return (
        f"{path}\n"
        f"  字节：{info['bytes']}，BOM：{info['bom']}，换行：{info['eol']}，"
        f"SHA-256：{info['sha256']}"
    )


def _print_difference(source: pathlib.Path, source_data: bytes, target: pathlib.Path, target_data: bytes) -> None:
    """输出文本差异摘要，避免差异过大淹没诊断结果。"""
    source_text = _text_info(source_data)["text"].splitlines(keepends=True)
    target_text = _text_info(target_data)["text"].splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            source_text,
            target_text,
            fromfile=str(source),
            tofile=str(target),
            n=2,
        )
    )
    print("  文本差异：")
    if not diff:
        print("    内容文本相同，但字节编码、BOM 或换行不同")
        return
    limit = 80
    for line in diff[:limit]:
        print(f"    {line.rstrip()}")
    if len(diff) > limit:
        print(f"    ……差异共 {len(diff)} 行，仅显示前 {limit} 行")


def _inspect(source: pathlib.Path, source_data: bytes, target: pathlib.Path) -> bool:
    """检查单个目标并报告是否缺失、相同或不同。"""
    print(f"\n目标：{target}")
    if not target.exists():
        print("  状态：MISSING（文件不存在，将在确认后创建）")
        return True
    if target.is_symlink():
        print("  状态：BLOCKED（目标是符号链接，为避免覆盖链接指向文件而停止）")
        return False
    target_data = target.read_bytes()
    if target_data == source_data:
        print("  状态：IDENTICAL（内容、编码、BOM 和换行均相同）")
        return True
    print("  状态：DIFFERENT（需要确认后覆盖）")
    print("  源文件摘要：")
    print(f"    {_describe(source, source_data)}")
    print("  目标文件摘要：")
    print(f"    {_describe(target, target_data)}")
    _print_difference(source, source_data, target, target_data)
    return True


def _sync(source: pathlib.Path, source_data: bytes, targets: list[pathlib.Path]) -> int:
    """覆盖两个固定目标文件。"""
    for target in targets:
        if target.is_symlink():
            print(f"BLOCKED  目标是符号链接，未覆盖：{target}", file=sys.stderr)
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_data)
        print(f"OK  已覆盖：{target}")
    return 0


def main(arguments: list[str] | None = None) -> int:
    """比较并按确认参数同步两个全局规则文件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="确认覆盖两个目标文件")
    options = parser.parse_args(arguments)
    source = _source_path()
    if not source.is_file():
        print(f"BLOCKED  Skill 内置规则文件不存在：{source}", file=sys.stderr)
        return 2
    source_data = source.read_bytes()
    print(f"源文件：{source}")
    print(_describe(source, source_data))
    targets = _target_paths()
    valid = True
    for target in targets:
        valid = _inspect(source, source_data, target) and valid
    if not valid:
        return 2
    if not options.yes:
        print("\nACTION_REQUIRED  以上目标将在确认后覆盖；确认后重新运行并添加 --yes")
        return 3
    return _sync(source, source_data, targets)


if __name__ == "__main__":
    raise SystemExit(main())

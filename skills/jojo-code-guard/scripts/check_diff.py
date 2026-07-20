#!/usr/bin/env python3
"""啾啾代码守护：检查未提交修改的编码、BOM、换行和 Git 空白错误。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

from guard_core import (
    Diagnostic,
    check_changes,
    check_conversion_policy,
    check_diff_size,
    check_filemode_changes,
    find_repo,
)


def _configure_output() -> None:
    """在 Windows 控制台和 Git hook 中统一使用 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _git_text(repo: pathlib.Path, arguments: list[str]) -> tuple[int, str]:
    """执行 Git 并以 UTF-8 安全显示命令输出。"""
    result = subprocess.run(
        ["git", *arguments],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    output = result.stdout or result.stderr
    return result.returncode, output.decode("utf-8", errors="replace").strip()


def _whitespace_diagnostics(repo: pathlib.Path, staged: bool) -> list[Diagnostic]:
    """将 git diff --check 结果转换为诊断。"""
    arguments = [
        "-c",
        "core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol",
        "diff",
        "--check",
    ]
    if staged:
        arguments.insert(4, "--cached")
    code, output = _git_text(repo, arguments)
    if code == 0:
        return []
    return [Diagnostic("BLOCKED", "GIT_WHITESPACE", "暂存区" if staged else "工作区", output)]


def _print_text(repo: pathlib.Path, diagnostics: list[Diagnostic]) -> None:
    """输出适合会话阅读的简明报告。"""
    _, status = _git_text(repo, ["-c", "core.quotepath=false", "status", "--short"])
    _, unstaged = _git_text(repo, ["diff", "--stat"])
    _, staged = _git_text(repo, ["diff", "--cached", "--stat"])
    print(f"仓库：{repo}")
    print("\n当前状态：")
    print(status or "工作区干净")
    print("\n未暂存统计：")
    print(unstaged or "无")
    print("\n已暂存统计：")
    print(staged or "无")
    print("\n守护结果：")
    if not diagnostics:
        print("OK  未发现编码、BOM、换行或 Git 空白污染")
        return
    for item in diagnostics:
        print(f"{item.level}  {item.code}  {item.path}：{item.message}")
    print("\n建议：先恢复本轮造成的污染，再用最小补丁重做；不要直接格式化整个文件。")


def main(arguments: list[str] | None = None) -> int:
    """解析参数并检查工作区与暂存区。"""
    _configure_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Git 工作树内的路径")
    parser.add_argument("--staged-only", action="store_true", help="只检查暂存区")
    parser.add_argument(
        "--allow-initial-baseline",
        action="store_true",
        help="允许无 HEAD 仓库保留历史编码/EOL属性（不可解码、二进制和替换字符仍阻断）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    options = parser.parse_args(arguments)
    try:
        repo = find_repo(options.repo)
        diagnostics = check_changes(
            repo,
            staged=True,
            allow_initial_baseline=options.allow_initial_baseline,
        )
        diagnostics.extend(check_conversion_policy(repo, staged=True))
        diagnostics.extend(check_diff_size(repo, staged=True))
        diagnostics.extend(check_filemode_changes(repo, staged=True))
        diagnostics.extend(_whitespace_diagnostics(repo, staged=True))
        if not options.staged_only:
            diagnostics.extend(
                check_changes(
                    repo,
                    staged=False,
                    allow_initial_baseline=options.allow_initial_baseline,
                )
            )
            diagnostics.extend(check_conversion_policy(repo, staged=False))
            diagnostics.extend(check_diff_size(repo, staged=False))
            diagnostics.extend(check_filemode_changes(repo, staged=False))
            diagnostics.extend(_whitespace_diagnostics(repo, staged=False))
    except RuntimeError as error:
        print(f"BLOCKED  {error}", file=sys.stderr)
        return 2

    unique = list(dict.fromkeys(diagnostics))
    if options.json:
        print(json.dumps([item.__dict__ for item in unique], ensure_ascii=False, indent=2))
    else:
        _print_text(repo, unique)
    return 1 if any(item.level == "BLOCKED" for item in unique) else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Git pre-commit 使用的暂存区守护入口。"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

try:
    from jojo_guard_core import (
        check_changes,
        check_conversion_policy,
        check_diff_size,
        check_filemode_changes,
        find_repo,
    )
except ImportError:
    from guard_core import (
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


def main() -> int:
    """阻止暂存区中的隐式编码、BOM、换行和空白污染。"""
    _configure_output()
    try:
        repo = find_repo()
        allow_initial_baseline = os.environ.get("JOJO_CODE_GUARD_ALLOW_INITIAL_BASELINE") == "1"
        diagnostics = check_changes(
            repo,
            staged=True,
            allow_initial_baseline=allow_initial_baseline,
        )
        diagnostics.extend(check_conversion_policy(repo, staged=True))
        diagnostics.extend(check_diff_size(repo, staged=True, block_format_only=True))
        diagnostics.extend(check_filemode_changes(repo, staged=True))
    except RuntimeError as error:
        print(f"jojo-code-guard: {error}", file=sys.stderr)
        return 2

    whitespace = subprocess.run(
        [
            "git",
            "-c",
            "core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol",
            "diff",
            "--cached",
            "--check",
        ],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    for item in diagnostics:
        print(f"{item.level} {item.code} {item.path}: {item.message}", file=sys.stderr)
    if os.environ.get("JOJO_CODE_GUARD_ALLOW_INITIAL_BASELINE") == "1":
        print(
            "WARNING INITIAL_BASELINE_OVERRIDE：本次 Hook 使用了显式历史基线例外，"
            "请在提交后尽快建立严格基线",
            file=sys.stderr,
        )
    if whitespace.returncode != 0:
        output = (whitespace.stdout or whitespace.stderr).decode("utf-8", errors="replace").strip()
        print(output, file=sys.stderr)
    if any(item.level == "BLOCKED" for item in diagnostics) or whitespace.returncode != 0:
        print("jojo-code-guard: commit blocked; inspect the staged diff before retrying.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

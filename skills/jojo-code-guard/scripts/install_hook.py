#!/usr/bin/env python3
"""将啾啾代码守护安装到仓库私有的 Git hooks 目录。"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import stat
import subprocess
import sys

from guard_core import find_repo


MARKER = "jojo-code-guard-managed-hook:v1"
WRAPPER = """#!/bin/sh
# jojo-code-guard-managed-hook:v1
# 此 hook 只检查暂存区，不修改文件。
set -eu
hook_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v py >/dev/null 2>&1 && py -3 -c 'import sys' >/dev/null 2>&1; then
    exec py -3 "$hook_dir/jojo_hook_check.py"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$hook_dir/jojo_hook_check.py"
elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(sys.version_info[0] != 3)' >/dev/null 2>&1; then
    exec python "$hook_dir/jojo_hook_check.py"
fi
echo "jojo-code-guard: Python 3 is required." >&2
exit 2
"""


def _configure_output() -> None:
    """在 Windows 控制台和 Git hook 中统一使用 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _config(repo: pathlib.Path, scope: str, key: str) -> str:
    """读取指定作用域的 Git 配置。"""
    result = subprocess.run(
        ["git", "config", scope, "--get", key],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.decode("utf-8", errors="replace").strip() if result.returncode == 0 else ""


def _default_hooks_dir(repo: pathlib.Path) -> pathlib.Path:
    """定位不受全局 hooksPath 影响的仓库私有 hooks 目录。"""
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("无法定位 Git common directory")
    common = pathlib.Path(os.fsdecode(result.stdout.strip()))
    if not common.is_absolute():
        common = (repo / common).resolve()
    return common / "hooks"


def install(repo: pathlib.Path, force_owned: bool = False) -> pathlib.Path:
    """安装或更新自有 hook，绝不覆盖第三方 hook。"""
    local_hooks = _config(repo, "--local", "core.hooksPath")
    global_hooks = _config(repo, "--global", "core.hooksPath")
    if local_hooks or global_hooks:
        raise RuntimeError(
            "检测到 core.hooksPath；为避免覆盖现有 hook 链，请先人工确认并将 jojo_hook_check.py 接入该链"
        )
    hooks_dir = _default_hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    pre_commit = hooks_dir / "pre-commit"
    source_dir = pathlib.Path(__file__).resolve().parent
    source_files = {
        "jojo_guard_core.py": source_dir / "guard_core.py",
        "jojo_hook_check.py": source_dir / "hook_check.py",
    }
    if pre_commit.exists():
        existing = pre_commit.read_text(encoding="utf-8", errors="replace")
        if MARKER not in existing:
            raise RuntimeError(f"已有第三方 pre-commit，未覆盖：{pre_commit}")
        copies_current = all(
            (hooks_dir / name).is_file() and (hooks_dir / name).read_bytes() == source.read_bytes()
            for name, source in source_files.items()
        )
        if not force_owned and existing == WRAPPER and copies_current:
            return pre_commit

    for name, source in source_files.items():
        shutil.copyfile(source, hooks_dir / name)
    pre_commit.write_bytes(WRAPPER.encode("utf-8"))
    pre_commit.chmod(pre_commit.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return pre_commit


def main(arguments: list[str] | None = None) -> int:
    """解析参数并安装本地 hook。"""
    _configure_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Git 工作树内的路径")
    parser.add_argument("--yes", action="store_true", help="确认安装或更新 Skill 自有 hook")
    options = parser.parse_args(arguments)
    if not options.yes:
        print("ACTION_REQUIRED  安装会写入 .git/hooks；确认后重新运行并添加 --yes")
        return 3
    try:
        path = install(find_repo(options.repo), force_owned=True)
    except (OSError, RuntimeError) as error:
        print(f"BLOCKED  {error}", file=sys.stderr)
        return 2
    print(f"OK  已安装：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

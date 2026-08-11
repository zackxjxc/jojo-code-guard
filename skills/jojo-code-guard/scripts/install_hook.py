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
KNOWN_WRAPPERS = frozenset({WRAPPER.encode("utf-8")})


def _configure_output() -> None:
    """在 Windows 控制台和 Git hook 中统一使用 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run_git(repo: pathlib.Path, arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    """执行 Git 命令并保留返回码与原始输出。"""
    result = subprocess.run(
        ["git", *arguments],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result


def _git_path(repo: pathlib.Path, arguments: list[str], description: str) -> pathlib.Path:
    """读取 Git 返回的路径，并按仓库根目录解析相对值。"""
    result = _run_git(repo, arguments)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"无法定位 {description}" + (f"：{detail}" if detail else ""))
    raw = result.stdout.rstrip(b"\r\n")
    if not raw:
        raise RuntimeError(f"Git 返回了空的 {description}")
    path = pathlib.Path(os.fsdecode(raw))
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _effective_hooks_path_setting(repo: pathlib.Path) -> str | None:
    """读取最终生效的 core.hooksPath，并尽量保留来源信息。"""
    effective = _run_git(repo, ["config", "--get", "core.hooksPath"])
    if effective.returncode == 1:
        return None
    if effective.returncode != 0:
        detail = effective.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("无法读取有效 core.hooksPath" + (f"：{detail}" if detail else ""))

    value = effective.stdout.decode("utf-8", errors="replace").rstrip("\r\n")
    origin = _run_git(repo, ["config", "--show-origin", "--show-scope", "--get", "core.hooksPath"])
    if origin.returncode == 0:
        detail = origin.stdout.decode("utf-8", errors="replace").strip()
        if detail:
            return detail
    return f"core.hooksPath={value!r}"


def _effective_hooks_dir(repo: pathlib.Path) -> pathlib.Path:
    """定位 Git 实际使用的 hooks 目录，并拒绝接管已有 hooksPath 链。"""
    hooks_dir = _git_path(repo, ["rev-parse", "--git-path", "hooks"], "有效 hooks 目录")
    configured = _effective_hooks_path_setting(repo)
    if configured is not None:
        raise RuntimeError(
            "检测到有效 core.hooksPath；为避免覆盖现有 hook 链，不自动安装。"
            f"来源和值：{configured}；Git 当前 hooks 目录：{hooks_dir}"
        )

    common_dir = _git_path(repo, ["rev-parse", "--git-common-dir"], "Git common directory")
    expected = (common_dir / "hooks").resolve()
    if hooks_dir != expected:
        raise RuntimeError(
            f"Git 报告的 hooks 目录与默认 common-dir 语义不一致：{hooks_dir} != {expected}"
        )
    return hooks_dir


def install(repo: pathlib.Path) -> pathlib.Path:
    """安装或更新自有 hook，绝不覆盖第三方 hook。"""
    hooks_dir = _effective_hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    pre_commit = hooks_dir / "pre-commit"
    source_dir = pathlib.Path(__file__).resolve().parent
    source_files = {
        "jojo_guard_core.py": source_dir / "guard_core.py",
        "jojo_hook_check.py": source_dir / "hook_check.py",
    }
    if pre_commit.exists() or pre_commit.is_symlink():
        if pre_commit.is_symlink():
            raise RuntimeError(f"已有符号链接 pre-commit，未覆盖：{pre_commit}")
        existing = pre_commit.read_bytes()
        if existing not in KNOWN_WRAPPERS:
            raise RuntimeError(f"已有第三方 pre-commit，未覆盖：{pre_commit}")
        copies_current = all(
            (hooks_dir / name).is_file() and (hooks_dir / name).read_bytes() == source.read_bytes()
            for name, source in source_files.items()
        )
        if copies_current:
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
        path = install(find_repo(options.repo))
    except (OSError, RuntimeError) as error:
        print(f"BLOCKED  {error}", file=sys.stderr)
        return 2
    print(f"OK  已安装：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

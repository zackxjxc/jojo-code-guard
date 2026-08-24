#!/usr/bin/env python3
"""将啾啾代码守护安装到仓库私有的 Git hooks 目录。"""

from __future__ import annotations

import argparse
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

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
    """读取 Git 返回的路径，并按仓库根目录转为不解析链接的绝对路径。"""
    result = _run_git(repo, arguments)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"无法定位 {description}" + (f"：{detail}" if detail else ""))
    raw = result.stdout.rstrip(b"\r\n")
    if not raw:
        raise RuntimeError(f"Git 返回了空的 {description}")
    path = pathlib.Path(os.fsdecode(raw))
    candidate = path if path.is_absolute() else repo / path
    return pathlib.Path(os.path.abspath(os.fspath(candidate)))


def _path_is_link_like(path: pathlib.Path, details: os.stat_result | None = None) -> bool:
    """识别符号链接、junction 和其他 Windows reparse point。"""
    if path.is_symlink():
        return True
    if details is None:
        try:
            details = path.lstat()
        except FileNotFoundError:
            return False
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_point)


def _assert_safe_hooks_directory(path: pathlib.Path) -> None:
    """拒绝通过链接型或非目录 hooks 路径写入。"""
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise RuntimeError(f"Git hooks 目录不存在：{path}") from error
    if _path_is_link_like(path, details):
        raise RuntimeError(f"Git hooks 目录是符号链接、junction 或 reparse point：{path}")
    if not stat.S_ISDIR(details.st_mode):
        raise RuntimeError(f"Git hooks 路径不是目录：{path}")


def _managed_file_identity(path: pathlib.Path, label: str) -> tuple[int, ...] | None:
    """记录受管普通文件身份，并拒绝链接或多链接对象。"""
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    if _path_is_link_like(path, details):
        raise RuntimeError(f"{label}是符号链接、junction 或 reparse point：{path}")
    if not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"{label}不是普通文件：{path}")
    if details.st_nlink != 1:
        raise RuntimeError(f"{label}是硬链接或多链接文件：{path}")
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(stat.S_IFMT(details.st_mode)),
        int(details.st_size),
        int(details.st_mtime_ns),
        int(details.st_nlink),
    )


def _write_managed_file(
    path: pathlib.Path,
    data: bytes,
    mode: int,
    label: str,
    expected: tuple[int, ...] | None,
) -> None:
    """从独占临时文件原子发布受管文件，不跟随目标链接。"""
    _assert_safe_hooks_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        _assert_safe_hooks_directory(path.parent)
        if _managed_file_identity(path, label) != expected:
            raise RuntimeError(f"{label}在发布前发生变化：{path}")
        os.replace(temporary, path)
        if _managed_file_identity(path, label) is None or path.read_bytes() != data:
            raise RuntimeError(f"{label}发布后复核失败：{path}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


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
    expected = pathlib.Path(os.path.abspath(os.fspath(common_dir / "hooks")))
    if os.path.normcase(os.fspath(hooks_dir)) != os.path.normcase(os.fspath(expected)):
        raise RuntimeError(
            f"Git 报告的 hooks 目录与默认 common-dir 语义不一致：{hooks_dir} != {expected}"
        )
    return hooks_dir


def install(repo: pathlib.Path) -> pathlib.Path:
    """安装或更新自有 hook，绝不覆盖第三方 hook。"""
    hooks_dir = _effective_hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    _assert_safe_hooks_directory(hooks_dir)
    pre_commit = hooks_dir / "pre-commit"
    source_dir = pathlib.Path(__file__).resolve().parent
    source_files = {
        "jojo_guard_core.py": source_dir / "guard_core.py",
        "jojo_hook_check.py": source_dir / "hook_check.py",
    }
    pre_commit_identity = _managed_file_identity(pre_commit, "pre-commit")
    helper_identities = {
        name: _managed_file_identity(hooks_dir / name, f"Hook 辅助脚本 {name}")
        for name in source_files
    }
    if pre_commit_identity is not None:
        existing = pre_commit.read_bytes()
        if existing not in KNOWN_WRAPPERS:
            raise RuntimeError(f"已有第三方 pre-commit，未覆盖：{pre_commit}")
        copies_current = all(
            helper_identities[name] is not None
            and (hooks_dir / name).read_bytes() == source.read_bytes()
            for name, source in source_files.items()
        )
        if copies_current:
            return pre_commit

    for name, source in source_files.items():
        source_details = source.stat()
        _write_managed_file(
            hooks_dir / name,
            source.read_bytes(),
            stat.S_IMODE(source_details.st_mode),
            f"Hook 辅助脚本 {name}",
            helper_identities[name],
        )
    wrapper_mode = (
        stat.S_IMODE(pre_commit.lstat().st_mode)
        if pre_commit_identity is not None
        else 0o755
    )
    wrapper_mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    _write_managed_file(
        pre_commit,
        WRAPPER.encode("utf-8"),
        wrapper_mode,
        "pre-commit",
        pre_commit_identity,
    )
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

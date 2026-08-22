#!/usr/bin/env python3
"""从发布仓库根目录原子生成完整的 Claude Code 插件适配包。"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import tempfile
import uuid


def _copy(source: pathlib.Path, destination: pathlib.Path, executable: bool = False) -> None:
    """复制一个发布资源，并在类 Unix 系统保留可执行权限。"""
    if not source.is_file():
        raise FileNotFoundError(f"发布资源不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if executable and os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
    """复制一棵发布目录，并排除解释器缓存。"""
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _validate_adapter(destination: pathlib.Path) -> None:
    """确认生成目录包含 Claude 自动加载所需的全部资源。"""
    required = (
        destination / ".claude-plugin" / "plugin.json",
        destination / ".claude-plugin" / "marketplace.json",
        destination / "hooks" / "hooks.json",
        destination / "hooks" / "session-start",
        destination / "hooks" / "post-write-check",
        destination / "hooks" / "run-hook.cmd",
        destination / "skills" / "jojo-code-guard" / "SKILL.md",
        destination / "skills" / "jojo-code-guard" / "references" / "自动加载规则.md",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Claude 适配包缺少资源：" + ", ".join(missing))


def _replace_directory(staging: pathlib.Path, destination: pathlib.Path) -> None:
    """用已校验的同盘暂存目录替换旧安装；失败时恢复旧目录。"""
    if destination.is_symlink():
        raise RuntimeError(f"拒绝覆盖符号链接安装目录：{destination}")
    if destination.exists() and not destination.is_dir():
        raise RuntimeError(f"拒绝覆盖非目录安装目标：{destination}")

    backup: pathlib.Path | None = None
    try:
        if destination.exists():
            backup = destination.parent / f".{destination.name}.backup-{uuid.uuid4().hex}"
            os.replace(destination, backup)
        os.replace(staging, destination)
    except Exception:
        if backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup)


def _build_adapter(root: pathlib.Path, staging: pathlib.Path) -> None:
    """在空暂存目录内构建完整适配包。"""
    source_skills = root / "skills"
    source_hooks = root / "hooks"
    source_commands = root / "commands"
    for source in (source_skills, source_hooks, source_commands):
        if not source.is_dir():
            raise FileNotFoundError(f"发布目录不存在：{source}")

    _copy(root / ".claude-plugin" / "plugin.json", staging / ".claude-plugin" / "plugin.json")
    _copy(root / ".claude-plugin" / "marketplace.json", staging / ".claude-plugin" / "marketplace.json")
    _copy_tree(source_hooks, staging / "hooks")
    _copy_tree(source_commands, staging / "commands")
    _copy_tree(source_skills, staging / "skills")

    if os.name != "nt":
        for name in ("session-start", "post-write-check"):
            path = staging / "hooks" / name
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _validate_adapter(staging)


def main() -> int:
    """从空目录重建 Claude manifest、命令、hook 和共享 Skill。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    codex_home = pathlib.Path(
        os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))
    ).expanduser()
    destination = pathlib.Path(
        os.environ.get(
            "JOJO_CLAUDE_PLUGIN_DIR",
            str(codex_home / "jojo-code-guard-claude-plugin"),
        )
    ).expanduser()

    if destination.resolve() == root.resolve():
        _validate_adapter(root)
        print(f"Claude adapter already uses source tree: {destination}")
        return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.sync-", dir=destination.parent)
    )
    try:
        _build_adapter(root, staging)
        _replace_directory(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f"Synced Claude adapter: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

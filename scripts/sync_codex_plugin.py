#!/usr/bin/env python3
"""从发布仓库根目录原子生成完整的 Codex 插件适配包。"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
import uuid


def _copy_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
    """复制一棵发布目录，并排除解释器缓存。"""
    if not source.is_dir():
        raise FileNotFoundError(f"发布目录不存在：{source}")
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _validate_adapter(destination: pathlib.Path) -> None:
    """确认生成目录包含 Codex 自动守护所需的全部资源。"""
    required = (
        destination / ".codex-plugin" / "plugin.json",
        destination / "hooks" / "hooks.json",
        destination / "hooks" / "session-start",
        destination / "hooks" / "post-write-check",
        destination / "hooks" / "run-hook.cmd",
        destination / "skills" / "jojo-code-guard" / "SKILL.md",
        destination / "skills" / "jojo-code-guard" / "scripts" / "check_diff.py",
        destination / "skills" / "jojo-code-guard" / "scripts" / "guard_core.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Codex 适配包缺少资源：" + ", ".join(missing))


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
    source_manifest = root / ".codex-plugin" / "plugin.json"
    if not source_manifest.is_file():
        raise FileNotFoundError(f"Codex manifest 不存在：{source_manifest}")

    manifest_destination = staging / ".codex-plugin"
    manifest_destination.mkdir(parents=True)
    shutil.copy2(source_manifest, manifest_destination / "plugin.json")
    _copy_tree(root / "hooks", staging / "hooks")
    _copy_tree(root / "skills", staging / "skills")
    _validate_adapter(staging)


def main() -> int:
    """复制 Codex manifest、标准 Hook 目录和共享 Skill。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    codex_home = pathlib.Path(
        os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))
    ).expanduser()
    destination = pathlib.Path(
        os.environ.get(
            "JOJO_CODEX_PLUGIN_DIR",
            str(codex_home / "plugins" / "jojo-code-guard"),
        )
    ).expanduser()

    if destination.resolve() == root.resolve():
        _validate_adapter(root)
        print(f"Codex plugin already uses source tree: {destination}")
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
    print(f"Synced Codex plugin: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

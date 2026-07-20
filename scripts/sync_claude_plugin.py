#!/usr/bin/env python3
"""从发布仓库根目录生成完整的 Claude Code 插件适配包。"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat


def _copy(source: pathlib.Path, destination: pathlib.Path, executable: bool = False) -> None:
    """复制一个发布资源，并在类 Unix 系统保留可执行权限。"""
    if not source.is_file():
        raise FileNotFoundError(f"发布资源不存在：{source}")
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if executable and os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _remove_obsolete_resources(root: pathlib.Path, destination: pathlib.Path) -> None:
    """从生成目录移除不再发布的旧版入口。"""
    if root.resolve() == destination.resolve():
        return
    for name in ("run-hook.cmd", "run-hook.sh"):
        path = destination / "hooks" / name
        if path.is_file() or path.is_symlink():
            path.unlink()
    obsolete_command = destination / "commands" / "commit.md"
    if obsolete_command.is_file() or obsolete_command.is_symlink():
        obsolete_command.unlink()
    old_skill = destination / "skills" / "jojo-code-guard-sync-global-rules"
    if old_skill.is_dir():
        shutil.rmtree(old_skill)
    removed_commit_skill = destination / "skills" / "jojo-code-guard-commit"
    if removed_commit_skill.is_dir():
        shutil.rmtree(removed_commit_skill)
    references = destination / "skills" / "jojo-code-guard" / "references"
    for name in ("兼容性改进计划.md", "生效与验收.md"):
        obsolete_document = references / name
        if obsolete_document.is_file() or obsolete_document.is_symlink():
            obsolete_document.unlink()


def _validate_adapter(destination: pathlib.Path) -> None:
    """确认生成目录包含 Claude 自动加载所需的全部资源。"""
    required = (
        destination / ".claude-plugin" / "plugin.json",
        destination / ".claude-plugin" / "marketplace.json",
        destination / "hooks" / "hooks.json",
        destination / "hooks" / "session-start",
        destination / "hooks" / "post-write-check",
        destination / "skills" / "jojo-code-guard" / "SKILL.md",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Claude 适配包缺少资源：" + ", ".join(missing))


def main() -> int:
    """从空目录重建 Claude manifest、命令、hook 和共享 Skill。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    source_skills = root / "skills"
    if not source_skills.is_dir():
        raise FileNotFoundError(f"Skill 源目录不存在：{source_skills}")

    codex_home = pathlib.Path(
        os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))
    ).expanduser()
    destination = pathlib.Path(
        os.environ.get(
            "JOJO_CLAUDE_PLUGIN_DIR",
            str(codex_home / "jojo-code-guard-claude-plugin"),
        )
    ).expanduser()

    files = [
        (root / ".claude-plugin" / "plugin.json", destination / ".claude-plugin" / "plugin.json", False),
        (root / ".claude-plugin" / "marketplace.json", destination / ".claude-plugin" / "marketplace.json", False),
        (root / "hooks" / "hooks.json", destination / "hooks" / "hooks.json", False),
        (root / "hooks" / "session-start", destination / "hooks" / "session-start", True),
        (root / "hooks" / "post-write-check", destination / "hooks" / "post-write-check", True),
    ]
    for source, target, executable in files:
        _copy(source, target, executable=executable)

    command_root = root / "commands"
    for source in sorted(command_root.glob("*.md")):
        _copy(source, destination / "commands" / source.name)

    skill_destination = destination / "skills"
    if source_skills.resolve() != skill_destination.resolve():
        shutil.copytree(
            source_skills,
            skill_destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    _remove_obsolete_resources(root, destination)
    _validate_adapter(destination)
    print(f"Synced Claude adapter: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

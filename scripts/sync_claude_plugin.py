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
        (root / "hooks" / "run-hook.cmd", destination / "hooks" / "run-hook.cmd", True),
        (root / "hooks" / "session-start", destination / "hooks" / "session-start", True),
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
    print(f"Synced Claude adapter: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

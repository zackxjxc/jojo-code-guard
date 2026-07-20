#!/usr/bin/env python3
"""从发布仓库根目录生成 Codex 插件适配包。"""

from __future__ import annotations

import os
import pathlib
import shutil


def main() -> int:
    """复制 Codex manifest、标准 Hook 目录和共享 Skill。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    source_manifest = root / ".codex-plugin" / "plugin.json"
    source_hooks = root / "hooks"
    source_skills = root / "skills"
    if not source_manifest.is_file():
        raise FileNotFoundError(f"Codex manifest 不存在：{source_manifest}")
    if not source_hooks.is_dir():
        raise FileNotFoundError(f"Codex Hook 源目录不存在：{source_hooks}")
    if not source_skills.is_dir():
        raise FileNotFoundError(f"Skill 源目录不存在：{source_skills}")

    codex_home = pathlib.Path(
        os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))
    ).expanduser()
    destination = pathlib.Path(
        os.environ.get(
            "JOJO_CODEX_PLUGIN_DIR",
            str(codex_home / "plugins" / "jojo-code-guard"),
        )
    ).expanduser()
    destination.joinpath(".codex-plugin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_manifest, destination / ".codex-plugin" / "plugin.json")
    if source_hooks.resolve() != (destination / "hooks").resolve():
        shutil.copytree(
            source_hooks,
            destination / "hooks",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    for name in ("run-hook.cmd", "run-hook.sh"):
        obsolete_launcher = destination / "hooks" / name
        if obsolete_launcher.is_file() or obsolete_launcher.is_symlink():
            obsolete_launcher.unlink()

    # Claude 的 commands 由对应客户端加载；Codex 使用原生 Skill，清理本脚本曾生成的同名旧入口，避免重复触发。
    command_destination = destination / "commands"
    if command_destination.is_dir() and command_destination.resolve() != (root / "commands").resolve():
        for name in ("check-diff.md", "commit.md", "doctor.md", "help.md"):
            legacy_command = command_destination / name
            if legacy_command.is_file():
                legacy_command.unlink()

    skill_destination = destination / "skills"
    if source_skills.resolve() != skill_destination.resolve():
        shutil.copytree(
            source_skills,
            skill_destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        old_skill = skill_destination / "jojo-code-guard-sync-global-rules"
        if old_skill.is_dir():
            shutil.rmtree(old_skill)
        removed_commit_skill = skill_destination / "jojo-code-guard-commit"
        if removed_commit_skill.is_dir():
            shutil.rmtree(removed_commit_skill)
        references = skill_destination / "jojo-code-guard" / "references"
        for name in ("兼容性改进计划.md", "生效与验收.md"):
            obsolete_document = references / name
            if obsolete_document.is_file() or obsolete_document.is_symlink():
                obsolete_document.unlink()
    print(f"Synced Codex plugin: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

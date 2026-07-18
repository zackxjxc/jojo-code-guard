#!/usr/bin/env python3
"""从发布仓库根目录生成 Codex 插件适配包。"""

from __future__ import annotations

import os
import pathlib
import shutil


def main() -> int:
    """复制 Codex manifest 和共享 Skill，不生成或执行 Claude hook。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    source_manifest = root / ".codex-plugin" / "plugin.json"
    source_skills = root / "skills"
    if not source_manifest.is_file():
        raise FileNotFoundError(f"Codex manifest 不存在：{source_manifest}")
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

    skill_destination = destination / "skills"
    if source_skills.resolve() != skill_destination.resolve():
        shutil.copytree(
            source_skills,
            skill_destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    print(f"Synced Codex plugin: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

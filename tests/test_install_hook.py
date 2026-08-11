# Git hook 安装回归测试：验证幂等更新和第三方 hook 保护。

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


# 直接导入发布 Skill 中的 Git hook 安装器
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "jojo-code-guard" / "scripts"))

import install_hook  # noqa: E402


class InstallHookTests(unittest.TestCase):
    """验证安装器只管理自身拥有的 pre-commit。"""

    def _init_repo(self, directory: str) -> Path:
        """初始化一个隔离的 Git 测试仓库。"""
        repo = Path(directory)
        result = subprocess.run(
            ["git", "init", "--quiet"],
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return repo

    @contextlib.contextmanager
    def _isolated_git_config(self, directory: str, **overrides: str):
        """隔离用户 Git 配置，并允许测试显式注入配置来源。"""
        environment = os.environ.copy()
        for name in (
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_KEY_0",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_SYSTEM",
            "GIT_CONFIG_VALUE_0",
        ):
            environment.pop(name, None)
        global_config = Path(directory) / "global.gitconfig"
        global_config.write_text("", encoding="utf-8")
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": str(global_config),
                "GIT_CONFIG_NOSYSTEM": "1",
                **overrides,
            }
        )
        with mock.patch.dict(os.environ, environment, clear=True):
            yield

    def test_install_is_idempotent_for_owned_hook(self) -> None:
        """重复安装自有 hook 不应产生漂移。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = self._init_repo(directory)
            with self._isolated_git_config(directory):
                first = install_hook.install(repo)
                first_content = first.read_bytes()
                second = install_hook.install(repo)

            self.assertEqual(first, second)
            self.assertEqual(second.read_bytes(), first_content)
            self.assertIn(install_hook.MARKER.encode("utf-8"), first_content)

    def test_third_party_hook_is_not_overwritten(self) -> None:
        """已有第三方 pre-commit 时必须拒绝覆盖。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = self._init_repo(directory)
            hooks_dir = repo / ".git" / "hooks"
            pre_commit = hooks_dir / "pre-commit"
            original = b"#!/bin/sh\necho third-party\n"
            pre_commit.write_bytes(original)

            with self._isolated_git_config(directory):
                with self.assertRaises(RuntimeError):
                    install_hook.install(repo)

            self.assertEqual(pre_commit.read_bytes(), original)

    def test_marker_comment_does_not_claim_third_party_hook(self) -> None:
        """第三方注释中碰巧出现 marker 时仍必须拒绝覆盖。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = self._init_repo(directory)
            pre_commit = repo / ".git" / "hooks" / "pre-commit"
            original = (
                b"#!/bin/sh\n"
                b"# documentation: jojo-code-guard-managed-hook:v1\n"
                b"echo third-party\n"
            )
            pre_commit.write_bytes(original)

            with self._isolated_git_config(directory):
                with self.assertRaises(RuntimeError):
                    install_hook.install(repo)

            self.assertEqual(pre_commit.read_bytes(), original)

    def test_owned_hook_refreshes_stale_copied_scripts(self) -> None:
        """自有 wrapper 不变时，过期的复制脚本也必须更新。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = self._init_repo(directory)
            with self._isolated_git_config(directory):
                first = install_hook.install(repo)
                stale = first.parent / "jojo_hook_check.py"
                stale.write_bytes(b"stale\n")
                second = install_hook.install(repo)

            source = Path(install_hook.__file__).resolve().parent / "hook_check.py"
            self.assertEqual(second.read_bytes(), first.read_bytes())
            self.assertEqual(stale.read_bytes(), source.read_bytes())

    def test_system_hooks_path_is_rejected(self) -> None:
        """system 作用域 hooksPath 生效时不得写入默认 .git/hooks。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = self._init_repo(directory)
            system_config = Path(directory) / "system.gitconfig"
            system_config.write_text("[core]\n\thooksPath = system-hooks\n", encoding="utf-8")

            with self._isolated_git_config(
                directory,
                GIT_CONFIG_SYSTEM=str(system_config),
                GIT_CONFIG_NOSYSTEM="0",
            ):
                with self.assertRaisesRegex(RuntimeError, "core.hooksPath"):
                    install_hook.install(repo)

            self.assertFalse((repo / ".git" / "hooks" / "pre-commit").exists())
            self.assertFalse((repo / "system-hooks" / "pre-commit").exists())

    def test_worktree_hooks_path_is_rejected(self) -> None:
        """worktree 作用域 hooksPath 生效时不得写入默认 .git/hooks。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = self._init_repo(directory)
            with self._isolated_git_config(directory):
                subprocess.run(
                    ["git", "config", "extensions.worktreeConfig", "true"],
                    cwd=repo,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "--worktree", "core.hooksPath", ".worktree-hooks"],
                    cwd=repo,
                    check=True,
                )
                with self.assertRaisesRegex(RuntimeError, "core.hooksPath"):
                    install_hook.install(repo)

            self.assertFalse((repo / ".git" / "hooks" / "pre-commit").exists())
            self.assertFalse((repo / ".worktree-hooks" / "pre-commit").exists())

    def test_command_scope_hooks_path_is_rejected(self) -> None:
        """command 作用域 hooksPath 生效时不得写入默认 .git/hooks。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = self._init_repo(directory)
            with self._isolated_git_config(
                directory,
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0="core.hooksPath",
                GIT_CONFIG_VALUE_0="command-hooks",
            ):
                with self.assertRaisesRegex(RuntimeError, "core.hooksPath"):
                    install_hook.install(repo)

            self.assertFalse((repo / ".git" / "hooks" / "pre-commit").exists())
            self.assertFalse((repo / "command-hooks" / "pre-commit").exists())


if __name__ == "__main__":
    unittest.main()

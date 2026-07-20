# Git hook 安装回归测试：验证幂等更新和第三方 hook 保护。

from __future__ import annotations

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

    def test_install_is_idempotent_for_owned_hook(self) -> None:
        """重复安装自有 hook 不应产生漂移。"""
        with tempfile.TemporaryDirectory() as directory:
            repo = self._init_repo(directory)
            with mock.patch.object(install_hook, "_config", return_value=""):
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

            with mock.patch.object(install_hook, "_config", return_value=""):
                with self.assertRaises(RuntimeError):
                    install_hook.install(repo)

            self.assertEqual(pre_commit.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

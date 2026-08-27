"""按变更选测规则的确定性回归测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import select_tests  # noqa: E402


class TestSelectionTests(unittest.TestCase):
    def test_skill_rule_change_selects_semantics_adapter_and_resource_checks(self) -> None:
        selection = select_tests.select_tests(
            ["skills/jojo-code-guard/references/通用行为规则.md"]
        )

        self.assertFalse(selection.full)
        self.assertFalse(selection.cross_platform)
        self.assertEqual(
            selection.modules,
            (
                "tests.test_rule_semantics",
                "tests.test_claude_adapter",
                "tests.test_claude_doctor",
            ),
        )

    def test_doctor_change_selects_core_doctor_and_requires_platform_matrix(self) -> None:
        selection = select_tests.select_tests(
            ["skills/jojo-code-guard/scripts/doctor.py"]
        )

        self.assertFalse(selection.full)
        self.assertTrue(selection.cross_platform)
        self.assertEqual(selection.modules, ("tests.test_claude_doctor",))

    def test_removed_sync_path_fails_closed_to_full_suite(self) -> None:
        selection = select_tests.select_tests(["scripts\\sync_codex_plugin.py"])

        self.assertTrue(selection.full)
        self.assertTrue(selection.cross_platform)

    def test_deleted_test_module_fails_closed_to_full_suite(self) -> None:
        selection = select_tests.select_tests(["tests/test_global_rules.py"])

        self.assertTrue(selection.full)
        self.assertTrue(selection.cross_platform)

    def test_workflow_and_unknown_code_fail_closed_to_full_suite(self) -> None:
        for path in (".github/workflows/test.yml", "scripts/new_shared_helper.py"):
            with self.subTest(path=path):
                selection = select_tests.select_tests([path])
                self.assertTrue(selection.full)
                self.assertTrue(selection.cross_platform)
                self.assertEqual(selection.command(), select_tests.FULL_TEST_COMMAND)

    def test_changed_test_file_selects_itself(self) -> None:
        selection = select_tests.select_tests(["tests/test_guard_core.py"])

        self.assertEqual(selection.modules, ("tests.test_guard_core",))
        self.assertFalse(selection.full)

    def test_shared_test_support_file_requires_full_suite(self) -> None:
        selection = select_tests.select_tests(["tests/helpers.py"])

        self.assertTrue(selection.full)
        self.assertTrue(selection.cross_platform)

    def test_multiple_paths_union_modules_in_stable_order(self) -> None:
        selection = select_tests.select_tests(
            [
                "skills/jojo-code-guard/scripts/install_hook.py",
                "README.md",
                "skills/jojo-code-guard/scripts/install_hook.py",
            ]
        )

        self.assertEqual(
            selection.modules,
            (
                "tests.test_rule_semantics",
                "tests.test_claude_adapter",
                "tests.test_claude_doctor",
                "tests.test_install_hook",
            ),
        )

    def test_json_cli_is_machine_readable(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "select_tests.py"),
                "--path",
                "README.md",
                "--json",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["paths"], ["README.md"])
        self.assertIn("tests.test_rule_semantics", payload["modules"])

    def test_changed_paths_uses_requested_commit_range(self) -> None:
        with mock.patch.object(
            select_tests,
            "_run_git",
            return_value="README.md\0scripts/select_tests.py\0",
        ) as run_git:
            paths = select_tests.changed_paths(base="origin/master", head="HEAD")

        self.assertEqual(paths, ("README.md", "scripts/select_tests.py"))
        run_git.assert_called_once_with(
            [
                "diff",
                "--name-only",
                "-z",
                "--diff-filter=ACDMRTUXB",
                "origin/master",
                "HEAD",
            ]
        )

    def test_changed_paths_includes_untracked_worktree_files(self) -> None:
        with mock.patch.object(
            select_tests,
            "_run_git",
            side_effect=("README.md\0", "AGENTS.md\0"),
        ):
            paths = select_tests.changed_paths()

        self.assertEqual(paths, ("README.md", "AGENTS.md"))

    def test_changed_paths_preserves_chinese_and_newline_names(self) -> None:
        with mock.patch.object(
            select_tests,
            "_run_git",
            return_value="references/中文规则.md\0notes/line\nbreak.md\0",
        ):
            paths = select_tests.changed_paths(base="HEAD^")

        self.assertEqual(
            paths,
            ("references/中文规则.md", "notes/line\nbreak.md"),
        )

    def test_repository_rule_uses_selector_as_floor_and_keeps_main_full(self) -> None:
        rule = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("python scripts/select_tests.py", rule)
        self.assertIn("最低验证集合", rule)
        self.assertIn("master", rule)
        self.assertIn("完整测试", rule)


if __name__ == "__main__":
    unittest.main()

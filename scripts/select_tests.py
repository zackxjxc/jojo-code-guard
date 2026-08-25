#!/usr/bin/env python3
"""根据 Git 变更选择 jojo-code-guard 的最小本地测试集合。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence


ROOT = pathlib.Path(__file__).resolve().parents[1]
FULL_TEST_COMMAND = (
    sys.executable,
    "-B",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
)
TEST_MODULE_ORDER = (
    "tests.test_select_tests",
    "tests.test_rule_semantics",
    "tests.test_claude_adapter",
    "tests.test_claude_doctor",
    "tests.test_global_rules",
    "tests.test_guard_core",
    "tests.test_install_hook",
    "tests.test_sync_transaction_safety",
)


def _configure_output() -> None:
    """让人类摘要与机器 JSON 在 Windows 管道中都稳定使用 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class TestSelection:
    """选测结果；modules 是本地迭代下限，cross_platform 要求合并前跑矩阵。"""

    paths: tuple[str, ...]
    modules: tuple[str, ...]
    full: bool
    cross_platform: bool
    reasons: tuple[str, ...]

    def command(self) -> tuple[str, ...]:
        if self.full:
            return FULL_TEST_COMMAND
        if not self.modules:
            return ()
        return (sys.executable, "-B", "-m", "unittest", *self.modules)


def _normalize_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _test_module_for_path(path: str) -> str | None:
    candidate = pathlib.PurePosixPath(path)
    if len(candidate.parts) != 2 or candidate.parts[0] != "tests":
        return None
    name = candidate.name
    if not name.startswith("test_") or candidate.suffix != ".py":
        return None
    return f"tests.{candidate.stem}"


def _add_reason(reasons: list[str], value: str) -> None:
    if value not in reasons:
        reasons.append(value)


def select_tests(paths: Iterable[str]) -> TestSelection:
    """把变更路径映射到测试模块；未知代码和共享基础设施失败关闭为全量。"""
    normalized_paths = tuple(sorted({path for value in paths if (path := _normalize_path(value))}))
    modules: set[str] = set()
    reasons: list[str] = []
    full = False
    cross_platform = False

    for path in normalized_paths:
        test_module = _test_module_for_path(path)
        if test_module is not None:
            modules.add(test_module)
            _add_reason(reasons, f"测试文件变更：{path}")
            continue

        if path.startswith("tests/"):
            full = True
            cross_platform = True
            _add_reason(reasons, f"共享测试基础设施变更：{path}")
            continue

        if path == "scripts/select_tests.py" or path == "AGENTS.md":
            modules.add("tests.test_select_tests")
            _add_reason(reasons, f"选测规则变更：{path}")
            continue

        if path.startswith(".github/workflows/") or path in {".editorconfig", ".gitattributes"}:
            full = True
            cross_platform = True
            _add_reason(reasons, f"跨平台测试基础设施变更：{path}")
            continue

        if path in {
            "scripts/sync_claude_plugin.py",
            "scripts/sync_codex_plugin.py",
        }:
            modules.update(
                {
                    "tests.test_claude_adapter",
                    "tests.test_sync_transaction_safety",
                }
            )
            cross_platform = True
            _add_reason(reasons, f"插件同步事务变更：{path}")
            continue

        if path == "skills/jojo-code-guard/scripts/doctor.py":
            modules.update(
                {
                    "tests.test_claude_doctor",
                    "tests.test_global_rules",
                }
            )
            cross_platform = True
            _add_reason(reasons, "doctor/全局规则实现变更")
            continue

        if path == "skills/jojo-code-guard/scripts/install_hook.py":
            modules.update(
                {
                    "tests.test_claude_doctor",
                    "tests.test_install_hook",
                }
            )
            cross_platform = True
            _add_reason(reasons, "Hook 安装实现变更")
            continue

        if path in {
            "skills/jojo-code-guard/scripts/check_diff.py",
            "skills/jojo-code-guard/scripts/guard_core.py",
            "skills/jojo-code-guard/scripts/hook_baseline.py",
            "skills/jojo-code-guard/scripts/hook_check.py",
        } or path.startswith("hooks/"):
            modules.update(
                {
                    "tests.test_claude_adapter",
                    "tests.test_guard_core",
                }
            )
            cross_platform = True
            _add_reason(reasons, f"diff/Hook 守护实现变更：{path}")
            continue

        if path.startswith(".claude-plugin/") or path.startswith(".codex-plugin/"):
            modules.update(
                {
                    "tests.test_claude_adapter",
                    "tests.test_claude_doctor",
                }
            )
            _add_reason(reasons, f"插件 manifest 变更：{path}")
            continue

        if (
            path == "README.md"
            or path.startswith("commands/")
            or path.startswith("skills/")
            and (
                path.endswith(".md")
                or "/references/" in path
                or "/agents/" in path
            )
        ):
            modules.update(
                {
                    "tests.test_rule_semantics",
                    "tests.test_claude_adapter",
                    "tests.test_claude_doctor",
                }
            )
            _add_reason(reasons, f"Skill/规则资源变更：{path}")
            continue

        if path.endswith(".md"):
            modules.update(
                {
                    "tests.test_rule_semantics",
                    "tests.test_claude_adapter",
                }
            )
            _add_reason(reasons, f"文档变更：{path}")
            continue

        full = True
        cross_platform = True
        _add_reason(reasons, f"未知或共享代码路径，保守全测：{path}")

    ordered_modules = tuple(module for module in TEST_MODULE_ORDER if module in modules)
    extra_modules = tuple(sorted(modules.difference(TEST_MODULE_ORDER)))
    return TestSelection(
        paths=normalized_paths,
        modules=ordered_modules + extra_modules,
        full=full,
        cross_platform=cross_platform,
        reasons=tuple(reasons),
    )


def _run_git(arguments: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Git 变更枚举失败")
    return result.stdout


def changed_paths(*, base: str | None = None, head: str = "HEAD") -> tuple[str, ...]:
    """读取提交区间或当前工作区的变更路径，并包含未跟踪文件。"""
    if base is not None:
        output = _run_git(
            ["diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", base, head]
        )
        return tuple(path for path in output.split("\0") if path)
    tracked = _run_git(["diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", "HEAD"])
    untracked = _run_git(["ls-files", "--others", "--exclude-standard", "-z"])
    return tuple(path for path in (tracked + untracked).split("\0") if path)


def _format_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def _print_human(selection: TestSelection) -> None:
    scope = "full" if selection.full else "targeted" if selection.modules else "none"
    print(f"scope: {scope}")
    print(f"cross-platform: {'yes' if selection.cross_platform else 'no'}")
    print("paths:")
    for path in selection.paths:
        print(f"  - {path}")
    print("tests:")
    if selection.full:
        print("  - all")
    else:
        for module in selection.modules:
            print(f"  - {module}")
    print("reasons:")
    for reason in selection.reasons:
        print(f"  - {reason}")
    command = selection.command()
    print("command: " + (_format_command(command) if command else "none"))


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="与该 Git revision 比较")
    parser.add_argument("--head", default="HEAD", help="区间终点，默认 HEAD")
    parser.add_argument("--path", action="append", default=[], help="直接提供变更路径，可重复")
    parser.add_argument("--json", action="store_true", help="输出 JSON 计划")
    parser.add_argument("--run", action="store_true", help="执行选出的本地测试")
    parser.add_argument("--full", action="store_true", help="无条件执行完整本地套件")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _configure_output()
    arguments = _parse_arguments(argv)
    paths = tuple(arguments.path) or changed_paths(base=arguments.base, head=arguments.head)
    selection = select_tests(paths)
    if arguments.full and not selection.full:
        selection = TestSelection(
            paths=selection.paths,
            modules=selection.modules,
            full=True,
            cross_platform=True,
            reasons=selection.reasons + ("命令行强制完整测试",),
        )
    if arguments.json:
        payload = asdict(selection)
        payload["command"] = selection.command()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(selection)
    command = selection.command()
    if not arguments.run or not command:
        return 0
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

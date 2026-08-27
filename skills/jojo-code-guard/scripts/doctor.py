#!/usr/bin/env python3
"""只读诊断 jojo-code-guard 核心运行时和仓库保护；可选补齐缺失配置。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import re
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass

from guard_core import find_repo


LEGACY_SECTION = re.compile(
    r"^##[ \t]+jojo-code-guard 自动加载(?:（必须严格遵守）)?[ \t]*$",
    re.MULTILINE,
)
MANAGED_HOOK_MARKER = "jojo-code-guard-managed-hook:v1"


@dataclass(frozen=True)
class Finding:
    """保存一条核心诊断。"""

    level: str
    area: str
    item: str
    message: str


def _configure_output() -> None:
    """统一控制台和 JSON 的 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run_git(repo: pathlib.Path, arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    """执行只读或明确授权的仓库 local Git 命令。"""
    return subprocess.run(
        ["git", *arguments],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git_value(repo: pathlib.Path, key: str) -> str:
    """读取最终生效的 Git 配置值。"""
    result = _run_git(repo, ["config", "--get", key])
    return result.stdout.decode("utf-8", errors="replace").strip() if result.returncode == 0 else ""


def _read_utf8(path: pathlib.Path) -> str | None:
    """严格读取小型 UTF-8 配置；格式异常时返回 None。"""
    try:
        data = path.read_bytes()
        if len(data) > 1024 * 1024:
            return None
        return data.decode("utf-8-sig", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None


def _plugin_root() -> pathlib.Path:
    """定位当前 doctor 随附的插件根目录。"""
    return pathlib.Path(__file__).resolve().parents[3]


def _check_runtime(findings: list[Finding]) -> None:
    """只检查自动守护真正依赖的 Python 与 Git。"""
    findings.append(
        Finding(
            "OK" if sys.version_info >= (3, 9) else "BLOCKED",
            "运行时",
            "Python",
            platform.python_version(),
        )
    )
    result = subprocess.run(
        ["git", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    detail = (result.stdout or result.stderr).decode("utf-8", errors="replace").strip()
    findings.append(Finding("OK" if result.returncode == 0 else "BLOCKED", "运行时", "Git", detail or "不可用"))


def _check_repo(findings: list[Finding], repo: pathlib.Path) -> None:
    """检查核心仓库规则和工作区状态，不评价无关构建工具。"""
    for name in (".editorconfig", ".gitattributes", ".gitignore"):
        path = repo / name
        findings.append(
            Finding(
                "OK" if path.is_file() else "ACTION_REQUIRED",
                "仓库",
                name,
                "存在" if path.is_file() else "缺失；需要时可用 --repair --yes 创建保守模板",
            )
        )
    agents = repo / "AGENTS.md"
    findings.append(
        Finding(
            "OK",
            "仓库",
            "AGENTS.md",
            "存在，由客户端原生发现" if agents.is_file() else "未提供；插件不会自动创建",
        )
    )
    status = _run_git(repo, ["-c", "core.quotepath=false", "status", "--short"])
    if status.returncode != 0:
        detail = (status.stderr or status.stdout).decode("utf-8", errors="replace").strip()
        findings.append(Finding("BLOCKED", "仓库", "工作区", detail or "git status 失败"))
    else:
        dirty = status.stdout.decode("utf-8", errors="replace").strip()
        findings.append(Finding("WARNING" if dirty else "OK", "仓库", "工作区", "存在未提交修改" if dirty else "干净"))

    autocrlf = _git_value(repo, "core.autocrlf").casefold()
    findings.append(
        Finding(
            "OK" if autocrlf in {"", "false", "0"} else "WARNING",
            "Git",
            "core.autocrlf",
            autocrlf or "未显式设置",
        )
    )
    core_eol = _git_value(repo, "core.eol")
    if core_eol:
        findings.append(Finding("WARNING", "Git", "core.eol", f"当前为 {core_eol}；确认与 .gitattributes 一致"))
    if os.name == "nt":
        filemode = _git_value(repo, "core.filemode").casefold()
        findings.append(
            Finding(
                "OK" if filemode in {"", "false", "0"} else "WARNING",
                "Git",
                "core.filemode",
                filemode or "未显式设置",
            )
        )


def _read_json(path: pathlib.Path) -> dict[str, object] | None:
    """读取插件 JSON 对象。"""
    content = _read_utf8(path)
    if content is None:
        return None
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _hook_commands(manifest: dict[str, object], event: str) -> list[str]:
    """枚举指定事件的命令处理器。"""
    hooks = manifest.get("hooks")
    if not isinstance(hooks, dict):
        return []
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return []
    commands: list[str] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for handler in group["hooks"]:
            if isinstance(handler, dict):
                for key in ("command", "commandWindows"):
                    value = handler.get(key)
                    if isinstance(value, str):
                        commands.append(value)
    return commands


def _has_compact_recovery_hook(manifest: dict[str, object]) -> bool:
    """确认 SessionStart 只在 compact 后发出短恢复提示。"""
    hooks = manifest.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get("SessionStart")
    if not isinstance(groups, list) or len(groups) != 1:
        return False
    group = groups[0]
    if not isinstance(group, dict) or group.get("matcher") != "compact":
        return False
    handlers = group.get("hooks")
    if not isinstance(handlers, list) or len(handlers) != 1 or not isinstance(handlers[0], dict):
        return False
    handler = handlers[0]
    commands = [handler.get(key) for key in ("command", "commandWindows")]
    return (
        all(isinstance(command, str) and "lifecycle.py" in command for command in commands)
        and handler.get("additionalContextLimit") == 300
    )


def _check_plugin(findings: list[Finding]) -> None:
    """校验最小插件清单与生命周期契约。"""
    root = _plugin_root()
    manifest = _read_json(root / ".codex-plugin" / "plugin.json")
    version = manifest.get("version") if manifest else None
    findings.append(
        Finding(
            "OK" if isinstance(version, str) and version else "BLOCKED",
            "插件",
            "Version",
            str(version or "manifest 缺失或无效"),
        )
    )
    hook_path = root / "hooks" / "hooks.json"
    hooks = _read_json(hook_path)
    if hooks is None:
        findings.append(Finding("BLOCKED", "插件", "Hook", f"无法读取 {hook_path}"))
        return
    session_start_ok = _has_compact_recovery_hook(hooks)
    findings.append(
        Finding(
            "OK" if session_start_ok else "BLOCKED",
            "插件",
            "SessionStart",
            "只在 compact 后发送短恢复提示" if session_start_ok else "必须只匹配 compact 且使用 lifecycle.py",
        )
    )
    configured = isinstance(hooks.get("hooks"), dict) and "UserPromptSubmit" in hooks["hooks"]
    findings.append(
        Finding(
            "BLOCKED" if configured else "OK",
            "插件",
            "UserPromptSubmit",
            "不应注册；会造成每条消息扫描" if configured else "未注册",
        )
    )
    for event in ("PreToolUse", "PostToolUse", "Stop"):
        commands = _hook_commands(hooks, event)
        current = commands and all("lifecycle.py" in command for command in commands)
        findings.append(
            Finding(
                "OK" if current else "BLOCKED",
                "插件",
                event,
                "使用单一 Python 生命周期入口" if current else "缺失或仍使用旧启动链",
            )
        )


def _check_pre_commit(findings: list[Finding], repo: pathlib.Path) -> None:
    """报告可选 pre-commit，不把缺失视为仓库错误。"""
    result = _run_git(repo, ["rev-parse", "--git-path", "hooks/pre-commit"])
    if result.returncode != 0:
        findings.append(Finding("WARNING", "Git Hook", "pre-commit", "无法定位"))
        return
    raw = result.stdout.rstrip(b"\r\n")
    path = pathlib.Path(os.fsdecode(raw))
    if not path.is_absolute():
        path = repo / path
    if not path.is_file():
        findings.append(Finding("OK", "Git Hook", "pre-commit", "未安装；这是可选门禁"))
        return
    content = _read_utf8(path)
    level = "OK" if content and MANAGED_HOOK_MARKER in content else "WARNING"
    message = "jojo 自有 Hook" if level == "OK" else "存在第三方 Hook，安装器不会覆盖"
    findings.append(Finding(level, "Git Hook", "pre-commit", message))


def _legacy_candidates() -> list[pathlib.Path]:
    """返回用户规则、旧版手工 Hook 和对应客户端配置。"""
    home = pathlib.Path.home()
    codex_home = pathlib.Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
    return [
        codex_home / "AGENTS.md",
        home / ".claude" / "CLAUDE.md",
        codex_home / "hooks" / "session-start",
        home / ".claude" / "hooks" / "session-start",
        codex_home / "hooks.json",
        home / ".claude" / "settings.json",
    ]


def _check_legacy_loading(findings: list[Finding]) -> None:
    """只报告 0.2.x 遗留的重复加载来源，不改写用户文件。"""
    for path in _legacy_candidates():
        content = _read_utf8(path)
        if content is None:
            continue
        if path.name in {"AGENTS.md", "CLAUDE.md"}:
            matched = LEGACY_SECTION.search(content) is not None
            item = "旧版自动加载节"
        elif path.name in {"hooks.json", "settings.json"}:
            normalized = re.sub(r"/+", "/", content.replace("\\", "/").casefold())
            matched = "sessionstart" in normalized and "/hooks/session-start" in normalized
            item = "旧版手工 SessionStart 配置"
        else:
            matched = "jojo-code-guard" in content or "JOJO_CODE_GUARD" in content
            item = "旧版手工 SessionStart"
        if matched:
            findings.append(
                Finding(
                    "ACTION_REQUIRED",
                    "迁移",
                    item,
                    f"审阅后移除精确 jojo 来源，保留其他用户规则：{path}",
                )
            )


def _template(name: str) -> bytes:
    """返回只用于缺失文件的保守模板。"""
    templates = {
        ".editorconfig": (
            "root = true\n\n[*]\ncharset = utf-8\nend_of_line = lf\n"
            "insert_final_newline = true\ntrim_trailing_whitespace = false\n\n"
            "[*.{bat,cmd}]\nend_of_line = crlf\n"
        ),
        ".gitattributes": "* -text\n\n*.bat text eol=crlf\n*.cmd text eol=crlf\n",
        ".gitignore": "/.vscode/*\n!/.vscode/settings.json\n",
    }
    return templates[name].encode("utf-8")


def _create_missing(path: pathlib.Path, data: bytes) -> bool:
    """用独占创建写入缺失普通文件，不覆盖任何现有目录项。"""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"配置路径已存在但不是普通文件：{path}")
        return False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return True


def repair_repo(repo: pathlib.Path) -> list[str]:
    """只创建缺失配置，并设置仓库 local Git 保护项。"""
    changed: list[str] = []
    for name in (".editorconfig", ".gitattributes", ".gitignore"):
        path = repo / name
        if path.is_symlink():
            raise RuntimeError(f"拒绝写入链接型配置：{path}")
        if _create_missing(path, _template(name)):
            changed.append(name)
    settings = [("core.autocrlf", "false"), ("core.safecrlf", "warn")]
    if os.name == "nt":
        settings.append(("core.filemode", "false"))
    for key, value in settings:
        if _git_value(repo, key).casefold() == value:
            continue
        result = _run_git(repo, ["config", "--local", key, value])
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"无法设置 {key}：{detail}")
        changed.append(f"git local {key}={value}")
    return changed


def _parse_arguments(arguments: list[str] | None) -> argparse.Namespace:
    """解析只读诊断和两个显式写入入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Git 工作树内的路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--repair", action="store_true", help="补齐缺失仓库配置和 local Git 保护项")
    parser.add_argument("--install-hook", action="store_true", help="安装可选仓库私有 pre-commit")
    parser.add_argument("--yes", action="store_true", help="确认执行 repair 或 Hook 安装")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """执行核心诊断或用户确认后的最小修复。"""
    _configure_output()
    options = _parse_arguments(arguments)
    findings: list[Finding] = []
    _check_runtime(findings)
    try:
        repo = find_repo(options.repo)
    except RuntimeError as error:
        repo = None
        findings.append(Finding("BLOCKED", "仓库", "当前目录", str(error)))

    if repo is not None:
        _check_repo(findings, repo)
        _check_pre_commit(findings, repo)
    _check_plugin(findings)
    _check_legacy_loading(findings)

    wants_write = options.repair or options.install_hook
    if wants_write and not options.yes:
        findings.append(
            Finding(
                "ACTION_REQUIRED",
                "修复",
                "确认",
                "将写入仓库配置或 .git/hooks；审阅报告后添加 --yes",
            )
        )
    elif wants_write:
        try:
            if repo is None:
                raise RuntimeError("写入前必须位于 Git 工作树")
            if options.repair:
                changed = repair_repo(repo)
                findings.append(Finding("OK", "修复", "仓库", "、".join(changed) if changed else "无需修改"))
            if options.install_hook:
                from install_hook import install

                findings.append(Finding("OK", "修复", "pre-commit", str(install(repo))))
        except (OSError, RuntimeError) as error:
            findings.append(Finding("BLOCKED", "修复", "执行", str(error)))

    if options.json:
        print(json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2))
    else:
        print(f"啾啾代码守护核心诊断：{repo or pathlib.Path(options.repo).resolve()}")
        for item in findings:
            print(f"{item.level:<15} {item.area:<8} {item.item}：{item.message}")
        print("\n说明：默认只读；不会联网、安装设备工具或改写用户级规则。")
    return 1 if any(item.level == "BLOCKED" for item in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())

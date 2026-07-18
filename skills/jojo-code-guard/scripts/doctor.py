#!/usr/bin/env python3
"""啾啾代码守护：只读诊断设备、Git 和仓库；可选地补齐缺失保护设施。"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from guard_core import find_repo, run_git


def _configure_output() -> None:
    """在 Windows 控制台和 Git hook 中统一使用 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class Finding:
    """保存一条诊断结果。"""

    level: str
    area: str
    item: str
    message: str


def _run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """执行只读外部命令并安全解码输出。"""
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        return 127, str(error)
    output = result.stdout or result.stderr
    return result.returncode, output.decode("utf-8", errors="replace").strip()


def _tool(findings: list[Finding], name: str, candidates: list[str], version_command: list[str] | None = None) -> None:
    """检查命令是否可用并记录版本摘要。"""
    executable = next((item for item in candidates if shutil.which(item)), None)
    if not executable:
        findings.append(Finding("WARNING", "设备", name, "未安装或不在 PATH 中"))
        return
    version = ""
    version_code = 0
    if version_command:
        command = list(version_command)
        if command and command[0] in candidates:
            command[0] = executable
        version_code, version = _run(command)
    if version_code != 0:
        findings.append(Finding("WARNING", "设备", name, f"{executable} 可定位，但版本命令失败：{version}"))
    else:
        findings.append(Finding("OK", "设备", name, f"{executable}{': ' + version.splitlines()[0] if version else ''}"))


def _config(repo: Path, scope: str, key: str) -> str:
    """读取 Git 配置值，不把缺失值当成错误。"""
    _, value = _run(["git", "config", scope, "--get", key], repo)
    return value


def _check_git(findings: list[Finding], repo: Path) -> None:
    """检查 Git 版本、全局/本地文本相关配置。"""
    _, version = _run(["git", "--version"])
    findings.append(Finding("OK", "Git", "版本", version or "无法读取版本"))
    keys = ("core.autocrlf", "core.eol", "core.safecrlf", "core.attributesfile", "core.hooksPath")
    for key in keys:
        local = _config(repo, "--local", key)
        global_value = _config(repo, "--global", key)
        if local:
            findings.append(Finding("OK", "Git", f"local {key}", local))
        elif global_value:
            level = "WARNING" if key in {"core.autocrlf", "core.eol", "core.attributesfile", "core.hooksPath"} else "OK"
            findings.append(Finding(level, "Git", f"global {key}", global_value))
        elif key == "core.safecrlf":
            findings.append(Finding("WARNING", "Git", key, "未设置；老项目建议在仓库 local 配置为 warn"))
        else:
            findings.append(Finding("OK", "Git", key, "未设置"))
    if _config(repo, "--local", "core.autocrlf").lower() != "false":
        findings.append(Finding("ACTION_REQUIRED", "Git", "core.autocrlf", "建议使用 git config --local core.autocrlf false"))


def _read_utf8(path: Path) -> str | None:
    """严格读取规则文件；失败时返回 None。"""
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None


def _check_editorconfig(findings: list[Finding], repo: Path) -> None:
    """检查 EditorConfig 是否会把老文件强制改写。"""
    path = repo / ".editorconfig"
    if not path.exists():
        findings.append(Finding("ACTION_REQUIRED", "仓库", ".editorconfig", "缺失；建议创建保守的 legacy 规则"))
        return
    content = _read_utf8(path)
    if content is None:
        findings.append(Finding("BLOCKED", "仓库", ".editorconfig", "不是可严格读取的 UTF-8 文件"))
        return
    dangerous = []
    for line in content.splitlines():
        stripped = line.strip().lower().replace(" ", "")
        key, separator, value = stripped.partition("=")
        if separator and key in {"charset", "end_of_line"} and value not in {"unset", "auto"}:
            dangerous.append(line.strip())
    if dangerous:
        findings.append(Finding("WARNING", "仓库", ".editorconfig", "包含可能改写老文件的全局编码/换行规则：" + "; ".join(dangerous)))
    else:
        findings.append(Finding("OK", "仓库", ".editorconfig", "存在且未发现全局强制编码/换行声明"))


def _check_attributes(findings: list[Finding], repo: Path) -> None:
    """检查 Git 属性是否可能进行隐式换行或编码转换。"""
    path = repo / ".gitattributes"
    if not path.exists():
        findings.append(Finding("ACTION_REQUIRED", "仓库", ".gitattributes", "缺失；老项目建议至少加入 * -text"))
        return
    content = _read_utf8(path)
    if content is None:
        findings.append(Finding("BLOCKED", "仓库", ".gitattributes", "不是可严格读取的 UTF-8 文件"))
        return
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    risky = [line for line in lines if any(token in line.split() for token in ("text", "text=auto", "eol=lf", "eol=crlf", "working-tree-encoding=UTF-8"))]
    if any(line.startswith("* -text") for line in lines):
        findings.append(Finding("OK", "仓库", ".gitattributes", "已设置 * -text，默认不会替换老文件换行"))
    elif risky:
        findings.append(Finding("WARNING", "仓库", ".gitattributes", "存在可能规范化老文件的规则：" + "; ".join(risky[:6])))
    else:
        findings.append(Finding("WARNING", "仓库", ".gitattributes", "存在但未声明老项目的字节保真策略"))


def _check_hook(findings: list[Finding], repo: Path) -> None:
    """检查有效 hooks 路径、pre-commit 和可选 pre-commit 框架。"""
    _, hooks_path = _run(["git", "rev-parse", "--git-path", "hooks"], repo)
    hook = Path(hooks_path) if Path(hooks_path).is_absolute() else (repo / hooks_path).resolve()
    pre_commit = hook / "pre-commit"
    if not pre_commit.exists():
        findings.append(Finding("ACTION_REQUIRED", "Git hook", str(pre_commit), "缺少 pre-commit；可安装仓库私有守护 hook"))
    else:
        hook_content = _read_utf8(pre_commit)
        if hook_content is not None and "jojo-code-guard-managed-hook" in hook_content:
            findings.append(Finding("OK", "Git hook", str(pre_commit), "已安装啾啾代码守护 hook"))
        else:
            findings.append(Finding("WARNING", "Git hook", str(pre_commit), "已有其他 hook，未验证是否调用编码检查；不会覆盖"))
    if (repo / ".pre-commit-config.yaml").exists():
        if shutil.which("pre-commit"):
            findings.append(Finding("OK", "Git hook", "pre-commit 框架", "配置和命令均存在"))
        else:
            findings.append(Finding("WARNING", "Git hook", "pre-commit 框架", "存在配置但命令未安装"))


def _find_claude_home() -> Path:
    """定位 Claude Code 用户目录。"""
    return Path.home() / ".claude"


def _check_claude_hooks(findings: list[Finding]) -> None:
    """检查 Claude Code SessionStart hook 是否已注册。

    检测顺序：~/.claude/hooks/session-start 文件、settings.json 中的 hooks 配置、已安装插件。
    任一命中即视为已注册。
    """
    claude_home = _find_claude_home()
    hooks_dir = claude_home / "hooks"
    session_start = hooks_dir / "session-start"
    settings_path = claude_home / "settings.json"

    # 直接放在 ~/.claude/hooks/ 下的 hook 文件
    if session_start.exists():
        content = _read_utf8(session_start)
        if content and "jojo-code-guard" in content:
            findings.append(Finding("OK", "Claude", "SessionStart hook", str(session_start)))
            return
        else:
            findings.append(
                Finding(
                    "WARNING",
                    "Claude",
                    "SessionStart hook",
                    f"存在但非啾啾代码守护 hook：{session_start}",
                )
            )
            return

    # settings.json 中的 hooks 配置
    if settings_path.exists():
        settings_content = _read_utf8(settings_path)
        if settings_content:
            try:
                settings = json.loads(_strip_jsonc_comments(settings_content))
            except json.JSONDecodeError:
                settings = None
            if isinstance(settings, dict):
                hooks_cfg = settings.get("hooks", {})
                if isinstance(hooks_cfg, dict):
                    session_start_cfgs = hooks_cfg.get("SessionStart")
                    if session_start_cfgs:
                        # 检查配置中是否包含 jojo-code-guard 相关命令
                        for cfg in session_start_cfgs if isinstance(session_start_cfgs, list) else [session_start_cfgs]:
                            cmd = ""
                            if isinstance(cfg, dict):
                                sub_hooks = cfg.get("hooks", [])
                                if isinstance(sub_hooks, list) and sub_hooks:
                                    cmd = sub_hooks[0].get("command", "") if isinstance(sub_hooks[0], dict) else ""
                            elif isinstance(cfg, str):
                                cmd = cfg
                            if "jojo-code-guard" in cmd or "session-start" in cmd:
                                findings.append(
                                    Finding("OK", "Claude", "settings.json hooks", "SessionStart 已配置")
                                )
                                return
                        # 有 SessionStart 但可能不是 jojo-code-guard
                        findings.append(
                            Finding("WARNING", "Claude", "settings.json hooks", "SessionStart 已配置但未识别为啾啾代码守护")
                        )
                        return

    # 检查插件目录
    plugins_data = claude_home / "plugins" / "data"
    if plugins_data.exists():
        for item in plugins_data.iterdir():
            if item.is_dir() and "jojo-code-guard" in item.name.lower():
                findings.append(Finding("OK", "Claude", "Plugin", f"已安装：{item.name}"))
                return

    findings.append(
        Finding(
            "ACTION_REQUIRED",
            "Claude",
            "SessionStart hook",
            "缺失；自动加载不会生效。在 jojo-code-guard 仓库中运行 doctor.py --repair --yes 可自动安装",
        )
    )


def _repair_claude_hook(repo: Path) -> str:
    """安装 Claude Code SessionStart hook 到 ~/.claude/hooks/ 并在 settings.json 注册。

    优先从当前仓库的 hooks/ 目录复制脚本；若找不到则从当前脚本所在目录查找。
    不会覆盖已有的非 jojo-code-guard hook。
    """
    claude_home = _find_claude_home()
    hooks_dir = claude_home / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # 定位 hook 源文件目录
    source_dir = None
    candidates = [
        repo / "hooks" if repo else None,
        Path(__file__).resolve().parent.parent / "hooks",  # skill 目录下的 hooks
        Path(__file__).resolve().parent,  # scripts 目录（可能包含 hook 脚本）
    ]
    for candidate in candidates:
        if candidate and (candidate / "session-start").exists():
            source_dir = candidate
            break

    if source_dir is None:
        raise RuntimeError(
            "找不到 hook 源文件（session-start）。"
            "请确保在 jojo-code-guard 仓库中运行，或手动将 hooks/ 下的文件复制到 ~/.claude/hooks/"
        )

    # 检查是否已有非 jojo-code-guard 的 hook
    existing_session_start = hooks_dir / "session-start"
    if existing_session_start.exists():
        existing_content = _read_utf8(existing_session_start)
        if existing_content and "jojo-code-guard" not in existing_content:
            raise RuntimeError(
                f"已有第三方 SessionStart hook：{existing_session_start}，未覆盖。请手动合并后再试"
            )

    # 复制 hook 脚本
    copied = []
    for name in ("session-start",):
        src = source_dir / name
        dst = hooks_dir / name
        if src.exists():
            shutil.copy2(str(src), str(dst))
            copied.append(str(dst))
            # Unix 可执行权限
            if platform.system() != "Windows":
                dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        else:
            raise RuntimeError(f"hook 源文件缺失：{src}")

    # 同时复制 run-hook.cmd（Windows 需要）
    run_hook_src = source_dir / "run-hook.cmd"
    if run_hook_src.exists():
        shutil.copy2(str(run_hook_src), str(hooks_dir / "run-hook.cmd"))
        copied.append(str(hooks_dir / "run-hook.cmd"))

    # 在 settings.json 中注册 hook
    settings_path = claude_home / "settings.json"
    if settings_path.exists():
        raw = settings_path.read_text(encoding="utf-8")
        try:
            settings_data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                settings_data = json.loads(_strip_jsonc_comments(raw))
            except json.JSONDecodeError:
                raise RuntimeError(f"无法解析 {settings_path}，请手动配置 hooks")
        if not isinstance(settings_data, dict):
            raise RuntimeError(f"{settings_path} 内容异常，请手动配置 hooks")
    else:
        settings_data = {}

    # 构造 hook 命令
    session_start_abs = hooks_dir / "session-start"
    if platform.system() == "Windows":
        # Windows：使用 Git Bash 运行 hook 脚本
        hook_command = f'bash "{session_start_abs.as_posix()}"'
    else:
        hook_command = f'bash "{session_start_abs.as_posix()}"'

    existing_hooks = settings_data.get("hooks", {})
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}

    # 检查是否已有 SessionStart 配置
    session_start_configs = existing_hooks.get("SessionStart")
    if session_start_configs:
        # 已有配置，检查是否需要追加
        if isinstance(session_start_configs, list):
            has_jojo = any(
                isinstance(cfg, dict)
                and any(
                    "jojo-code-guard" in h.get("command", "")
                    for h in (cfg.get("hooks", []) if isinstance(cfg.get("hooks"), list) else [])
                )
                for cfg in session_start_configs
            )
            if not has_jojo:
                # 追加 jojo-code-guard 的 hook 配置
                session_start_configs.append(
                    {
                        "matcher": "startup|resume|clear|compact",
                        "hooks": [
                            {"type": "command", "command": hook_command, "async": False}
                        ],
                    }
                )
        elif isinstance(session_start_configs, str) and "jojo-code-guard" not in session_start_configs:
            # 单个命令，追加
            existing_hooks["SessionStart"] = [
                {"matcher": "", "hooks": [{"type": "command", "command": session_start_configs}]},
                {
                    "matcher": "startup|resume|clear|compact",
                    "hooks": [
                        {"type": "command", "command": hook_command, "async": False}
                    ],
                },
            ]
    else:
        existing_hooks["SessionStart"] = [
            {
                "matcher": "startup|resume|clear|compact",
                "hooks": [
                    {"type": "command", "command": hook_command, "async": False}
                ],
            }
        ]

    settings_data["hooks"] = existing_hooks
    new_content = json.dumps(settings_data, ensure_ascii=False, indent=2)
    settings_path.write_text(new_content + "\n", encoding="utf-8")
    copied.append(f"{settings_path} (已更新 hooks 配置)")

    return "已安装：" + ", ".join(copied)


def _check_vscode_settings(findings: list[Finding], repo: Path) -> None:
    """检查 VS Code 设置是否可能覆盖老文件的编码、换行或格式。"""
    path = repo / ".vscode" / "settings.json"
    item = ".vscode/settings.json"
    if not path.exists():
        findings.append(Finding("WARNING", "仓库", item, "未提供编辑器级保护；这是可选文件"))
        return
    content = _read_utf8(path)
    if content is None:
        findings.append(
            Finding("BLOCKED", "仓库", item, "不是可严格读取的 UTF-8/JSONC 文件")
        )
        return

    tracked_code, tracked_output = _run(["git", "ls-files", "--error-unmatch", "--", item], repo)
    ignored_code, ignored_output = _run(["git", "check-ignore", "--no-index", "--", item], repo)
    tracked = tracked_code == 0 and bool(tracked_output)
    ignored = ignored_code == 0 and bool(ignored_output)
    if tracked:
        message = "文件已纳入 Git 跟踪；只应保存项目级、无机器路径的设置"
        if ignored:
            message += "（当前 .gitignore 仍匹配该路径，但已跟踪文件不会因此消失）"
        findings.append(Finding("OK", "仓库", item, message))
    elif ignored:
        findings.append(
            Finding("WARNING", "仓库", item, "文件存在但被 .gitignore 忽略；仅本机生效，团队共享需显式加入 Git")
        )
    else:
        findings.append(Finding("WARNING", "仓库", item, "文件未纳入 Git 跟踪；仅本机生效，是否共享由团队决定"))

    jsonc = _strip_jsonc_comments(content)
    try:
        settings = json.loads(jsonc)
    except json.JSONDecodeError as error:
        findings.append(Finding("BLOCKED", "仓库", item, f"JSONC 无法解析，未能可靠检查设置：{error.msg}"))
        return
    if not isinstance(settings, dict):
        findings.append(Finding("BLOCKED", "仓库", item, "顶层内容必须是 JSON 对象"))
        return
    findings_added = False
    eol_values = []
    encoding_values = []
    auto_guess = False
    format_on_save = False
    code_actions_on_save = False
    insert_final_newline = False
    trim_trailing_whitespace = False
    for key, value in _iter_setting_values(settings):
        if key == "files.eol" and isinstance(value, str):
            eol_values.append(value)
        elif key == "files.encoding" and isinstance(value, str):
            encoding_values.append(value)
        elif key == "files.autoGuessEncoding" and value is True:
            auto_guess = True
        elif key in {"formatOnSave", "editor.formatOnSave"} and (value is True or value == "modifications"):
            format_on_save = True
        elif key == "editor.codeActionsOnSave" and value:
            code_actions_on_save = True
        elif key == "files.insertFinalNewline" and value is True:
            insert_final_newline = True
        elif key == "files.trimTrailingWhitespace" and value is True:
            trim_trailing_whitespace = True
    if eol_values:
        findings.append(
            Finding(
                "WARNING",
                "仓库",
                item,
                "存在 files.eol 设置（"
                + ", ".join(json.dumps(value, ensure_ascii=False) for value in eol_values)
                + "），老文件可能被保存为统一换行",
            )
        )
        findings_added = True
    if encoding_values:
        findings.append(
            Finding(
                "WARNING",
                "仓库",
                item,
                "存在 files.encoding 设置（"
                + ", ".join(json.dumps(value, ensure_ascii=False) for value in encoding_values)
                + "），请确认不会覆盖旧文件编码",
            )
        )
        findings_added = True
    if format_on_save:
        findings.append(
            Finding("WARNING", "仓库", item, "发现 formatOnSave=true，老项目可能产生整文件 diff")
        )
        findings_added = True
    if code_actions_on_save:
        findings.append(
            Finding("WARNING", "仓库", item, "发现 codeActionsOnSave 自动执行设置，可能改写无关代码")
        )
        findings_added = True
    if auto_guess:
        findings.append(Finding("OK", "仓库", item, "启用 autoGuessEncoding，有利于打开旧编码文件"))
        findings_added = True
    if insert_final_newline or trim_trailing_whitespace:
        enabled = []
        if insert_final_newline:
            enabled.append("files.insertFinalNewline")
        if trim_trailing_whitespace:
            enabled.append("files.trimTrailingWhitespace")
        findings.append(
            Finding(
                "WARNING",
                "仓库",
                item,
                "发现保存时自动改写设置（" + ", ".join(enabled) + "），老文件可能产生无关 diff",
            )
        )
        findings_added = True
    if not findings_added:
        findings.append(Finding("OK", "仓库", item, "存在且未发现明显自动改写设置"))


def _strip_jsonc_comments(content: str) -> str:
    """移除 JSONC 注释和尾随逗号，同时保留字符串内容。"""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif char == "/" and next_char == "/":
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                index += 1
        elif char == "/" and next_char == "*":
            output.append(" ")
            index += 2
            while index + 1 < len(content) and content[index:index + 2] != "*/":
                if content[index] in "\r\n":
                    output.append(content[index])
                index += 1
            index += 2 if index + 1 <= len(content) else 0
        else:
            output.append(char)
            index += 1

    without_comments = "".join(output)
    output = []
    in_string = False
    escaped = False
    index = 0
    while index < len(without_comments):
        char = without_comments[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(without_comments) and without_comments[lookahead].isspace():
                lookahead += 1
            if lookahead < len(without_comments) and without_comments[lookahead] in "}]":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _iter_setting_values(settings: dict[str, object]):
    """遍历顶层和语言作用域内的 VS Code 设置。"""
    for key, value in settings.items():
        yield key, value
        if isinstance(value, dict):
            yield from _iter_setting_values(value)


def _check_repo(findings: list[Finding], repo: Path) -> None:
    """检查仓库规则文件、状态和潜在格式化设置。"""
    for name in ("AGENTS.md", ".gitignore"):
        findings.append(
            Finding("OK" if (repo / name).exists() else "ACTION_REQUIRED", "仓库", name, "存在" if (repo / name).exists() else "缺失")
        )
    _check_editorconfig(findings, repo)
    _check_attributes(findings, repo)
    _check_vscode_settings(findings, repo)
    _check_hook(findings, repo)
    status = run_git(repo, ["status", "--short"], check=False).decode("utf-8", errors="replace").strip()
    head_check = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if head_check.returncode != 0:
        findings.append(Finding("WARNING", "仓库", "Git 基线", "尚无首个提交；首次提交按老项目基线保留现有文件字节"))
    if status:
        findings.append(Finding("WARNING", "工作区", "未提交修改", "存在；修复配置前不要覆盖这些修改"))
    else:
        findings.append(Finding("OK", "工作区", "未提交修改", "干净"))


def _template(name: str) -> bytes:
    """返回只用于缺失文件的保守模板。"""
    templates = {
        "AGENTS.md": """# 项目代码守护规则\n\n- 现有文件保持原始编码、BOM 和换行，不自动全量转码。\n- 新增 C/C++ 文件使用 UTF-8 无 BOM + LF。\n- 新增 `.bat`、`.cmd` 文件使用 UTF-8 无 BOM + CRLF。\n- 修改前后检查 `git status --short` 和 `git diff --stat`。\n- 禁止无关格式化、批量整理和自动提交。\n- 发现整文件 diff 或疑似编码污染时立即停止并报告。\n""",
        ".editorconfig": """root = true\n\n[*]\n# 老项目不强制全局编码和换行，避免编辑器保存时改写历史文件。\ncharset = unset\nend_of_line = unset\ninsert_final_newline = unset\ntrim_trailing_whitespace = false\nindent_style = space\nindent_size = 4\n""",
        ".gitattributes": """# 老项目默认保留文件原始字节，避免 Git 自动转换换行。\n* -text\n""",
        ".gitignore": """# 常见 C++ 构建和 IDE 输出\n/build/\n/out/\n/.vs/\n/CMakeFiles/\nCMakeCache.txt\ncompile_commands.json\n\n# 仅共享项目级 VS Code 设置\n/.vscode/*\n!/.vscode/settings.json\n""",
    }
    return templates[name].encode("utf-8")


def repair_repo(repo: Path, install_hook: bool = False) -> list[str]:
    """只创建缺失的保守配置，绝不覆盖已有文件。"""
    created: list[str] = []
    for name in ("AGENTS.md", ".editorconfig", ".gitattributes", ".gitignore"):
        path = repo / name
        if not path.exists():
            path.write_bytes(_template(name))
            created.append(name)
    if not _config(repo, "--local", "core.autocrlf"):
        subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=str(repo), check=True)
        created.append("git local core.autocrlf=false")
    if not _config(repo, "--local", "core.safecrlf"):
        subprocess.run(["git", "config", "--local", "core.safecrlf", "warn"], cwd=str(repo), check=True)
        created.append("git local core.safecrlf=warn")
    if install_hook:
        from install_hook import install

        created.append(str(install(repo)))
    return created


def _install_tools(findings: list[Finding]) -> None:
    """按平台安装明显缺失的基础工具；调用者必须先取得明确确认。"""
    system = platform.system()
    commands: list[list[str]] = []
    if system == "Windows" and shutil.which("winget"):
        for tool, package in (("PowerShell 7", "Microsoft.PowerShell"), ("gsudo", "gerardog.gsudo"), ("ripgrep", "BurntSushi.ripgrep.MSVC")):
            executable = "pwsh" if tool == "PowerShell 7" else "gsudo" if tool == "gsudo" else "rg"
            action = "upgrade" if shutil.which(executable) else "install"
            commands.append(
                [
                    "winget",
                    action,
                    "--id",
                    package,
                    "--exact",
                    "--source",
                    "winget",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ]
            )
    elif system == "Darwin" and shutil.which("brew"):
        commands.append(["brew", "upgrade" if shutil.which("rg") else "install", "ripgrep"])
    if commands:
        for command in commands:
            code, output = _run(command)
            findings.append(Finding("OK" if code == 0 else "BLOCKED", "设备安装", " ".join(command[:4]), output or "安装命令已执行"))
    else:
        findings.append(Finding("ACTION_REQUIRED", "设备安装", "工具", "未找到可安全自动安装的包管理器或工具均已存在"))


def main(arguments: list[str] | None = None) -> int:
    """执行诊断或用户确认后的安全补齐。"""
    _configure_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Git 工作树内的路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--repair", action="store_true", help="创建缺失仓库配置并设置安全的 local Git 默认值")
    parser.add_argument("--install-hook", action="store_true", help="随 repair 安装仓库私有 pre-commit")
    parser.add_argument("--install-tools", action="store_true", help="按平台安装缺失工具")
    parser.add_argument("--yes", action="store_true", help="确认执行写入或安装操作")
    options = parser.parse_args(arguments)
    repo: Path | None
    repo_error: str | None = None
    try:
        repo = find_repo(options.repo)
    except RuntimeError as error:
        repo = None
        repo_error = str(error)

    findings: list[Finding] = []
    _tool(findings, "Python 3", ["python3", "python", "py"], [sys.executable, "--version"])
    _tool(findings, "ripgrep", ["rg"], ["rg", "--version"])
    _tool(findings, "CMake", ["cmake"], ["cmake", "--version"])
    _tool(findings, "Ninja", ["ninja.exe", "ninja.bat", "ninja"], ["ninja", "--version"])
    if shutil.which("git"):
        _tool(findings, "Git LFS", ["git-lfs"], ["git", "lfs", "version"])
    if platform.system() == "Windows":
        if shutil.which("pwsh"):
            _tool(findings, "PowerShell 7", ["pwsh"], ["pwsh", "-NoLogo", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
        elif shutil.which("powershell"):
            findings.append(Finding("ACTION_REQUIRED", "设备", "PowerShell 7", "当前只有 Windows PowerShell 5.1；建议安装受支持的 PowerShell 7，并让 AI 终端使用 pwsh.exe"))
        else:
            findings.append(Finding("ACTION_REQUIRED", "设备", "PowerShell 7", "未找到 PowerShell；Windows 建议安装受支持的 PowerShell 7"))
        _tool(findings, "gsudo", ["gsudo"], ["gsudo", "--version"])
        _tool(findings, "winget", ["winget"], ["winget", "--version"])
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.exists() or shutil.which("bash"):
            findings.append(Finding("OK", "设备", "Git Bash", "Claude SessionStart hook 可运行"))
        else:
            findings.append(Finding("WARNING", "设备", "Git Bash", "未找到；Claude 插件仍可使用 Skill，但不会注入 SessionStart 上下文"))
    if repo is None:
        findings.append(Finding("BLOCKED", "仓库", "当前目录", repo_error or "不是 Git 工作树"))
    else:
        _check_git(findings, repo)
        _check_repo(findings, repo)

    # Claude Code hook 注册状态（无论是否在仓库中都检查）
    _check_claude_hooks(findings)

    if options.repair or options.install_hook or options.install_tools:
        if not options.yes:
            findings.append(Finding("ACTION_REQUIRED", "修复", "确认", "将要写入仓库或安装工具；确认后添加 --yes"))
        else:
            try:
                if repo is None and (options.repair or options.install_hook):
                    raise RuntimeError("修复仓库前必须在 Git 工作树中运行 doctor")
                if options.repair:
                    created = repair_repo(repo, install_hook=options.install_hook)
                    findings.append(Finding("OK", "修复", "仓库", "已创建：" + (", ".join(created) or "无需创建")))
                    # 同时尝试安装 Claude Code SessionStart hook
                    try:
                        claude_result = _repair_claude_hook(repo)
                        findings.append(Finding("OK", "修复", "Claude hook", claude_result))
                    except (OSError, RuntimeError) as claude_error:
                        findings.append(Finding("WARNING", "修复", "Claude hook", str(claude_error)))
                elif options.install_hook:
                    from install_hook import install

                    findings.append(Finding("OK", "修复", "Git hook", str(install(repo))))
                if options.install_tools:
                    _install_tools(findings)
            except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                findings.append(Finding("BLOCKED", "修复", "执行", str(error)))

    if options.json:
        print(json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2))
    else:
        print(f"啾啾代码守护诊断：{repo or Path(options.repo).resolve()}")
        for item in findings:
            print(f"{item.level:<15} {item.area:<8} {item.item}：{item.message}")
        print("\n说明：诊断默认只读；老文件不自动转码，配置存在时不覆盖。")
        if any(item.level in {"ACTION_REQUIRED", "WARNING"} for item in findings):
            print("\n下一步选项：")
            print("[1] 仅查看报告，不修改")
            print("[2] 补齐缺失仓库配置 + Claude hook：doctor.py --repair --yes")
            print("[3] 安装仓库私有 pre-commit：doctor.py --install-hook --yes")
            print("[4] 安装或更新缺失设备工具：doctor.py --install-tools --yes")
    return 1 if any(item.level == "BLOCKED" for item in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())

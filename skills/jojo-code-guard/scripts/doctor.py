#!/usr/bin/env python3
"""啾啾代码守护：只读诊断设备、Git 和仓库；可选地补齐缺失保护设施。"""

from __future__ import annotations

import argparse
import ctypes
import difflib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from guard_core import find_repo, run_git


# 插件的稳定标识和两端运行时必需资源
PLUGIN_ID = "jojo-code-guard@jojo-code-guard"
CLAUDE_PLUGIN_ID = PLUGIN_ID
CODEX_PLUGIN_ID = PLUGIN_ID
CLAUDE_PLUGIN_REQUIRED_FILES = (
    ".claude-plugin/plugin.json",
    "hooks/hooks.json",
    "hooks/session-start",
    "hooks/post-write-check",
    "skills/jojo-code-guard/SKILL.md",
)
CODEX_PLUGIN_REQUIRED_FILES = (
    ".codex-plugin/plugin.json",
    "hooks/session-start",
    "hooks/post-write-check",
    "skills/jojo-code-guard/SKILL.md",
)

# doctor 管理的用户级规则目标和合并块边界
GLOBAL_RULE_TARGET_RELATIVE_PATHS = (
    Path(".claude") / "CLAUDE.md",
    Path(".codex") / "AGENTS.md",
)
GLOBAL_RULE_START_MARKER = "<!-- jojo-code-guard:global-rules:start -->"
GLOBAL_RULE_END_MARKER = "<!-- jojo-code-guard:global-rules:end -->"


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


@dataclass(frozen=True)
class HookCapabilities:
    """记录安装清单声明的三段自动守护能力。"""

    session_start: bool
    post_write: bool
    stop_check: bool


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
    # Windows 上 core.filemode 必须为 false，否则 Unix 可执行权限位(100755↔100644)
    # 差异会导致 git status 持续显示 0 行内容的 modified
    if os.name == "nt":
        fm = _config(repo, "--local", "core.filemode").lower()
        scope = "local"
        if not fm:
            fm = _config(repo, "--global", "core.filemode").lower()
            scope = "global"
        if not fm:
            fm = "true"  # git 默认值
            scope = "默认"
        if fm != "false":
            findings.append(Finding("WARNING", "Git", f"{scope} core.filemode",
                f"当前为 {fm}，Windows 上应设为 false；"
                "否则 Unix 可执行权限位差异会令 git status 持续显示 0 行内容的 modified"))
        else:
            findings.append(Finding("OK", "Git", f"{scope} core.filemode", fm))


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
        forces_encoding_or_eol = key in {"charset", "end_of_line"} and value not in {"unset", "auto"}
        changes_on_save = key in {"insert_final_newline", "trim_trailing_whitespace"} and value == "true"
        if separator and (forces_encoding_or_eol or changes_on_save):
            dangerous.append(line.strip())
    if dangerous:
        findings.append(
            Finding(
                "WARNING",
                "仓库",
                ".editorconfig",
                "包含可能改写老文件的编码、换行或保存清理规则：" + "; ".join(dangerous),
            )
        )
    else:
        findings.append(Finding("OK", "仓库", ".editorconfig", "存在且未发现强制编码、换行或保存清理声明"))


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
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    risky_tokens = ("text", "text=auto", "eol=lf", "eol=crlf", "working-tree-encoding=UTF-8")
    risky = [line for line in lines if any(token in line.split() for token in risky_tokens)]
    preserves_default = any(line.startswith("* -text") for line in lines)
    if risky:
        message = "存在可能规范化老文件的具体规则：" + "; ".join(risky[:6])
        if preserves_default:
            message += "；具体路径规则会覆盖 * -text 的默认值"
        findings.append(Finding("WARNING", "仓库", ".gitattributes", message))
    elif preserves_default:
        findings.append(Finding("OK", "仓库", ".gitattributes", "已设置 * -text，默认不会替换老文件换行"))
    else:
        findings.append(Finding("WARNING", "仓库", ".gitattributes", "存在但未声明老项目的字节保真策略"))


def _check_hook(findings: list[Finding], repo: Path) -> None:
    """检查有效 hooks 路径、pre-commit 和可选 pre-commit 框架。"""
    _, hooks_path = _run(["git", "rev-parse", "--git-path", "hooks"], repo)
    hook = Path(hooks_path) if Path(hooks_path).is_absolute() else (repo / hooks_path).resolve()
    pre_commit = hook / "pre-commit"
    if not pre_commit.exists():
        findings.append(
            Finding(
                "WARNING",
                "Git hook",
                str(pre_commit),
                "未安装仓库私有 pre-commit（可选；需要提交阶段机械门禁时再安装）",
            )
        )
    else:
        hook_content = _read_utf8(pre_commit)
        if hook_content is not None and "jojo-code-guard-managed-hook" in hook_content:
            source_dir = Path(__file__).resolve().parent
            try:
                from install_hook import WRAPPER
            except ImportError:
                WRAPPER = None
            expected = {
                "jojo_guard_core.py": source_dir / "guard_core.py",
                "jojo_hook_check.py": source_dir / "hook_check.py",
            }
            stale: list[str] = []
            for name, source in expected.items():
                try:
                    if not source.is_file() or not (hook / name).is_file():
                        stale.append(name)
                    elif (hook / name).read_bytes() != source.read_bytes():
                        stale.append(name)
                except OSError:
                    stale.append(name)
            if WRAPPER is not None and pre_commit.read_bytes() != WRAPPER.encode("utf-8"):
                stale.insert(0, "pre-commit")
            if stale:
                findings.append(
                    Finding(
                        "ACTION_REQUIRED",
                        "Git hook",
                        str(pre_commit),
                        "Hook 已安装但检查脚本不是当前版本："
                        + ", ".join(stale)
                        + "；请重新运行 doctor.py --install-hook --yes",
                    )
                )
            else:
                findings.append(Finding("OK", "Git hook", str(pre_commit), "已安装啾啾代码守护 hook，脚本版本匹配"))
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


def _find_codex_home() -> Path:
    """定位 Codex 用户目录，并尊重官方 CODEX_HOME 覆盖。"""
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()


def _read_json_object(path: Path) -> dict[str, object] | None:
    """读取 UTF-8 JSON/JSONC 对象，格式异常时返回 None。"""
    content = _read_utf8(path)
    if content is None:
        return None
    try:
        value = json.loads(_strip_jsonc_comments(content))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _plugin_root() -> Path:
    """定位当前 doctor 随附的插件根目录。"""
    return Path(__file__).resolve().parents[3]


def _manifest_version(path: Path) -> str | None:
    """读取插件 manifest 中的非空版本号。"""
    manifest = _read_json_object(path)
    version = manifest.get("version") if manifest else None
    return version if isinstance(version, str) and version else None


def _source_plugin_versions() -> dict[str, str]:
    """读取当前发布包中可用的 Claude/Codex 版本号。"""
    root = _plugin_root()
    versions: dict[str, str] = {}
    for client, relative in (
        ("Claude", ".claude-plugin/plugin.json"),
        ("Codex", ".codex-plugin/plugin.json"),
    ):
        path = root / relative
        if path.is_file():
            version = _manifest_version(path)
            if version:
                versions[client] = version
    return versions


def _current_plugin_version() -> str | None:
    """返回两端一致的当前发布版本；缺失或冲突时返回 None。"""
    versions = _source_plugin_versions()
    root = _plugin_root()
    invalid = (
        (root / ".claude-plugin" / "plugin.json").is_file() and "Claude" not in versions
    ) or ((root / ".codex-plugin" / "plugin.json").is_file() and "Codex" not in versions)
    if invalid:
        return None
    unique = set(versions.values())
    return next(iter(unique)) if len(unique) == 1 else None


def _check_source_plugin_version(findings: list[Finding]) -> str | None:
    """确认当前 doctor 随附的客户端 manifest 版本一致。"""
    versions = _source_plugin_versions()
    root = _plugin_root()
    invalid = [
        client
        for client, relative in (
            ("Claude", ".claude-plugin/plugin.json"),
            ("Codex", ".codex-plugin/plugin.json"),
        )
        if (root / relative).is_file() and client not in versions
    ]
    if invalid:
        findings.append(
            Finding("BLOCKED", "插件源码", "Version", "客户端 manifest 版本缺失或无法解析：" + "、".join(invalid))
        )
        return None
    if not versions:
        findings.append(Finding("BLOCKED", "插件源码", "Version", "未找到可解析的客户端 manifest 版本"))
        return None
    unique = set(versions.values())
    summary = "，".join(f"{client}={version}" for client, version in sorted(versions.items()))
    if len(unique) != 1:
        findings.append(Finding("BLOCKED", "插件源码", "Version", "客户端版本不一致：" + summary))
        return None
    version = next(iter(unique))
    findings.append(Finding("OK", "插件源码", "Version", f"当前 doctor 随附版本 {version}（{summary}）"))
    return version


def _hook_manifest_path(plugin_root: Path, client: str) -> Path:
    """按客户端 manifest 定位生命周期 Hook 清单。"""
    manifest_name = ".claude-plugin/plugin.json" if client == "Claude" else ".codex-plugin/plugin.json"
    manifest = _read_json_object(plugin_root / manifest_name)
    configured = manifest.get("hooks") if manifest else None
    if isinstance(configured, str) and configured.strip():
        return plugin_root / configured.removeprefix("./")
    return plugin_root / "hooks" / "hooks.json"


def _check_hook_manifest_freshness(
    findings: list[Finding], client: str, install_path: Path
) -> Path | None:
    """比较安装缓存与当前发布包的客户端 Hook 清单。"""
    source_root = _plugin_root()
    expected_path = _hook_manifest_path(source_root, client)
    installed_path = _hook_manifest_path(install_path, client)
    try:
        expected_path.resolve().relative_to(source_root.resolve())
        installed_path.resolve().relative_to(install_path.resolve())
    except (OSError, ValueError):
        findings.append(Finding("BLOCKED", client, "Hook manifest", "Hook 清单路径超出插件目录"))
        return None
    expected = _read_json_object(expected_path) if expected_path.is_file() else None
    installed = _read_json_object(installed_path) if installed_path.is_file() else None
    if expected is None:
        findings.append(
            Finding("BLOCKED", client, "Hook manifest", f"当前发布清单缺失或无法解析：{expected_path}")
        )
        return None
    if installed is None:
        findings.append(
            Finding("BLOCKED", client, "Hook manifest", f"安装清单缺失或无法解析：{installed_path}")
        )
        return None
    if installed != expected:
        findings.append(
            Finding(
                "ACTION_REQUIRED",
                client,
                "Hook manifest",
                f"安装清单不是当前 doctor 随附版本：{installed_path}；请升级或重新安装插件",
            )
        )
    else:
        findings.append(
            Finding("OK", client, "Hook manifest", f"与当前 doctor 随附清单一致：{installed_path}")
        )
    return installed_path


def _check_installed_plugin_version(
    findings: list[Finding],
    client: str,
    install_path: Path,
    manifest_relative: str,
    expected_version: str | None,
    registered_version: str | None = None,
    cache_version: str | None = None,
) -> str | None:
    """核对登记、缓存目录、安装 manifest 与当前发布版本。"""
    installed_version = _manifest_version(install_path / manifest_relative)
    if installed_version is None:
        findings.append(
            Finding("BLOCKED", client, "Plugin version", f"安装 manifest 缺少有效版本：{install_path / manifest_relative}")
        )
        return None
    inconsistent: list[str] = []
    if registered_version and registered_version != installed_version:
        inconsistent.append(f"登记={registered_version}")
    if cache_version and cache_version != "local" and cache_version != installed_version:
        inconsistent.append(f"缓存目录={cache_version}")
    if inconsistent:
        findings.append(
            Finding(
                "BLOCKED",
                client,
                "Plugin version",
                f"安装 manifest={installed_version}，但" + "、".join(inconsistent),
            )
        )
        return installed_version
    if expected_version and installed_version != expected_version:
        findings.append(
            Finding(
                "ACTION_REQUIRED",
                client,
                "Plugin version",
                f"已安装 {installed_version}，当前 doctor 随附 {expected_version}；请升级或重新安装插件",
            )
        )
    elif expected_version:
        findings.append(Finding("OK", client, "Plugin version", installed_version))
    else:
        findings.append(
            Finding("WARNING", client, "Plugin version", f"已安装 {installed_version}，但无法确定当前发布版本")
        )
    return installed_version


def _parse_codex_plugin_enabled(content: str) -> bool | None:
    """解析 Codex 生成的精确插件表，不依赖 Python 3.11 的 tomllib。"""
    section = re.compile(
        r'^\s*\[\s*plugins\s*\.\s*["\']'
        + re.escape(CODEX_PLUGIN_ID)
        + r'["\']\s*\]\s*(?:#.*)?$'
    )
    table = re.compile(r"^\s*\[[^]]+\]\s*(?:#.*)?$")
    enabled = re.compile(r"^\s*enabled\s*=\s*(true|false)\s*(?:#.*)?$", re.IGNORECASE)
    in_plugin = False
    for line in content.splitlines():
        if table.fullmatch(line):
            in_plugin = section.fullmatch(line) is not None
            continue
        if in_plugin:
            match = enabled.fullmatch(line)
            if match:
                return match.group(1).lower() == "true"
    return None


def _parse_codex_hooks_config(content: str) -> bool | None:
    """读取用户配置中的显式 features.hooks 备用值。"""
    table = re.compile(r"^\s*\[[^]]+\]\s*(?:#.*)?$")
    features = re.compile(r"^\s*\[\s*features\s*\]\s*(?:#.*)?$", re.IGNORECASE)
    hooks = re.compile(r"^\s*hooks\s*=\s*(true|false)\s*(?:#.*)?$", re.IGNORECASE)
    in_features = False
    for line in content.splitlines():
        if table.fullmatch(line):
            in_features = features.fullmatch(line) is not None
            continue
        if in_features:
            match = hooks.fullmatch(line)
            if match:
                return match.group(1).lower() == "true"
    return None


def _iter_hook_commands(value: object):
    """递归枚举 Hook 配置中的 command 命令。"""
    if isinstance(value, dict):
        command = value.get("command")
        if value.get("type") == "command" and isinstance(command, str):
            yield command
        for child in value.values():
            yield from _iter_hook_commands(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_hook_commands(child)


def _hook_command_resources(manifest: dict[str, object] | None) -> set[str]:
    """从 Hook 命令提取插件内被调用的脚本相对路径。"""
    resources: set[str] = set()
    if not manifest:
        return resources
    for command in _iter_hook_commands(manifest.get("hooks")):
        normalized = command.replace("\\", "/")
        for name in re.findall(r"/hooks/([A-Za-z0-9._-]+)", normalized):
            resources.add("hooks/" + name)
    return resources


def _handler_runs_stop_check(handler: object) -> bool:
    """确认 Stop handler 会以正确事件调用回合结束检查。"""
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    return "stop-check" in command or "post-write-check" in command


def _add_manual_hook_findings(findings: list[Finding], client: str) -> None:
    """明确区分静态配置与只能在客户端内完成的验收。"""
    if client == "Codex":
        trust = "请在当前 Codex 会话使用 /hooks 审阅并信任当前 Hook 精确哈希；doctor 不读取内部信任存储"
    else:
        trust = "请在 Claude 的 /hooks 中确认插件 Hook 已加载且允许执行；doctor 不把安装登记等同于运行时信任"
    findings.append(Finding("WARNING", client, "Hook trust（人工验收）", trust))
    findings.append(
        Finding(
            "WARNING",
            client,
            "Hook execution（人工验收）",
            "请在真实会话分别触发 SessionStart、文件写入和 Stop；静态文件存在不能证明生命周期事件实际执行",
        )
    )


def _check_codex_hook_feature(findings: list[Finding], config_content: str | None) -> None:
    """优先读取 Codex CLI 报告的 Hooks 有效功能状态。"""
    executable = shutil.which("codex")
    if executable:
        code, output = _run([executable, "features", "list"])
        if code == 0:
            plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
            match = re.search(r"(?m)^\s*hooks\s+\S+\s+(true|false)\s*$", plain, re.IGNORECASE)
            if match:
                enabled = match.group(1).lower() == "true"
                level = "OK" if enabled else "ACTION_REQUIRED"
                message = "有效状态为 true" if enabled else "有效状态为 false；生命周期 Hook 不会运行"
                findings.append(Finding(level, "Codex", "Hooks feature", message))
                return
            failure = "codex features list 输出中没有 hooks 有效状态"
        else:
            failure = "codex features list 执行失败" + (f"：{output}" if output else "")
    else:
        failure = "PATH 中未找到 Codex CLI"

    configured = _parse_codex_hooks_config(config_content) if config_content is not None else None
    suffix = ""
    if configured is not None:
        suffix = f"；用户配置 features.hooks={'true' if configured else 'false'}，但这不代表最终有效状态"
    findings.append(Finding("WARNING", "Codex", "Hooks feature", failure + suffix + "；需要在 Codex 内人工确认"))


def _codex_cache_root(codex_home: Path) -> Path:
    """按官方缓存布局定位当前插件的 Codex 版本目录。"""
    plugin_name, marketplace = CODEX_PLUGIN_ID.rsplit("@", 1)
    return codex_home / "plugins" / "cache" / marketplace / plugin_name


def _check_codex_plugin(findings: list[Finding], expected_version: str | None = None) -> None:
    """只读检查 Codex 插件启用状态、缓存版本和 Hook 资源。"""
    codex_home = _find_codex_home()
    config_path = codex_home / "config.toml"
    config_content = _read_utf8(config_path) if config_path.exists() else None
    if config_path.exists() and config_content is None:
        findings.append(Finding("WARNING", "Codex", "config.toml", f"无法按 UTF-8 读取：{config_path}"))
    enabled = _parse_codex_plugin_enabled(config_content) if config_content is not None else None
    if enabled is True:
        findings.append(Finding("OK", "Codex", "Plugin enabled", CODEX_PLUGIN_ID))
    elif enabled is False:
        findings.append(Finding("ACTION_REQUIRED", "Codex", "Plugin enabled", "插件缓存可能存在，但配置中已禁用"))
    else:
        findings.append(
            Finding("ACTION_REQUIRED", "Codex", "Plugin enabled", "config.toml 未明确记录精确插件 ID 的启用状态")
        )

    _check_codex_hook_feature(findings, config_content)
    cache_root = _codex_cache_root(codex_home)
    try:
        installations = sorted(
            (path for path in cache_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        ) if cache_root.is_dir() else []
    except OSError as error:
        findings.append(Finding("BLOCKED", "Codex", "Plugin cache", f"无法读取 {cache_root}：{error}"))
        return
    if not installations:
        level = "BLOCKED" if enabled is True else "ACTION_REQUIRED"
        findings.append(Finding(level, "Codex", "Plugin cache", f"未找到安装缓存：{cache_root}"))
        return

    valid: list[tuple[Path, str]] = []
    invalid: list[str] = []
    for install_path in installations:
        version = _manifest_version(install_path / ".codex-plugin" / "plugin.json")
        if version:
            valid.append((install_path, version))
        else:
            invalid.append(str(install_path))
    if invalid:
        findings.append(
            Finding("BLOCKED", "Codex", "Plugin cache", "缓存 manifest 缺失或版本无效：" + "，".join(invalid))
        )
    if not valid:
        return

    versions = sorted({version for _, version in valid})
    matching = [entry for entry in valid if expected_version and entry[1] == expected_version]
    selected: tuple[Path, str] | None
    if len(valid) > 1:
        selected = None
    elif len(matching) == 1:
        selected = matching[0]
    elif len(valid) == 1:
        selected = valid[0]
    else:
        selected = None
    if len(valid) > 1:
        findings.append(
            Finding(
                "WARNING",
                "Codex",
                "Plugin cache",
                "发现多个缓存版本：" + "、".join(versions) + "；实际加载版本需在客户端内人工确认",
            )
        )
    else:
        findings.append(Finding("OK", "Codex", "Plugin cache", str(valid[0][0])))
    if selected is None:
        findings.append(
            Finding("WARNING", "Codex", "Plugin version", "无法仅凭缓存目录确定当前加载版本，请在客户端内人工确认")
        )
        _add_manual_hook_findings(findings, "Codex")
        return

    install_path, _ = selected
    _check_installed_plugin_version(
        findings,
        "Codex",
        install_path,
        ".codex-plugin/plugin.json",
        expected_version if expected_version is not None else _current_plugin_version(),
        cache_version=install_path.name,
    )
    hook_path = _hook_manifest_path(install_path, "Codex")
    required = set(CODEX_PLUGIN_REQUIRED_FILES)
    try:
        relative_hook_path = hook_path.resolve().relative_to(install_path.resolve())
        required.add(relative_hook_path.as_posix())
    except (OSError, ValueError):
        findings.append(Finding("BLOCKED", "Codex", "Plugin resources", f"Hook 清单超出插件目录：{hook_path}"))
        _add_manual_hook_findings(findings, "Codex")
        return
    hook_path = _check_hook_manifest_freshness(findings, "Codex", install_path)
    manifest = _read_json_object(hook_path) if hook_path else None
    required.update(_hook_command_resources(manifest))
    missing = sorted(name for name in required if not (install_path / name).is_file())
    if missing:
        findings.append(Finding("BLOCKED", "Codex", "Plugin resources", "安装目录缺少资源：" + ", ".join(missing)))
    else:
        findings.append(Finding("OK", "Codex", "Plugin resources", str(install_path)))
        capabilities = _hook_capabilities(
            manifest,
            ("apply_patch", "Edit", "Write", "Bash"),
        )
        _add_hook_capability_findings(findings, "Codex", capabilities)
    _add_manual_hook_findings(findings, "Codex")


def _matcher_covers_tools(matcher: str, tools: tuple[str, ...]) -> bool:
    """判断 Claude matcher 是否覆盖全部文件写入工具。"""
    if matcher.strip() == "*":
        return True
    try:
        return all(re.fullmatch(matcher, tool) is not None for tool in tools)
    except re.error:
        return False


def _hook_capabilities(
    manifest: dict[str, object] | None,
    post_tools: tuple[str, ...],
) -> HookCapabilities:
    """解析客户端所需的 SessionStart、PostToolUse 和 Stop 声明。"""
    hook_groups = manifest.get("hooks") if manifest else None
    if not isinstance(hook_groups, dict):
        return HookCapabilities(False, False, False)

    session_start = False
    session_entries = hook_groups.get("SessionStart")
    if isinstance(session_entries, list):
        for entry in session_entries:
            matcher = entry.get("matcher") if isinstance(entry, dict) else None
            handlers = entry.get("hooks") if isinstance(entry, dict) else None
            if (
                isinstance(matcher, str)
                and _matcher_covers_tools(matcher, ("startup", "resume", "clear", "compact"))
                and isinstance(handlers, list)
            ):
                session_start = any(
                    isinstance(handler, dict)
                    and handler.get("type") == "command"
                    and isinstance(handler.get("command"), str)
                    and "session-start" in handler["command"]
                    for handler in handlers
                )
                if session_start:
                    break

    post_write = False
    post_entries = hook_groups.get("PostToolUse")
    if isinstance(post_entries, list):
        for entry in post_entries:
            matcher = entry.get("matcher") if isinstance(entry, dict) else None
            handlers = entry.get("hooks") if isinstance(entry, dict) else None
            if (
                isinstance(matcher, str)
                and _matcher_covers_tools(matcher, post_tools)
                and isinstance(handlers, list)
            ):
                post_write = any(
                    isinstance(handler, dict)
                    and handler.get("type") == "command"
                    and isinstance(handler.get("command"), str)
                    and "post-write-check" in handler["command"]
                    for handler in handlers
                )
                if post_write:
                    break

    stop_check = False
    stop_entries = hook_groups.get("Stop")
    if isinstance(stop_entries, list):
        for entry in stop_entries:
            handlers = entry.get("hooks") if isinstance(entry, dict) else None
            if isinstance(handlers, list) and any(_handler_runs_stop_check(handler) for handler in handlers):
                stop_check = True
                break
    return HookCapabilities(session_start, post_write, stop_check)


def _add_hook_capability_findings(
    findings: list[Finding],
    client: str,
    capabilities: HookCapabilities,
) -> None:
    """分别报告三段清单能力，不把静态声明等同于实际执行。"""
    states = (
        (
            "SessionStart",
            capabilities.session_start,
            "安装清单已声明会话开始 Hook",
            "插件资源存在但未配置 session-start；请升级或重新安装插件",
        ),
        (
            "PostToolUse",
            capabilities.post_write,
            "安装清单已覆盖编辑和 shell 写入工具",
            "插件资源存在但未配置 post-write-check；请升级或重新安装插件",
        ),
        (
            "Stop",
            capabilities.stop_check,
            "安装清单已声明回合结束兜底检查",
            "插件资源存在但未正确配置 Stop 检查；请升级或重新安装插件",
        ),
    )
    for item, enabled, ok_message, missing_message in states:
        findings.append(
            Finding("OK" if enabled else "ACTION_REQUIRED", client, item, ok_message if enabled else missing_message)
        )


def _check_legacy_claude_hooks(
    findings: list[Finding], claude_home: Path, settings: dict[str, object] | None
) -> None:
    """报告可能与插件重复执行的旧版手工 hook。"""
    session_start = claude_home / "hooks" / "session-start"
    content = _read_utf8(session_start) if session_start.exists() else None
    if content and "JOJO_CODE_GUARD" in content:
        findings.append(
            Finding(
                "WARNING",
                "Claude",
                "旧版手工 hook",
                f"可能与插件重复执行，请人工确认：{session_start}",
            )
        )
    if settings:
        commands = list(_iter_hook_commands(settings.get("hooks")))
        legacy = [command for command in commands if "jojo-code-guard" in command.lower()]
        if legacy:
            findings.append(
                Finding(
                    "WARNING",
                    "Claude",
                    "settings.json hooks",
                    "存在旧版 jojo-code-guard 手工命令，请人工确认",
                )
            )


def _check_claude_hooks(findings: list[Finding], expected_version: str | None = None) -> None:
    """精确检查 Claude 插件的登记、启用状态和自动加载资源。"""
    claude_home = _find_claude_home()
    settings_path = claude_home / "settings.json"
    registry_path = claude_home / "plugins" / "installed_plugins.json"
    settings = _read_json_object(settings_path) if settings_path.exists() else {}
    registry = _read_json_object(registry_path) if registry_path.exists() else {}

    if settings_path.exists() and settings is None:
        findings.append(Finding("WARNING", "Claude", "settings.json", "无法解析，未能确认插件启用状态"))
    if registry_path.exists() and registry is None:
        findings.append(Finding("BLOCKED", "Claude", "插件登记", f"无法解析：{registry_path}"))
        _check_legacy_claude_hooks(findings, claude_home, settings)
        return

    enabled_plugins = settings.get("enabledPlugins") if settings else None
    enabled = enabled_plugins.get(CLAUDE_PLUGIN_ID) if isinstance(enabled_plugins, dict) else None
    plugins = registry.get("plugins") if registry else None
    records = plugins.get(CLAUDE_PLUGIN_ID) if isinstance(plugins, dict) else None
    if not isinstance(records, list) or not records:
        level = "BLOCKED" if enabled is True else "ACTION_REQUIRED"
        findings.append(
            Finding(
                level,
                "Claude",
                "Plugin",
                "未找到有效安装登记；请使用 /plugin install jojo-code-guard@jojo-code-guard 安装",
            )
        )
        _check_legacy_claude_hooks(findings, claude_home, settings)
        return

    # 使用最后一条登记，它通常对应最近一次安装或更新
    record = records[-1]
    install_value = record.get("installPath") if isinstance(record, dict) else None
    if not isinstance(install_value, str) or not install_value:
        findings.append(Finding("BLOCKED", "Claude", "插件登记", "installPath 缺失或不是字符串"))
        _check_legacy_claude_hooks(findings, claude_home, settings)
        return

    install_path = Path(install_value).expanduser()
    registered_value = record.get("version") if isinstance(record, dict) else None
    registered_version = registered_value if isinstance(registered_value, str) and registered_value else None
    _check_installed_plugin_version(
        findings,
        "Claude",
        install_path,
        ".claude-plugin/plugin.json",
        expected_version if expected_version is not None else _current_plugin_version(),
        registered_version=registered_version,
    )
    hooks_path = _check_hook_manifest_freshness(findings, "Claude", install_path)
    hooks_manifest = _read_json_object(hooks_path) if hooks_path else None
    required = set(CLAUDE_PLUGIN_REQUIRED_FILES)
    required.update(_hook_command_resources(hooks_manifest))
    missing = sorted(name for name in required if not (install_path / name).is_file())
    if missing:
        findings.append(
            Finding("BLOCKED", "Claude", "Plugin resources", "安装目录缺少资源：" + ", ".join(missing))
        )
    else:
        findings.append(Finding("OK", "Claude", "Plugin resources", str(install_path)))
        capabilities = _hook_capabilities(
            hooks_manifest,
            ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell"),
        )
        _add_hook_capability_findings(findings, "Claude", capabilities)

    if enabled is True:
        findings.append(Finding("OK", "Claude", "Plugin enabled", CLAUDE_PLUGIN_ID))
    elif enabled is False:
        findings.append(Finding("ACTION_REQUIRED", "Claude", "Plugin enabled", "插件已安装但被禁用"))
    else:
        findings.append(
            Finding("WARNING", "Claude", "Plugin enabled", "已安装，但 settings.json 未明确记录启用状态")
        )
    _add_manual_hook_findings(findings, "Claude")
    _check_legacy_claude_hooks(findings, claude_home, settings)


def _global_rule_source_path() -> Path:
    """定位 Skill 内置的全局规则源文件。"""
    return Path(__file__).resolve().parents[1] / "references" / "全局规则.md"


def _global_rule_target_paths() -> list[Path]:
    """生成 Claude 与 Codex 的固定用户级规则路径。"""
    home = Path.home()
    return [home / relative for relative in GLOBAL_RULE_TARGET_RELATIVE_PATHS]


def _normalize_newlines(text: str) -> str:
    """将文本换行统一为 LF，仅用于内容比较。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _global_rule_info(data: bytes) -> str:
    """生成规则文件的字节、BOM、换行和哈希摘要。"""
    bom = "utf-8" if data.startswith(b"\xef\xbb\xbf") else "none"
    payload = data[3:] if bom == "utf-8" else data
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        eol = "unknown"
    else:
        crlf = text.count("\r\n")
        remainder = text.replace("\r\n", "")
        lf_only = remainder.count("\n")
        cr_only = remainder.count("\r")
        kinds = sum(bool(value) for value in (crlf, lf_only, cr_only))
        if kinds > 1:
            eol = "mixed"
        elif crlf:
            eol = "crlf"
        elif lf_only:
            eol = "lf"
        elif cr_only:
            eol = "cr"
        else:
            eol = "none"
    digest = hashlib.sha256(data).hexdigest()[:16]
    return f"字节={len(data)}，BOM={bom}，换行={eol}，SHA-256={digest}"


def _global_rule_diff(source: Path, source_data: bytes, target: Path, target_data: bytes) -> str:
    """生成适合 doctor 报告的受限统一差异。"""
    source_text = source_data.decode("utf-8-sig", errors="replace").splitlines(keepends=True)
    target_text = target_data.decode("utf-8-sig", errors="replace").splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            source_text,
            target_text,
            fromfile=str(source),
            tofile=str(target),
            n=2,
        )
    )
    if not diff:
        return "文本相同，但编码、BOM 或换行不同"
    limit = 80
    preview = "".join(diff[:limit]).rstrip()
    if len(diff) > limit:
        preview += f"\n……差异共 {len(diff)} 行，仅显示前 {limit} 行"
    return preview


def _global_rule_content_state(target_data: bytes, source_data: bytes) -> str:
    """判断目标是否已包含当前源规则或当前受管合并块。"""
    try:
        target_text = target_data.decode("utf-8-sig", errors="strict")
        source_text = source_data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return "invalid"
    target_normal = _normalize_newlines(target_text)
    source_normal = _normalize_newlines(source_text).rstrip("\n")
    block = f"{GLOBAL_RULE_START_MARKER}\n{source_normal}\n{GLOBAL_RULE_END_MARKER}"
    if block in target_normal:
        return "merged"
    if source_normal in target_normal:
        return "contained"
    return "different"


def _check_global_rules(findings: list[Finding], mode: str | None = None) -> None:
    """只读比较两个用户级全局规则目标。"""
    source = _global_rule_source_path()
    if not source.is_file():
        findings.append(Finding("BLOCKED", "全局规则", "源文件", f"不存在：{source}"))
        return
    source_data = source.read_bytes()
    for target in _global_rule_target_paths():
        if target.is_symlink():
            findings.append(
                Finding("BLOCKED", "全局规则", str(target), "目标是符号链接，拒绝跟随写入")
            )
            continue
        if not target.exists():
            findings.append(
                Finding(
                    "ACTION_REQUIRED",
                    "全局规则",
                    str(target),
                    "目标不存在，可选择覆盖或合并创建",
                )
            )
            continue
        try:
            target_data = target.read_bytes()
        except OSError as error:
            findings.append(Finding("BLOCKED", "全局规则", str(target), f"无法读取：{error}"))
            continue
        if target_data == source_data:
            findings.append(Finding("OK", "全局规则", str(target), "与内置源文件逐字节一致"))
            continue
        if mode == "merge":
            try:
                _merge_global_rule_bytes(target_data, source_data)
            except RuntimeError as error:
                findings.append(Finding("BLOCKED", "全局规则", str(target), str(error)))
                continue
        content_state = _global_rule_content_state(target_data, source_data)
        if content_state == "merged":
            findings.append(Finding("OK", "全局规则", str(target), "受管合并块已是最新版本"))
            continue
        if content_state == "contained":
            findings.append(Finding("OK", "全局规则", str(target), "已包含当前内置规则"))
            continue
        message = (
            f"与内置规则不同；源：{_global_rule_info(source_data)}；"
            f"目标：{_global_rule_info(target_data)}"
        )
        if mode is not None:
            message += "\n" + _global_rule_diff(source, source_data, target, target_data)
        findings.append(Finding("WARNING", "全局规则", str(target), message))


def _merge_global_rule_bytes(target_data: bytes, source_data: bytes) -> bytes:
    """在保留目标编码和换行的前提下创建或更新受管规则块。"""
    target_bom = b"\xef\xbb\xbf" if target_data.startswith(b"\xef\xbb\xbf") else b""
    target_payload = target_data[len(target_bom):]
    source_payload = source_data[3:] if source_data.startswith(b"\xef\xbb\xbf") else source_data
    try:
        target_text = target_payload.decode("utf-8", errors="strict")
        source_text = source_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"合并只支持严格 UTF-8 文本：{error}") from error

    crlf = target_text.count("\r\n")
    remainder = target_text.replace("\r\n", "")
    lf_only = remainder.count("\n")
    cr_only = remainder.count("\r")
    if sum(bool(value) for value in (crlf, lf_only, cr_only)) > 1:
        raise RuntimeError("目标使用混合换行，拒绝在合并时重写")
    line_ending = "\r\n" if crlf else "\r" if cr_only else "\n"
    source_normal = _normalize_newlines(source_text).rstrip("\n")
    if GLOBAL_RULE_START_MARKER in source_normal or GLOBAL_RULE_END_MARKER in source_normal:
        raise RuntimeError("内置规则不能包含 doctor 的受管块标记")
    source_for_target = source_normal.replace("\n", line_ending)
    block = (
        f"{GLOBAL_RULE_START_MARKER}{line_ending}"
        f"{source_for_target}{line_ending}"
        f"{GLOBAL_RULE_END_MARKER}"
    )

    start_count = target_text.count(GLOBAL_RULE_START_MARKER)
    end_count = target_text.count(GLOBAL_RULE_END_MARKER)
    if start_count != end_count or start_count > 1:
        raise RuntimeError("目标中的 jojo-code-guard 受管标记不完整或重复")
    if start_count == 1:
        start = target_text.index(GLOBAL_RULE_START_MARKER)
        end_start = target_text.find(GLOBAL_RULE_END_MARKER, start + len(GLOBAL_RULE_START_MARKER))
        if end_start < 0:
            raise RuntimeError("目标中的 jojo-code-guard 受管标记顺序错误")
        end = end_start + len(GLOBAL_RULE_END_MARKER)
        merged = target_text[:start] + block + target_text[end:]
    elif source_normal in _normalize_newlines(target_text):
        return target_data
    elif not target_text:
        merged = block + line_ending
    else:
        if target_text.endswith(("\r", "\n")):
            separator = line_ending
        else:
            separator = line_ending * 2
        merged = target_text + separator + block + line_ending
    return target_bom + merged.encode("utf-8")


def _sync_global_rules(mode: str) -> list[str]:
    """按覆盖或合并模式写入两个全局规则目标并复核结果。"""
    if mode not in {"overwrite", "merge"}:
        raise RuntimeError(f"不支持的全局规则同步模式：{mode}")
    source = _global_rule_source_path()
    if not source.is_file():
        raise RuntimeError(f"Skill 内置规则文件不存在：{source}")
    source_data = source.read_bytes()
    plans: list[tuple[Path, bytes, bool, bytes]] = []
    for target in _global_rule_target_paths():
        if target.is_symlink():
            raise RuntimeError(f"目标是符号链接，拒绝写入：{target}")
        existed = target.exists()
        current = target.read_bytes() if existed else b""
        data = source_data if mode == "overwrite" else _merge_global_rule_bytes(current, source_data)
        plans.append((target, data, existed, current))

    changed: list[str] = []
    written: list[tuple[Path, bool, bytes]] = []
    try:
        for target, data, existed, current in plans:
            target.parent.mkdir(parents=True, exist_ok=True)
            if existed and current == data:
                continue
            written.append((target, existed, current))
            target.write_bytes(data)
            changed.append(str(target))
        for target, data, _, _ in plans:
            if target.read_bytes() != data:
                raise RuntimeError(f"写入后复核失败：{target}")
    except (OSError, RuntimeError) as error:
        rollback_errors: list[str] = []
        for target, existed, current in reversed(written):
            try:
                if existed:
                    target.write_bytes(current)
                elif target.exists() or target.is_symlink():
                    target.unlink()
            except OSError as rollback_error:
                rollback_errors.append(f"{target}: {rollback_error}")
        message = f"写入失败并已回滚：{error}"
        if rollback_errors:
            message += "；回滚失败：" + "；".join(rollback_errors)
        raise RuntimeError(message) from error
    return changed


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
    safe_eol_values = []
    encoding_values = []
    auto_guess = False
    auto_guess_seen = False
    format_on_save = False
    code_actions_on_save = False
    insert_final_newline = False
    trim_trailing_whitespace = False
    for key, value in _iter_setting_values(settings):
        if key == "files.eol" and isinstance(value, str):
            if value.lower() == "auto":
                safe_eol_values.append(value)
            else:
                eol_values.append(value)
        elif key == "files.encoding" and isinstance(value, str):
            encoding_values.append(value)
        elif key == "files.autoGuessEncoding" and value is True:
            auto_guess = True
            auto_guess_seen = True
        elif key == "files.autoGuessEncoding" and value is False:
            auto_guess_seen = True
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
    if safe_eol_values:
        findings.append(Finding("OK", "仓库", item, "files.eol=auto，会沿用已打开文件的原始换行"))
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
    elif auto_guess_seen:
        findings.append(
            Finding(
                "WARNING",
                "仓库",
                item,
                "关闭 autoGuessEncoding；打开旧编码文件时可能被错误解码并在保存时产生乱码",
            )
        )
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
    for name in (".gitignore",):
        findings.append(
            Finding("OK" if (repo / name).exists() else "ACTION_REQUIRED", "仓库", name, "存在" if (repo / name).exists() else "缺失")
        )
    agents_path = repo / "AGENTS.md"
    findings.append(
        Finding(
            "OK",
            "仓库",
            "AGENTS.md",
            "存在，将遵守其中的项目规则"
            if agents_path.exists()
            else "未提供；可由用户自行创建并写入项目规则",
        )
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
        findings.append(
            Finding(
                "WARNING",
                "仓库",
                "Git 基线",
                "尚无首个提交；默认仍严格检查新增文件。若明确导入老项目历史基线，"
                "可在 check_diff.py 中使用 --allow-initial-baseline，仅放宽可解释的编码、BOM、换行和末尾换行属性；"
                "不可解码、二进制和替换字符仍会阻断",
            )
        )
    if status:
        findings.append(Finding("WARNING", "工作区", "未提交修改", "存在；修复配置前不要覆盖这些修改"))
    else:
        findings.append(Finding("OK", "工作区", "未提交修改", "干净"))


def _template(name: str) -> bytes:
    """返回只用于缺失文件的保守模板。"""
    templates = {
        ".editorconfig": """root = true\n\n[*]\n# 老项目不强制全局编码和换行，避免编辑器保存时改写历史文件。\ncharset = unset\nend_of_line = unset\ninsert_final_newline = unset\ntrim_trailing_whitespace = false\nindent_style = space\nindent_size = 4\n""",
        ".gitattributes": """# 老项目默认保留文件原始字节，避免 Git 自动转换换行。\n* -text\n""",
        ".gitignore": """# 常见 C++ 构建和 IDE 输出\n/build/\n/out/\n/.vs/\n/CMakeFiles/\nCMakeCache.txt\ncompile_commands.json\n\n# 仅共享项目级 VS Code 设置\n/.vscode/*\n!/.vscode/settings.json\n""",
    }
    return templates[name].encode("utf-8")


def repair_repo(repo: Path, install_hook: bool = False) -> list[str]:
    """创建缺失规则文件，并校正已明确授权的仓库本地 Git 保护项。"""
    created: list[str] = []
    for name in (".editorconfig", ".gitattributes", ".gitignore"):
        path = repo / name
        if not path.exists():
            path.write_bytes(_template(name))
            created.append(name)
    if _config(repo, "--local", "core.autocrlf").lower() != "false":
        subprocess.run(["git", "config", "--local", "core.autocrlf", "false"], cwd=str(repo), check=True)
        created.append("git local core.autocrlf=false")
    if not _config(repo, "--local", "core.safecrlf"):
        subprocess.run(["git", "config", "--local", "core.safecrlf", "warn"], cwd=str(repo), check=True)
        created.append("git local core.safecrlf=warn")
    if os.name == "nt" and _config(repo, "--local", "core.filemode").lower() != "false":
        subprocess.run(["git", "config", "--local", "core.filemode", "false"], cwd=str(repo), check=True)
        created.append("git local core.filemode=false")
    if install_hook:
        from install_hook import install

        created.append(str(install(repo)))
    return created


def _is_windows_admin() -> bool:
    """判断当前 Windows 进程是否已获得管理员令牌。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _ps_single_quote(value: str) -> str:
    """转义 PowerShell 单引号字符串。"""
    return "'" + value.replace("'", "''") + "'"


def _run_elevated_install(commands: list[list[str]]) -> tuple[bool, str]:
    """生成临时 PowerShell 脚本并通过 UAC 请求管理员权限执行。"""
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        return False, "未找到 PowerShell，无法申请 UAC 管理员权限"
    descriptor, temporary_path = tempfile.mkstemp(prefix="jojo-code-guard-", suffix=".ps1")
    os.close(descriptor)
    script_path = Path(temporary_path)
    payload = json.dumps(commands, ensure_ascii=False)
    script = f'''# 此脚本由 AI 生成，用于在用户授权 UAC 后安装设备工具。
$commands = ConvertFrom-Json -InputObject @'
{payload}
'@
foreach ($command in $commands) {{
    $executable = [string]$command[0]
    $commandArguments = @($command | Select-Object -Skip 1)
    Write-Host "正在执行：$executable"
    & $executable @commandArguments
    if ($LASTEXITCODE -ne 0) {{
        Write-Warning "命令失败，退出码：$LASTEXITCODE"
    }}
}}
Write-Host "安装脚本执行完毕。"
Read-Host "按 Enter 关闭此管理员窗口"
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
'''
    script_path.write_text(script, encoding="utf-8", newline="\n")
    argument_list = "-NoProfile -ExecutionPolicy Bypass -File " + _ps_single_quote(str(script_path))
    command = (
        "Start-Process -FilePath "
        + _ps_single_quote(powershell)
        + " -ArgumentList "
        + _ps_single_quote(argument_list)
        + " -WorkingDirectory "
        + _ps_single_quote(str(Path.cwd()))
        + " -Verb RunAs"
    )
    code, output = _run([powershell, "-NoProfile", "-Command", command])
    if code != 0:
        return False, output or "启动 UAC 管理员安装脚本失败"
    return True, f"已创建管理员安装脚本：{script_path}；请在 UAC 提示中选择“是”并授权"


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
        if system == "Windows" and not _is_windows_admin():
            launched, message = _run_elevated_install(commands)
            findings.append(Finding("ACTION_REQUIRED" if launched else "BLOCKED", "设备安装", "UAC", message))
        else:
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
    parser.add_argument(
        "--sync-global-rules",
        choices=("overwrite", "merge"),
        help="同步全局规则：overwrite 覆盖，merge 保留原文并更新受管块",
    )
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
        bash = shutil.which("bash")
        if bash:
            code, output = _run([bash, "--norc", "--noprofile", "-c", "exit 0"])
            message = "Claude/Codex 生命周期 Hook 可调用 Bash"
            findings.append(
                Finding("OK" if code == 0 else "WARNING", "设备", "Git Bash", message if code == 0 else output)
            )
        elif git_bash.exists():
            findings.append(
                Finding(
                    "ACTION_REQUIRED",
                    "设备",
                    "Git Bash",
                    f"已安装于 {git_bash}，但 bash 不在 PATH；Claude/Codex 生命周期 Hook 无法按当前命令启动",
                )
            )
        else:
            findings.append(
                Finding(
                    "WARNING",
                    "设备",
                    "Git Bash",
                    "未找到；主 Skill 仍可使用，但 Claude/Codex 的 Bash 生命周期 Hook 不会运行",
                )
            )
    if repo is None:
        findings.append(Finding("BLOCKED", "仓库", "当前目录", repo_error or "不是 Git 工作树"))
    else:
        _check_git(findings, repo)
        _check_repo(findings, repo)

    # 两端插件状态无论是否在仓库中都只读检查
    expected_version = _check_source_plugin_version(findings)
    _check_claude_hooks(findings, expected_version=expected_version)
    _check_codex_plugin(findings, expected_version=expected_version)
    _check_global_rules(findings, mode=options.sync_global_rules)

    has_action = options.repair or options.install_hook or options.install_tools or options.sync_global_rules
    if has_action:
        if not options.yes:
            if options.sync_global_rules:
                label = "覆盖" if options.sync_global_rules == "overwrite" else "合并"
                findings.append(
                    Finding(
                        "ACTION_REQUIRED",
                        "全局规则",
                        "确认",
                        f"已选择{label}模式；确认差异后添加 --yes",
                    )
                )
            findings.append(
                Finding(
                    "ACTION_REQUIRED",
                    "修复",
                    "确认",
                    "将要写入仓库、用户规则或安装工具；确认后添加 --yes",
                )
            )
        else:
            try:
                if repo is None and (options.repair or options.install_hook):
                    raise RuntimeError("修复仓库前必须在 Git 工作树中运行 doctor")
                if options.repair:
                    created = repair_repo(repo, install_hook=options.install_hook)
                    findings.append(Finding("OK", "修复", "仓库", "已创建：" + (", ".join(created) or "无需创建")))
                elif options.install_hook:
                    from install_hook import install

                    findings.append(Finding("OK", "修复", "Git hook", str(install(repo))))
                if options.sync_global_rules:
                    changed = _sync_global_rules(options.sync_global_rules)
                    label = "覆盖" if options.sync_global_rules == "overwrite" else "合并"
                    message = "、".join(changed) if changed else "目标已是期望内容，无需写入"
                    findings.append(Finding("OK", "全局规则", label, message))
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
            print("[2] 补齐缺失仓库配置：doctor.py --repair --yes")
            print("[3] 可选安装仓库私有 pre-commit：doctor.py --install-hook --yes")
            print("[4] 安装或更新缺失设备工具：doctor.py --install-tools --yes")
            print("[5] 预览覆盖全局规则：doctor.py --sync-global-rules overwrite")
            print("[6] 预览合并全局规则：doctor.py --sync-global-rules merge")
    return 1 if any(item.level == "BLOCKED" for item in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())

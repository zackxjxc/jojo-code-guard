#!/usr/bin/env python3
"""啾啾代码守护：只读诊断设备、Git 和仓库；可选地补齐缺失保护设施。"""

from __future__ import annotations

import argparse
import ctypes
import difflib
import errno
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from guard_core import find_repo, inspect_bytes, run_git


# 插件的稳定标识和两端运行时必需资源
PLUGIN_ID = "jojo-code-guard@jojo-code-guard"
CLAUDE_PLUGIN_ID = PLUGIN_ID
CODEX_PLUGIN_ID = PLUGIN_ID
REMOTE_PLUGIN_MANIFEST_URL = (
    "https://raw.githubusercontent.com/zackxjxc/jojo-code-guard/master/.claude-plugin/plugin.json"
)
PLUGIN_RESOURCE_SHA256 = {
    "hooks/hooks.json": "85d2aa67269ba75b1e176b8fb34cead14b1e174c56356addf05f9228fdf9ccdc",
    "hooks/session-start": "8cc60b565779cddb8d09f32a68e21b0d0ff7a48619926d7970b52396eea8a192",
    "hooks/post-write-check": "6a01641c84ae4353de69465e2727e6361e891776e4c1202ce94ddecd348fd7ca",
    "hooks/run-hook.cmd": "9ca38a90bf001ddc017dcac21014ef4aa50126ce8d7cc7dc606666f44efe7d1b",
    "skills/jojo-code-guard/SKILL.md": "92cf16fa7fb3efbf6ae774836a6711da57bc44f36a45dc8122a9233ac925c086",
    "skills/jojo-code-guard/PowerShell规则.md": "a915ddd7ac368303a6d97bec8ef7b896e139514a37f5aa948d994ab721e4090e",
    "skills/jojo-code-guard/references/通用行为规则.md": "7c57deb8b0d3a10434eeaa400997b8290985de9448aae090e7017a8854db424c",
    "skills/jojo-code-guard/references/通用文件守护.md": "af038fb86597ba4d532c3f9cf3fbabbf888be40d27ad9602fed724a47f0811c4",
    "skills/jojo-code-guard/references/长任务输出控制.md": "d0b11c6534eded9805df02acd34c354b19f5fe78fca95ab1e75ce5d8f463fd4b",
    "skills/jojo-code-guard/references/C++专项规则.md": "9e9a8e8b87ef09dc2ab38fa99b65d6c5ae1acd4e68d86d0f57e9220fe6250ef1",
    "skills/jojo-code-guard/references/Git操作规则.md": "a42256a13ddc2fb5409ec4caac8a8cda03f8ec2765cc6edcd94833c450add01e",
    "skills/jojo-code-guard/references/usage.md": "aa38d11206d37f97202185a7f2747eecbdc8fbdebfdf7a9755d3d088534214b7",
    "skills/jojo-code-guard/references/自动加载规则.md": "36b76ac7c28b1cc62850a18fbd4ab13c4d55b1a825831e79a086d76937c52df9",
    "skills/jojo-code-guard/scripts/check_diff.py": "f3949b144cf69fed40aaffff34146c6f624bfab3d2f9f5709ff3024e5e6f911d",
    "skills/jojo-code-guard/scripts/guard_core.py": "52bf8487fc218aa4134c4c5891f8700c2f56c9fc49ea7642854c0e0041c44f81",
    "skills/jojo-code-guard/scripts/hook_baseline.py": "7f95628833e04decd47afe349dafd96e3c8b4a99ea604cb2816131d20c320317",
    "skills/jojo-code-guard/scripts/hook_check.py": "aae71657777aa0cae609cc00d7cefc273b4d3ee2afc59b06d20984c8c3b9d0ca",
    "skills/jojo-code-guard/scripts/install_hook.py": "b2d7b4e0bcb30c004369671c5577ea36a003dc4a93c6254bb45715f6d382057d",
    "skills/jojo-code-guard-doctor/SKILL.md": "b16a6601d323f3dd6b049615dace0b7de8f47bfdc572a35a1ddbddc4713a0791",
    "skills/jojo-code-guard-check-diff/SKILL.md": "73817f07722b44d95e76e4a324d7a0d7a5a1413060e4242a6c88c033b07033fe",
    "skills/jojo-code-guard-help/SKILL.md": "8c383ae710939c68ebd0259d992f5ef701d3fe80fc9e6eaad17cabd118b76091",
    "skills/jojo-code-guard/agents/openai.yaml": "48a6c37b1a579220dfd6206e580823cd546a0853050e624bfc244084f00a521b",
    "skills/jojo-code-guard-doctor/agents/openai.yaml": "e4afd27e7293924d206813efa5feb00edb463ada7ca92f8d86defb8df1392cd8",
    "skills/jojo-code-guard-check-diff/agents/openai.yaml": "1b9e108bb9610ba2d8b6ea7f8a68119d06d06523bebcbbc4c8aca89ff6592c2a",
    "skills/jojo-code-guard-help/agents/openai.yaml": "c907aeb761e7afc7f629c34f239dd962a534704281006862cd8dcace2d480ee6",
}
CLAUDE_PLUGIN_RESOURCE_SHA256 = {
    ".claude-plugin/plugin.json": "d55227a6c760890a99137692ab51fb5aebf21d435675e56c508b48c649a871be",
    ".claude-plugin/marketplace.json": "9b90a7dd6c2dde5461bf89042c6c7f46dccce4e90d99255c3ccd35414f5e51c5",
    "commands/doctor.md": "1e739c4bec55f99b92dac4a61028bb2399b09b2360fdc4f34774bb391e036bd2",
    "commands/check-diff.md": "63e07127637131767d5f03618d3689b8e0d1387ffc29f66f391bbfa65fe0512f",
    "commands/help.md": "41a764ee9cedcbd7e8587fea18e07ad41da64473cc195f245480f47d5848b8f8",
}
CODEX_PLUGIN_RESOURCE_SHA256 = {
    ".codex-plugin/plugin.json": "e74210f7993624f2e6125492a89bd41718022cee90345bb7fc089906ee3290d3",
}
PUBLIC_SKILL_ENTRYPOINTS = frozenset(
    {
        "skills/jojo-code-guard/SKILL.md",
        "skills/jojo-code-guard-doctor/SKILL.md",
        "skills/jojo-code-guard-check-diff/SKILL.md",
        "skills/jojo-code-guard-help/SKILL.md",
    }
)
CLAUDE_COMMAND_ENTRYPOINTS = frozenset(
    {
        "commands/doctor.md",
        "commands/check-diff.md",
        "commands/help.md",
    }
)
CLAUDE_EMPTY_COMPONENT_FILES = ("SKILL.md", ".mcp.json", ".lsp.json", "settings.json")
CLAUDE_EMPTY_COMPONENT_DIRECTORIES = (
    "monitors",
    "bin",
    "workflows",
    "output-styles",
    "themes",
)
CODEX_EMPTY_COMPONENT_FILES = (".mcp.json", ".app.json")
# doctor.py 是完整性校验器自身，不能内嵌稳定的自摘要；仍须作为运行入口检查存在性。
PLUGIN_REQUIRED_FILES = (
    "skills/jojo-code-guard/scripts/doctor.py",
)
CLAUDE_PLUGIN_REQUIRED_FILES = (
    *PLUGIN_REQUIRED_FILES,
    *PLUGIN_RESOURCE_SHA256,
    *CLAUDE_PLUGIN_RESOURCE_SHA256,
)
CODEX_PLUGIN_REQUIRED_FILES = (
    *PLUGIN_REQUIRED_FILES,
    *PLUGIN_RESOURCE_SHA256,
    *CODEX_PLUGIN_RESOURCE_SHA256,
)

# doctor 管理的用户级规则目标和自动加载节标题
GLOBAL_RULE_TARGET_RELATIVE_PATHS = (
    Path(".claude") / "CLAUDE.md",
    Path(".codex") / "AGENTS.md",
)
GLOBAL_RULE_CREATED_TITLE = "# 全局规则"
GLOBAL_RULE_SECTION_HEADING = "## jojo-code-guard 自动加载（必须严格遵守）"
GLOBAL_RULE_SECTION_PATTERN = re.compile(
    r"^##[ \t]+jojo-code-guard 自动加载(?:（必须严格遵守）)?[ \t]*$",
    re.MULTILINE,
)
GLOBAL_RULE_NEXT_SECTION_PATTERN = re.compile(r"^#{1,2}[ \t]+", re.MULTILINE)


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
    """严格读取 UTF-8，并保留文件中的原始换行符。"""
    try:
        return path.read_bytes().decode("utf-8-sig", errors="strict")
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


def _tracked_batch_paths(repo: Path) -> list[str]:
    """返回被 Git 跟踪的 Windows 批处理文件。"""
    output = run_git(repo, ["ls-files", "-z", "--", "*.bat", "*.cmd"], check=False)
    return [item.decode("utf-8", errors="surrogateescape") for item in output.split(b"\0") if item]


def _check_attr(repo: Path, paths: list[str]) -> dict[str, dict[str, str]]:
    """用 NUL 分隔输出读取路径最终生效的 Git 属性。"""
    if not paths:
        return {}
    output = run_git(repo, ["check-attr", "-z", "text", "eol", "--", *paths], check=False)
    fields = output.split(b"\0")
    attributes: dict[str, dict[str, str]] = {}
    for index in range(0, len(fields) - 2, 3):
        path = fields[index].decode("utf-8", errors="surrogateescape")
        name = fields[index + 1].decode("ascii", errors="replace")
        value = fields[index + 2].decode("utf-8", errors="replace")
        attributes.setdefault(path, {})[name] = value
    return attributes


def _batch_attributes_are_crlf(values: dict[str, str]) -> bool:
    """判断批处理路径的最终属性是否为标准 CRLF 配置。"""
    return values.get("text") == "set" and values.get("eol") == "crlf"


def _check_batch_contents(findings: list[Finding], repo: Path, paths: list[str]) -> None:
    """检查被跟踪批处理文件的工作区编码、BOM 和换行。"""
    for relative in paths:
        path = repo / relative
        try:
            if path.is_symlink() or not path.is_file():
                continue
            info = inspect_bytes(path.read_bytes())
        except OSError as error:
            findings.append(Finding("WARNING", "批处理", relative, f"无法读取工作区文件：{error}"))
            continue
        if info.error or info.encoding != "utf-8":
            detail = info.error or f"当前编码为 {info.encoding}"
            findings.append(Finding("ACTION_REQUIRED", "批处理", relative, f"不是 UTF-8：{detail}"))
        if info.bom != "none":
            findings.append(Finding("ACTION_REQUIRED", "批处理", relative, f"必须为 UTF-8 无 BOM，当前 BOM={info.bom}"))
        if info.eol == "mixed":
            findings.append(Finding("ACTION_REQUIRED", "批处理", relative, "存在 LF/CRLF 混合换行，必须统一为 CRLF"))
        elif info.eol == "lf":
            findings.append(Finding("ACTION_REQUIRED", "批处理", relative, "当前为 LF，必须使用 CRLF"))
        elif info.eol not in {"none", "crlf"}:
            findings.append(Finding("ACTION_REQUIRED", "批处理", relative, f"当前换行为 {info.eol}，必须使用 CRLF"))
        if not any(item.area == "批处理" and item.item == relative for item in findings):
            findings.append(Finding("OK", "批处理", relative, "UTF-8 无 BOM + CRLF"))


def _check_attributes(findings: list[Finding], repo: Path) -> None:
    """检查 Git 属性及被跟踪批处理文件的实际字节。"""
    path = repo / ".gitattributes"
    batch_paths = _tracked_batch_paths(repo)
    if not path.exists():
        findings.append(
            Finding(
                "ACTION_REQUIRED",
                "仓库",
                ".gitattributes",
                "缺失；新模板将加入 * -text、*.bat text eol=crlf 和 *.cmd text eol=crlf",
            )
        )
        if batch_paths:
            findings.append(Finding("ACTION_REQUIRED", "仓库", "批处理 CRLF 属性", "仓库存在批处理文件，但没有 Git CRLF 检出规则"))
        _check_batch_contents(findings, repo, batch_paths)
        return
    content = _read_utf8(path)
    if content is None:
        findings.append(Finding("BLOCKED", "仓库", ".gitattributes", "不是可严格读取的 UTF-8 文件"))
        _check_batch_contents(findings, repo, batch_paths)
        return
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    standard_batch_rules = {"*.bat text eol=crlf", "*.cmd text eol=crlf"}
    risky: list[str] = []
    for line in lines:
        normalized = " ".join(line.lower().split())
        if normalized in standard_batch_rules:
            continue
        tokens = normalized.split()
        if any(
            token in {"text", "text=auto", "eol=lf", "eol=crlf"}
            or token.startswith("working-tree-encoding=")
            for token in tokens
        ):
            risky.append(line)
    preserves_default = any(" ".join(line.lower().split()).startswith("* -text") for line in lines)
    if risky:
        message = "存在可能规范化老文件的具体规则：" + "; ".join(risky[:6])
        if preserves_default:
            message += "；具体路径规则会覆盖 * -text 的默认值"
        findings.append(Finding("WARNING", "仓库", ".gitattributes", message))
    elif preserves_default:
        findings.append(Finding("OK", "仓库", ".gitattributes", "已设置 * -text，默认不会替换老文件换行"))
    else:
        findings.append(Finding("WARNING", "仓库", ".gitattributes", "存在但未声明老项目的字节保真策略"))

    probes = [".jojo-code-guard-probe.bat", ".jojo-code-guard-probe.cmd"]
    effective = _check_attr(repo, probes + batch_paths)
    invalid = [
        (relative, effective.get(relative, {}))
        for relative in probes + batch_paths
        if not _batch_attributes_are_crlf(effective.get(relative, {}))
    ]
    if invalid and batch_paths:
        details = "; ".join(
            f"{relative}: text={values.get('text', 'unspecified')}, eol={values.get('eol', 'unspecified')}"
            for relative, values in invalid[:8]
        )
        findings.append(
            Finding(
                "ACTION_REQUIRED",
                "仓库",
                "批处理 CRLF 属性",
                "最终生效属性不是 text=set、eol=crlf，可能被后续或更具体规则覆盖：" + details,
            )
        )
    elif not invalid:
        findings.append(Finding("OK", "仓库", "批处理 CRLF 属性", "*.bat/*.cmd 最终生效属性为 text=set、eol=crlf"))
    _check_batch_contents(findings, repo, batch_paths)


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
        findings.append(
            Finding(
                "WARNING",
                "插件源码",
                "Version",
                "当前为直接 Skill 安装或未随附客户端 manifest；无法确定发布版本，跳过版本一致性比较",
            )
        )
        return None
    unique = set(versions.values())
    summary = "，".join(f"{client}={version}" for client, version in sorted(versions.items()))
    if len(unique) != 1:
        findings.append(Finding("BLOCKED", "插件源码", "Version", "客户端版本不一致：" + summary))
        return None
    version = next(iter(unique))
    findings.append(Finding("OK", "插件源码", "Version", f"当前 doctor 随附版本 {version}（{summary}）"))
    return version


def _fetch_remote_plugin_version() -> tuple[str | None, str | None]:
    """从发布仓库读取最新版号，网络失败时返回可展示的原因。"""
    request = urllib.request.Request(
        REMOTE_PLUGIN_MANIFEST_URL,
        headers={"User-Agent": "jojo-code-guard-doctor"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = response.read(64 * 1024 + 1)
        if len(data) > 64 * 1024:
            return None, "远端 manifest 超过 64 KiB"
        manifest = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as error:
        return None, str(error)
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not version.strip():
        return None, "远端 manifest 缺少有效 version"
    return version.strip(), None


def _semantic_version_key(version: str) -> tuple[int, int, int] | None:
    """解析 doctor 使用的三段式发布版本号。"""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(part) for part in match.groups()) if match else None


def _check_plugin_update(findings: list[Finding], current_version: str | None) -> None:
    """比较当前 Skill 与远端发布版本，并给出客户端更新命令。"""
    if current_version is None:
        findings.append(Finding("WARNING", "插件更新", "远端版本", "当前版本未知，跳过更新检查"))
        return
    remote_version, error = _fetch_remote_plugin_version()
    if remote_version is None:
        findings.append(
            Finding("WARNING", "插件更新", "远端版本", f"无法检查更新：{error or '未知错误'}")
        )
        return
    current_key = _semantic_version_key(current_version)
    remote_key = _semantic_version_key(remote_version)
    if current_key is None or remote_key is None:
        findings.append(
            Finding(
                "WARNING",
                "插件更新",
                "远端版本",
                f"无法比较版本号：当前 {current_version}，远端 {remote_version}",
            )
        )
        return
    if remote_key > current_key:
        findings.append(
            Finding(
                "ACTION_REQUIRED",
                "插件更新",
                "发现新版本",
                f"当前 {current_version}，远端 {remote_version}；Skill 不会自行更新。"
                "Codex 请依次运行 `codex plugin marketplace upgrade jojo-code-guard`、"
                "`codex plugin add jojo-code-guard@jojo-code-guard`；"
                "Claude Code 请依次运行 `/plugin marketplace update jojo-code-guard`、"
                "`/plugin install jojo-code-guard@jojo-code-guard`，最后重启客户端",
            )
        )
    else:
        findings.append(
            Finding("OK", "插件更新", "远端版本", f"当前 {current_version}，远端 {remote_version}，无需更新")
        )


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


def _resource_sha256(path: Path) -> str:
    """流式计算受管资源摘要，避免把文件大小当作可信前提。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plugin_path_is_link_like(path: Path) -> bool:
    """识别插件目录中的符号/硬链接和 Windows junction 等 reparse point。"""
    if path.is_symlink():
        return True
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_point)


def _check_plugin_install_root(
    findings: list[Finding],
    client: str,
    install_path: Path,
) -> bool:
    """在读取任何安装资源前拒绝符号链接、junction 和 reparse 根。"""
    if not _plugin_path_is_link_like(install_path):
        return True
    findings.append(
        Finding(
            "BLOCKED",
            client,
            "Plugin resource integrity",
            f"安装根是链接或 reparse point，拒绝作为信任根：{install_path}",
        )
    )
    return False


def _raise_plugin_walk_error(error: OSError) -> None:
    """让 os.walk 的目录读取失败进入完整性 BLOCKED 分支。"""
    raise error


def _plugin_component_entrypoints(root: Path, suffix: str | None) -> list[Path]:
    """枚举插件组件文件并阻止遍历目录链接。"""
    if _plugin_path_is_link_like(root):
        return [root]
    if not root.is_dir():
        return []
    candidates: list[Path] = []
    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
        onerror=_raise_plugin_walk_error,
    ):
        current = Path(directory)
        for name in list(directory_names):
            path = current / name
            if _plugin_path_is_link_like(path):
                candidates.append(path)
                directory_names.remove(name)
        for name in file_names:
            path = current / name
            if (
                _plugin_path_is_link_like(path)
                or suffix is None
                or path.suffix.lower() == suffix
            ):
                candidates.append(path)
    return candidates


def _plugin_skill_entrypoints(root: Path) -> list[Path]:
    """递归枚举 Skill，并阻止目录链接隐藏新的发现入口。"""
    if _plugin_path_is_link_like(root):
        return [root]
    if not root.is_dir():
        return []
    candidates: list[Path] = []
    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
        onerror=_raise_plugin_walk_error,
    ):
        current = Path(directory)
        for name in list(directory_names):
            path = current / name
            if _plugin_path_is_link_like(path):
                candidates.append(path)
                directory_names.remove(name)
        candidates.extend(current / name for name in file_names if name.casefold() == "skill.md")
    return candidates


def _unexpected_plugin_entrypoints(client: str, install_path: Path) -> list[str]:
    """列出客户端会自动发现、但本插件没有声明的运行入口。"""
    skills_root = install_path / "skills"
    candidates = _plugin_skill_entrypoints(skills_root)
    allowed = PUBLIC_SKILL_ENTRYPOINTS
    if client == "Claude":
        commands_root = install_path / "commands"
        agents_root = install_path / "agents"
        candidates.extend(_plugin_component_entrypoints(commands_root, ".md"))
        candidates.extend(_plugin_component_entrypoints(agents_root, ".md"))
        for relative in CLAUDE_EMPTY_COMPONENT_FILES:
            path = install_path / relative
            if path.exists() or _plugin_path_is_link_like(path):
                candidates.append(path)
        for relative in CLAUDE_EMPTY_COMPONENT_DIRECTORIES:
            root = install_path / relative
            candidates.extend(_plugin_component_entrypoints(root, None))
        allowed = allowed | CLAUDE_COMMAND_ENTRYPOINTS
    elif client == "Codex":
        for relative in CODEX_EMPTY_COMPONENT_FILES:
            path = install_path / relative
            if path.exists() or _plugin_path_is_link_like(path):
                candidates.append(path)

    return sorted(
        {
            path.relative_to(install_path).as_posix()
            for path in candidates
            if path.relative_to(install_path).as_posix() not in allowed
        }
    )


def _check_plugin_resource_integrity(
    findings: list[Finding], client: str, install_path: Path
) -> bool:
    """用 doctor 内置摘要校验可执行资源和提示规则，并拒绝目录外符号链接。"""
    failures: list[str] = []
    if not _check_plugin_install_root(findings, client, install_path):
        return False
    try:
        root = install_path.resolve(strict=True)
    except OSError as error:
        findings.append(
            Finding(
                "BLOCKED",
                client,
                "Plugin resource integrity",
                f"无法安全解析安装根：{install_path}（{error}）",
            )
        )
        return False
    resource_hashes = dict(PLUGIN_RESOURCE_SHA256)
    if client == "Claude":
        resource_hashes.update(CLAUDE_PLUGIN_RESOURCE_SHA256)
        required_files = CLAUDE_PLUGIN_REQUIRED_FILES
    else:
        resource_hashes.update(CODEX_PLUGIN_RESOURCE_SHA256)
        required_files = CODEX_PLUGIN_REQUIRED_FILES
    try:
        unexpected = _unexpected_plugin_entrypoints(client, install_path)
    except OSError as error:
        failures.append(f"公开入口（无法枚举：{error}）")
    else:
        failures.extend(f"{relative}（未声明公开入口）" for relative in unexpected)
    for relative in required_files:
        path = install_path / relative
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            if _plugin_path_is_link_like(path) or not path.is_file():
                failures.append(f"{relative}（不是独占普通文件）")
                continue
            expected = resource_hashes.get(relative)
            actual = _resource_sha256(path) if expected is not None else None
        except (OSError, ValueError) as error:
            failures.append(f"{relative}（无法安全读取：{error}）")
            continue
        if expected is not None and actual != expected:
            failures.append(f"{relative}（SHA-256 不匹配）")

    if failures:
        findings.append(
            Finding(
                "BLOCKED",
                client,
                "Plugin resource integrity",
                "安装资源被修改、损坏或越出插件目录：" + "，".join(failures),
            )
        )
        return False
    findings.append(
        Finding(
            "OK",
            client,
            "Plugin resource integrity",
            f"{len(resource_hashes)} 个受管资源摘要一致",
        )
    )
    return True


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
    if not _check_plugin_install_root(findings, "Codex", cache_root):
        return
    try:
        candidates = sorted(cache_root.iterdir(), key=lambda path: path.name) if cache_root.is_dir() else []
    except OSError as error:
        findings.append(Finding("BLOCKED", "Codex", "Plugin cache", f"无法读取 {cache_root}：{error}"))
        return
    installations: list[Path] = []
    for path in candidates:
        if not _check_plugin_install_root(findings, "Codex", path):
            continue
        if path.is_dir():
            installations.append(path)
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

    # 多缓存时虽然无法判断客户端实际加载哪一个，所有候选仍必须逐一通过资源和入口完整性检查。
    for install_path, _ in valid:
        _check_plugin_resource_integrity(findings, "Codex", install_path)

    if len(valid) > 1:
        for install_path, version in valid:
            if install_path.name != "local" and install_path.name != version:
                findings.append(
                    Finding(
                        "BLOCKED",
                        "Codex",
                        "Plugin version",
                        f"安装 manifest={version}，但缓存目录={install_path.name}",
                    )
                )

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
    if not _check_plugin_install_root(findings, "Claude", install_path):
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
        return
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
        _check_plugin_resource_integrity(findings, "Claude", install_path)
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


def _global_rule_section_source_path() -> Path:
    """定位 Skill 内置的自动加载节源文件。"""
    return Path(__file__).resolve().parents[1] / "references" / "自动加载规则.md"


def _global_rule_target_paths() -> list[Path]:
    """生成 Claude 与 Codex 的用户级规则路径，并尊重 CODEX_HOME。"""
    return [_find_claude_home() / "CLAUDE.md", _find_codex_home() / "AGENTS.md"]


def _normalize_newlines(text: str) -> str:
    """将文本换行统一为 LF，仅用于内容比较。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class _GlobalRuleMetadata:
    """需要跨原子替换保真的文件系统元数据。"""

    windows_attributes: int | None
    windows_dacl: bytes | None
    alternate_streams: tuple[tuple[str, bytes], ...]
    xattrs: tuple[tuple[str, bytes], ...]
    uid: int | None
    gid: int | None
    file_flags: int | None
    darwin_acl: bytes | None


@dataclass(frozen=True)
class _GlobalRuleSnapshot:
    """一个全局规则目标的冲突检测快照。"""

    exists: bool
    data: bytes
    identity: tuple[int, int, int, int, int] | None
    mode: int | None
    metadata: _GlobalRuleMetadata | None
    recovery_path: Path | None = field(default=None, compare=False, repr=False)


@dataclass
class _GlobalRuleWrite:
    """记录一次可能已经落盘的写入，供失败时安全回滚。"""

    target: Path
    original: _GlobalRuleSnapshot
    intended_data: bytes
    committed: _GlobalRuleSnapshot | None = None


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


def _global_rule_diff(target: Path, current_data: bytes, proposed_data: bytes) -> str:
    """生成当前用户文件与拟议节级更新之间的受限差异。"""
    current_text = current_data.decode("utf-8-sig", errors="replace").splitlines(keepends=True)
    proposed_text = proposed_data.decode("utf-8-sig", errors="replace").splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            current_text,
            proposed_text,
            fromfile=str(target),
            tofile=f"{target}（拟议）",
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


def _global_rule_section_text(source_data: bytes) -> str:
    """读取并验证只包含一个当前自动加载节的内置源。"""
    payload = source_data[3:] if source_data.startswith(b"\xef\xbb\xbf") else source_data
    try:
        source_text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"自动加载节源文件不是严格 UTF-8：{error}") from error
    normalized = _normalize_newlines(source_text).strip("\n")
    matches = list(GLOBAL_RULE_SECTION_PATTERN.finditer(normalized))
    if len(matches) != 1 or matches[0].start() != 0:
        raise RuntimeError("自动加载节源文件必须且只能包含一个受管节")
    if matches[0].group(0) != GLOBAL_RULE_SECTION_HEADING:
        raise RuntimeError("自动加载节源文件必须使用当前受管标题")
    headings = _markdown_headings(normalized)
    if (
        not headings
        or headings[0][0] != 0
        or headings[0][2:] != (2, "atx", GLOBAL_RULE_SECTION_HEADING.removeprefix("## "))
        or len(headings) != 1
    ):
        raise RuntimeError("自动加载节源文件不能包含其他一级或二级标题")
    return normalized


def _global_rule_line_ending(text: str) -> str:
    """读取用户全局规则的单一换行类型。"""
    crlf = text.count("\r\n")
    remainder = text.replace("\r\n", "")
    lf_only = remainder.count("\n")
    cr_only = remainder.count("\r")
    if sum(bool(value) for value in (crlf, lf_only, cr_only)) > 1:
        raise RuntimeError("目标使用混合换行，拒绝更新自动加载节")
    return "\r\n" if crlf else "\r" if cr_only else "\n"


def _markdown_visible_text(body: str, in_html_comment: bool) -> tuple[str, bool]:
    """移除一行中的 HTML 注释片段，同时保留注释外的 Markdown。"""
    visible: list[str] = []
    cursor = 0
    while cursor < len(body):
        if in_html_comment:
            closing = body.find("-->", cursor)
            if closing < 0:
                return "".join(visible), True
            cursor = closing + 3
            in_html_comment = False
            continue
        opening = body.find("<!--", cursor)
        if opening < 0:
            visible.append(body[cursor:])
            break
        visible.append(body[cursor:opening])
        cursor = opening + 4
        in_html_comment = True
    return "".join(visible), in_html_comment


def _markdown_headings(text: str) -> list[tuple[int, int, int, str, str]]:
    """返回代码围栏和 HTML 注释之外的一、二级 ATX/Setext 标题。"""
    first_line = re.split(r"\r\n|\r|\n", text, maxsplit=1)[0]
    if re.fullmatch(r"---[ \t]*", first_line):
        raise RuntimeError("目标包含 YAML front matter，无法可靠判断自动加载节边界")
    headings: list[tuple[int, int, int, str, str]] = []
    fence_character = ""
    fence_length = 0
    in_html_comment = False
    saw_container_marker = False
    container_followed_by_blank = False
    paragraph_start: int | None = None
    paragraph_parts: list[str] = []
    paragraph_ambiguous_after_container = False
    link_reference_ambiguous = False
    offset = 0
    markdown_lines = re.finditer(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
    for line_match in markdown_lines:
        line = line_match.group(0)
        if not line:
            continue
        body = line.rstrip("\r\n")
        fence = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", body)
        if fence_character:
            if fence:
                marker = fence.group(1)
                closes_fence = (
                    marker[0] == fence_character
                    and len(marker) >= fence_length
                    and re.fullmatch(r"[ \t]*", fence.group(2)) is not None
                )
                if closes_fence:
                    fence_character = ""
                    fence_length = 0
            paragraph_start = None
            paragraph_parts = []
            link_reference_ambiguous = False
            offset += len(line)
            continue

        if in_html_comment:
            _, in_html_comment = _markdown_visible_text(body, True)
            paragraph_start = None
            paragraph_parts = []
            link_reference_ambiguous = False
            offset += len(line)
            continue

        # 围栏起始优先于 info string 中形似 HTML 注释的文本。
        if fence:
            marker = fence.group(1)
            if marker[0] == "`" and "`" in fence.group(2):
                raise RuntimeError("反引号代码围栏的信息串包含反引号，无法可靠判断自动加载节边界")
            fence_character = marker[0]
            fence_length = len(marker)
            paragraph_start = None
            paragraph_parts = []
            link_reference_ambiguous = False
            offset += len(line)
            continue

        if re.match(r"^[ ]{0,3}<!--", body):
            _, in_html_comment = _markdown_visible_text(body, False)
            paragraph_start = None
            paragraph_parts = []
            paragraph_ambiguous_after_container = False
            link_reference_ambiguous = False
            offset += len(line)
            continue

        if re.match(
            r"^[ ]{0,3}(?:</?[A-Za-z][A-Za-z0-9-]*(?:[ \t]|$)|"
            r"<(?:script|pre|style|textarea)(?:[ \t>]|$)|"
            r"</?[A-Za-z][^>\r\n]*>|<\?[^\r\n]*|<![A-Z][^\r\n]*|<!\[CDATA\[)",
            body,
            flags=re.IGNORECASE,
        ):
            raise RuntimeError("目标包含行首原始 HTML 块，无法可靠判断自动加载节边界")

        if re.match(
            r"^[ ]{0,3}(?:>|[-+*](?:[ \t]+|$)|\d{1,9}[.)](?:[ \t]+|$))",
            body,
        ):
            saw_container_marker = True
            container_followed_by_blank = False
        elif saw_container_marker and not body.strip(" \t"):
            container_followed_by_blank = True

        # 必须先在原始位置确认 ATX 前缀；不能删除注释后凭空拼出 ##。
        heading = re.match(r"^([ ]{0,3})(#{1,6})(?:[ \t]+|$)(.*)$", body)
        if heading:
            level = len(heading.group(2))
            if level <= 2 and heading.group(1) and saw_container_marker:
                raise RuntimeError("目标在列表或引用块之后包含归属含糊的缩进 H1/H2，拒绝猜测自动加载节边界")
            # CommonMark closing # 必须在原始行尾成立；不能先删 HTML 注释再制造 closing sequence。
            raw_title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(3)).rstrip(" \t")
            visible_title, inline_comment_open = _markdown_visible_text(raw_title, False)
            if inline_comment_open:
                raise RuntimeError("目标包含跨 Markdown 块的未闭合行内 HTML 注释，无法可靠判断自动加载节边界")
            title = visible_title.strip(" \t")
            if level <= 2:
                headings.append((offset, offset + len(line), level, "atx", title))
                if not heading.group(1):
                    saw_container_marker = False
                    container_followed_by_blank = False
            paragraph_start = None
            paragraph_parts = []
            paragraph_ambiguous_after_container = False
            link_reference_ambiguous = False
        else:
            visible, inline_comment_open = _markdown_visible_text(body, False)
            if inline_comment_open:
                raise RuntimeError("目标包含跨 Markdown 块的未闭合行内 HTML 注释，无法可靠判断自动加载节边界")
            possible_link_reference = re.match(r"^[ ]{0,3}\[[^\r\n]+\]:", visible)
            setext = re.match(r"^[ ]{0,3}(=+|-+)[ \t]*$", visible)
            if possible_link_reference:
                paragraph_start = None
                paragraph_parts = []
                paragraph_ambiguous_after_container = False
                link_reference_ambiguous = True
            elif setext and link_reference_ambiguous:
                raise RuntimeError("Markdown 链接引用定义与 Setext 标题边界含糊，拒绝猜测自动加载节范围")
            elif setext and paragraph_start is not None:
                if paragraph_ambiguous_after_container:
                    raise RuntimeError("目标在列表或引用块之后包含归属含糊的 Setext 标题，拒绝猜测自动加载节边界")
                level = 1 if setext.group(1).startswith("=") else 2
                title = " ".join(paragraph_parts).strip(" \t")
                headings.append((paragraph_start, offset + len(line), level, "setext", title))
                saw_container_marker = False
                paragraph_start = None
                paragraph_parts = []
                paragraph_ambiguous_after_container = False
                link_reference_ambiguous = False
            elif re.match(
                r"^[ ]{0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$",
                visible,
            ):
                paragraph_start = None
                paragraph_parts = []
                paragraph_ambiguous_after_container = False
                link_reference_ambiguous = False
            elif re.match(r"^[ ]{0,3}[^\x09-\x0d\x20]", visible) and not re.match(
                r"^[ ]{0,3}(?:>|[-+*](?:[ \t]+|$)|\d{1,9}[.)](?:[ \t]+|$))",
                visible,
            ):
                if paragraph_start is None:
                    if (
                        saw_container_marker
                        and container_followed_by_blank
                        and not visible.startswith(" ")
                    ):
                        saw_container_marker = False
                        container_followed_by_blank = False
                    paragraph_start = offset
                    paragraph_ambiguous_after_container = saw_container_marker
                paragraph_parts.append(visible.strip(" \t"))
            else:
                paragraph_start = None
                paragraph_parts = []
                paragraph_ambiguous_after_container = False
                link_reference_ambiguous = False
        offset += len(line)
    if fence_character:
        raise RuntimeError("目标包含未闭合的 Markdown fenced code block，无法可靠判断自动加载节边界")
    if in_html_comment:
        raise RuntimeError("目标包含未闭合的 HTML 注释，无法可靠判断自动加载节边界")
    return headings


def _global_rule_section_ranges(text: str) -> list[tuple[int, int]]:
    """定位 fenced code block 之外所有新旧 jojo-code-guard 自动加载节。"""
    managed_titles = {
        "jojo-code-guard 自动加载",
        "jojo-code-guard 自动加载（必须严格遵守）",
    }
    headings = _markdown_headings(text)
    ranges: list[tuple[int, int]] = []
    for index, (start, _end, level, syntax, title) in enumerate(headings):
        if level != 2 or syntax != "atx" or title not in managed_titles:
            continue
        next_start = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        ranges.append((start, next_start))
    return ranges


def _upsert_global_rule_section(
    target_data: bytes,
    source_data: bytes,
    *,
    create_title: bool,
) -> bytes:
    """只新增或更新自动加载节，并保留节外用户内容。"""
    target_bom = b"\xef\xbb\xbf" if target_data.startswith(b"\xef\xbb\xbf") else b""
    target_payload = target_data[len(target_bom):]
    try:
        target_text = target_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"自动加载节同步只支持严格 UTF-8 文本：{error}") from error

    line_ending = _global_rule_line_ending(target_text)
    section = _global_rule_section_text(source_data).replace("\n", line_ending)
    ranges = _global_rule_section_ranges(target_text)
    if not ranges:
        if create_title:
            updated = GLOBAL_RULE_CREATED_TITLE + line_ending * 2 + section + line_ending
        elif not target_text:
            updated = section + line_ending
        else:
            if target_text.endswith(line_ending * 2):
                separator = ""
            elif target_text.endswith(("\r", "\n")):
                separator = line_ending
            else:
                separator = line_ending * 2
            updated = target_text + separator + section + line_ending
        return target_bom + updated.encode("utf-8")

    # 所有匹配节都属于 jojo-code-guard；保留首节位置并移除后续重复节
    updated = target_text
    for start, end in reversed(ranges[1:]):
        updated = updated[:start] + updated[end:]

    first_start, first_end = ranges[0]
    suffix = updated[first_end:]
    if suffix:
        replacement = section + line_ending * 2
    else:
        replacement = section + (line_ending if target_text.endswith(("\r", "\n")) else "")
    updated = updated[:first_start] + replacement + suffix
    return target_bom + updated.encode("utf-8")


def _global_rule_file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """提取可检测替换、截断和元数据变化的文件身份。"""
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _global_rule_path_is_junction(path: Path) -> bool:
    """识别 junction 和其他 Windows reparse point。"""
    checker = getattr(path, "is_junction", None)
    if checker is not None and checker():
        return True
    if os.name != "nt":
        return False
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_point)


def _is_macos_system_path_alias(path: Path) -> bool:
    """仅允许 macOS 根目录固定的 BSD 兼容路径别名。"""
    return sys.platform == "darwin" and path.as_posix() in {"/var", "/tmp", "/etc"}


def _assert_global_rule_path_safe(target: Path) -> None:
    """拒绝目标或任一父路径经符号链接、junction 或普通文件重定向。"""
    absolute = target.absolute()
    for index, candidate in enumerate((absolute, *absolute.parents)):
        if candidate.is_symlink() and not _is_macos_system_path_alias(candidate):
            raise RuntimeError(f"路径包含符号链接，拒绝跟随写入：{candidate}")
        if _global_rule_path_is_junction(candidate):
            raise RuntimeError(f"路径包含 junction，拒绝跟随写入：{candidate}")
        if index > 0 and candidate.exists() and not candidate.is_dir():
            raise RuntimeError(f"目标父路径不是目录：{candidate}")


_GLOBAL_RULE_MAX_AUXILIARY_METADATA_BYTES = 16 * 1024 * 1024


def _windows_global_rule_dacl(path: Path) -> bytes:
    """读取 Windows self-relative DACL；无法读取时失败关闭。"""
    security_information = 0x00000004  # DACL_SECURITY_INFORMATION
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_file_security = advapi32.GetFileSecurityW
    get_file_security.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    )
    get_file_security.restype = ctypes.c_int
    needed = ctypes.c_uint32()
    get_file_security(str(path), security_information, None, 0, ctypes.byref(needed))
    if not needed.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(needed.value)
    if not get_file_security(
        str(path),
        security_information,
        descriptor,
        needed.value,
        ctypes.byref(needed),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    data = bytearray(descriptor.raw[: needed.value])
    if len(data) < 4:
        raise RuntimeError(f"Windows DACL 安全描述符过短：{path}")
    # SetFileSecurityW 会重新计算 SE_DACL_AUTO_INHERITED；该位不改变 ACL 内容或保护边界。
    control = int.from_bytes(data[2:4], "little") & ~0x0400
    data[2:4] = control.to_bytes(2, "little")
    return bytes(data)


def _set_windows_global_rule_dacl(path: Path, descriptor_data: bytes) -> None:
    """把预检时的 Windows DACL 应用到同目录暂存文件。"""
    security_information = 0x00000004  # DACL_SECURITY_INFORMATION
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    set_file_security = advapi32.SetFileSecurityW
    set_file_security.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p)
    set_file_security.restype = ctypes.c_int
    descriptor = ctypes.create_string_buffer(descriptor_data)
    if not set_file_security(str(path), security_information, descriptor):
        raise ctypes.WinError(ctypes.get_last_error())


def _reapply_windows_global_rule_dacl_pinned(
    path: Path,
    descriptor_data: bytes,
    expected_object: _GlobalRuleSnapshot,
) -> None:
    """用拒绝写入/删除共享的句柄钉住已发布对象，再修正 ReplaceFileW 的继承标记。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    read_control = 0x00020000
    write_dac = 0x00040000
    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    open_existing = 3
    handle = create_file(
        str(path),
        read_control | write_dac | file_read_attributes,
        file_share_read,
        None,
        open_existing,
        0,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        pinned = _global_rule_snapshot(path)
        if not _global_rule_snapshots_equivalent(
            pinned,
            expected_object,
            require_same_object=True,
        ):
            raise RuntimeError(f"DACL 修正前目标身份或元数据已变化，拒绝修改外部版本：{path}")
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        set_kernel_security = advapi32.SetKernelObjectSecurity
        set_kernel_security.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)
        set_kernel_security.restype = ctypes.c_int
        descriptor = ctypes.create_string_buffer(descriptor_data)
        if not set_kernel_security(handle, 0x00000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
        corrected = _global_rule_snapshot(path)
        metadata = expected_object.metadata
        if metadata is None:
            raise RuntimeError(f"DACL 修正缺少预期元数据：{path}")
        corrected_metadata = _GlobalRuleMetadata(
            windows_attributes=metadata.windows_attributes,
            windows_dacl=descriptor_data,
            alternate_streams=metadata.alternate_streams,
            xattrs=metadata.xattrs,
            uid=metadata.uid,
            gid=metadata.gid,
            file_flags=metadata.file_flags,
            darwin_acl=metadata.darwin_acl,
        )
        expected_corrected = _GlobalRuleSnapshot(
            exists=expected_object.exists,
            data=expected_object.data,
            identity=expected_object.identity,
            mode=expected_object.mode,
            metadata=corrected_metadata,
        )
        if not _global_rule_snapshots_equivalent(
            corrected,
            expected_corrected,
            require_same_object=True,
        ):
            raise RuntimeError(f"DACL 修正期间目标身份或元数据发生变化：{path}")
    finally:
        close_handle(handle)


def _windows_global_rule_streams(path: Path) -> tuple[tuple[str, bytes], ...]:
    """枚举并读取 NTFS named streams，避免原子替换静默丢失 ADS。"""

    class _Win32FindStreamData(ctypes.Structure):
        _fields_ = (("size", ctypes.c_longlong), ("name", ctypes.c_wchar * 296))

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = (ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
    find_first.restype = ctypes.c_void_p
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    find_next.restype = ctypes.c_int
    find_close = kernel32.FindClose
    find_close.argtypes = (ctypes.c_void_p,)
    find_close.restype = ctypes.c_int

    entry = _Win32FindStreamData()
    handle = find_first(str(path), 0, ctypes.byref(entry), 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error in (2, 38):  # ERROR_FILE_NOT_FOUND / ERROR_HANDLE_EOF
            return ()
        raise ctypes.WinError(error)

    streams: list[tuple[str, bytes]] = []
    total = 0
    try:
        while True:
            stream_name = entry.name
            if stream_name != "::$DATA":
                if not stream_name.endswith(":$DATA"):
                    raise RuntimeError(f"无法识别 alternate data stream：{stream_name}")
                logical_name = stream_name.removesuffix(":$DATA")
                if entry.size < 0 or entry.size > _GLOBAL_RULE_MAX_AUXILIARY_METADATA_BYTES:
                    raise RuntimeError(f"alternate data stream 过大，拒绝自动复制：{logical_name}")
                value = Path(str(path) + logical_name).read_bytes()
                total += len(value)
                if total > _GLOBAL_RULE_MAX_AUXILIARY_METADATA_BYTES:
                    raise RuntimeError("alternate data stream 总量过大，拒绝自动复制")
                streams.append((logical_name, value))
            if find_next(handle, ctypes.byref(entry)):
                continue
            error = ctypes.get_last_error()
            if error != 38:  # ERROR_HANDLE_EOF
                raise ctypes.WinError(error)
            break
    finally:
        find_close(handle)
    return tuple(sorted(streams))


def _darwin_global_rule_acl(path: Path) -> bytes:
    """读取 macOS 扩展 ACL 的文本表示，供替换前后精确比较。"""
    acl_type_extended = 0x00000100
    libc = ctypes.CDLL(None, use_errno=True)
    acl_get_file = libc.acl_get_file
    acl_get_file.argtypes = (ctypes.c_char_p, ctypes.c_int)
    acl_get_file.restype = ctypes.c_void_p
    acl_to_text = libc.acl_to_text
    acl_to_text.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t))
    acl_to_text.restype = ctypes.c_void_p
    acl_free = libc.acl_free
    acl_free.argtypes = (ctypes.c_void_p,)
    acl_free.restype = ctypes.c_int

    acl = acl_get_file(os.fsencode(path), acl_type_extended)
    if not acl:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))
    text_pointer: int | None = None
    try:
        length = ctypes.c_ssize_t()
        text_pointer = acl_to_text(acl, ctypes.byref(length))
        if not text_pointer:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(path))
        return ctypes.string_at(text_pointer, length.value)
    finally:
        if text_pointer:
            acl_free(text_pointer)
        acl_free(acl)


def _darwin_global_rule_acl_or_empty(path: Path) -> bytes:
    """macOS 将不存在的扩展 ACL 规范化为空，而非目标文件缺失。"""
    try:
        return _darwin_global_rule_acl(path)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return b""
        raise


def _set_darwin_global_rule_acl(path: Path, acl_text: bytes) -> None:
    """从快照文本恢复 macOS 扩展 ACL。"""
    acl_type_extended = 0x00000100
    libc = ctypes.CDLL(None, use_errno=True)
    acl_init = libc.acl_init
    acl_init.argtypes = (ctypes.c_int,)
    acl_init.restype = ctypes.c_void_p
    acl_from_text = libc.acl_from_text
    acl_from_text.argtypes = (ctypes.c_char_p,)
    acl_from_text.restype = ctypes.c_void_p
    acl_set_file = libc.acl_set_file
    acl_set_file.argtypes = (ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
    acl_set_file.restype = ctypes.c_int
    acl_free = libc.acl_free
    acl_free.argtypes = (ctypes.c_void_p,)
    acl_free.restype = ctypes.c_int

    # acl_from_text 不接受空文本；acl_init(0) 才是 macOS 的合法空 ACL。
    acl = acl_init(0) if not acl_text else acl_from_text(acl_text)
    if not acl:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))
    try:
        if acl_set_file(os.fsencode(path), acl_type_extended, acl) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(path))
    finally:
        acl_free(acl)


def _global_rule_metadata(target: Path, info: os.stat_result) -> _GlobalRuleMetadata:
    """读取替换操作必须保真的 ACL、ADS、属性或 xattr。"""
    if os.name == "nt":
        attributes = getattr(info, "st_file_attributes", 0)
        unsupported = attributes & (0x0200 | 0x0800 | 0x4000)
        if unsupported:
            raise RuntimeError("目标使用 sparse/compressed/encrypted 属性，无法安全保真，拒绝写入")
        return _GlobalRuleMetadata(
            attributes,
            _windows_global_rule_dacl(target),
            _windows_global_rule_streams(target),
            (),
            None,
            None,
            None,
            None,
        )

    xattrs: list[tuple[str, bytes]] = []
    total = 0
    if hasattr(os, "listxattr"):
        for name in sorted(os.listxattr(target, follow_symlinks=False)):
            value = os.getxattr(target, name, follow_symlinks=False)
            total += len(value)
            if total > _GLOBAL_RULE_MAX_AUXILIARY_METADATA_BYTES:
                raise RuntimeError("扩展属性总量过大，拒绝自动复制")
            xattrs.append((name, value))
    file_flags = getattr(info, "st_flags", None)
    immutable_flags = sum(
        getattr(stat, name, 0)
        for name in ("UF_APPEND", "UF_IMMUTABLE", "SF_APPEND", "SF_IMMUTABLE")
    )
    if file_flags is not None and file_flags & immutable_flags:
        raise RuntimeError("目标使用 append/immutable 文件标志，拒绝自动替换")
    darwin_acl = _darwin_global_rule_acl_or_empty(target) if sys.platform == "darwin" else None
    return _GlobalRuleMetadata(
        None,
        None,
        (),
        tuple(xattrs),
        info.st_uid,
        info.st_gid,
        file_flags,
        darwin_acl,
    )


def _apply_global_rule_metadata(
    path: Path,
    metadata: _GlobalRuleMetadata,
    *,
    mode: int | None = None,
) -> None:
    """把预检元数据复制到暂存文件；任一项失败都不得继续替换。"""
    if os.name == "nt":
        if mode is not None:
            os.chmod(path, mode)
        if metadata.windows_dacl is not None:
            _set_windows_global_rule_dacl(path, metadata.windows_dacl)
        for stream_name, value in metadata.alternate_streams:
            Path(str(path) + stream_name).write_bytes(value)
        if metadata.windows_attributes is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            set_attributes = kernel32.SetFileAttributesW
            set_attributes.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32)
            set_attributes.restype = ctypes.c_int
            if not set_attributes(str(path), metadata.windows_attributes):
                raise ctypes.WinError(ctypes.get_last_error())
        return

    if metadata.uid is not None and metadata.gid is not None and hasattr(os, "chown"):
        current = os.lstat(path)
        if current.st_uid != metadata.uid or current.st_gid != metadata.gid:
            os.chown(path, metadata.uid, metadata.gid, follow_symlinks=False)
    if mode is not None:
        os.chmod(path, mode, follow_symlinks=False)
    if hasattr(os, "setxattr"):
        for name, value in metadata.xattrs:
            os.setxattr(path, name, value, follow_symlinks=False)
    if metadata.darwin_acl is not None:
        _set_darwin_global_rule_acl(path, metadata.darwin_acl)
    if metadata.file_flags is not None and hasattr(os, "chflags"):
        os.chflags(path, metadata.file_flags, follow_symlinks=False)


def _global_rule_snapshot(target: Path) -> _GlobalRuleSnapshot:
    """安全读取目标，并拒绝非普通文件、硬链接和读取期间的变化。"""
    _assert_global_rule_path_safe(target)
    if not target.exists():
        return _GlobalRuleSnapshot(False, b"", None, None, None)
    try:
        before = os.lstat(target)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"目标不是普通文件：{target}")
        if before.st_nlink > 1:
            raise RuntimeError(f"目标是硬链接，拒绝连带修改其他路径：{target}")
        metadata_before = _global_rule_metadata(target, before)
        data = target.read_bytes()
        after = os.lstat(target)
        metadata_after = _global_rule_metadata(target, after)
    except FileNotFoundError as error:
        raise RuntimeError(f"读取期间目标发生变化：{target}") from error
    if _global_rule_file_identity(before) != _global_rule_file_identity(after):
        raise RuntimeError(f"读取期间目标发生变化：{target}")
    if metadata_before != metadata_after:
        raise RuntimeError(f"读取期间目标元数据发生变化：{target}")
    return _GlobalRuleSnapshot(
        True,
        data,
        _global_rule_file_identity(after),
        stat.S_IMODE(after.st_mode),
        metadata_after,
    )


def _assert_global_rule_snapshot(target: Path, expected: _GlobalRuleSnapshot) -> None:
    """落盘前确认目标仍与预检快照完全一致。"""
    if _global_rule_snapshot(target) != expected:
        raise RuntimeError(f"目标在预检后发生变化，拒绝覆盖并发修改：{target}")


def _ensure_global_rule_parent(parent: Path, created: list[Path]) -> None:
    """逐级创建缺失父目录，并把本次实际创建项立即登记到事务账本。"""
    absolute = parent.absolute()
    missing: list[Path] = []
    cursor = absolute
    while not cursor.exists():
        _assert_global_rule_path_safe(cursor)
        missing.append(cursor)
        if cursor.parent == cursor:
            raise RuntimeError(f"找不到可用的目标父目录：{parent}")
        cursor = cursor.parent
    _assert_global_rule_path_safe(cursor)
    if not cursor.is_dir():
        raise RuntimeError(f"目标父路径不是目录：{cursor}")

    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            _assert_global_rule_path_safe(directory)
            if not directory.is_dir():
                raise RuntimeError(f"并发创建的父路径不是目录：{directory}")
        else:
            created.append(directory)


def _global_rule_snapshots_equivalent(
    actual: _GlobalRuleSnapshot,
    expected: _GlobalRuleSnapshot,
    *,
    require_same_object: bool = False,
) -> bool:
    """比较内容与保真元数据；置换备份可忽略改名导致的 ctime 变化。"""
    if (
        actual.exists != expected.exists
        or actual.data != expected.data
        or actual.mode != expected.mode
        or actual.metadata != expected.metadata
    ):
        return False
    if not require_same_object or not actual.exists:
        return True
    if actual.identity is None or expected.identity is None:
        return False
    return actual.identity[:4] == expected.identity[:4]


def _replace_global_rule_file_with_backup(target: Path, temporary: Path, backup: Path) -> Path:
    """原子置换现有文件并保留实际被置换对象；不支持交换原语时失败关闭。"""
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        replace_file = kernel32.ReplaceFileW
        replace_file.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        replace_file.restype = ctypes.c_int
        if not replace_file(str(target), str(temporary), str(backup), 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        return backup

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise RuntimeError("当前 libc 不提供 renameat2，拒绝使用有 TOCTOU 窗口的替换")
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(temporary), -100, os.fsencode(target), 0x2) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise RuntimeError("当前系统不提供 renamex_np，拒绝使用有 TOCTOU 窗口的替换")
        renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex_np.restype = ctypes.c_int
        if renamex_np(os.fsencode(temporary), os.fsencode(target), 0x2) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    else:
        raise RuntimeError("当前平台没有受支持的原子文件交换原语，拒绝写入")

    # POSIX exchange 后 temporary 已经是实际被置换对象；直接把该名字作为 recovery_path，
    # 避免 swap 成功后再登记备份时出现可删除用户版本的第二个失败窗口。
    return temporary


def _install_new_global_rule_file(target: Path, temporary: Path) -> None:
    """以 no-clobber 方式发布新文件，避免覆盖预检后并发创建的目标。"""
    try:
        _move_global_rule_file_no_replace(temporary, target)
    except FileExistsError as error:
        raise RuntimeError(f"目标在预检后被并发创建，拒绝覆盖：{target}") from error


def _move_global_rule_file_no_replace(source: Path, destination: Path) -> None:
    """原子移动且不覆盖目的路径，用于安全撤销本轮新建文件。"""
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move_file = kernel32.MoveFileW
        move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        move_file.restype = ctypes.c_int
        if not move_file(str(source), str(destination)):
            raise ctypes.WinError(ctypes.get_last_error())
        return

    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise RuntimeError("当前 libc 不提供 renameat2，拒绝不安全地撤销新文件")
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 0x1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(source))
        return

    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise RuntimeError("当前系统不提供 renamex_np，拒绝不安全地撤销新文件")
        renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex_np.restype = ctypes.c_int
        if renamex_np(os.fsencode(source), os.fsencode(destination), 0x4) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(source))
        return

    raise RuntimeError("当前平台没有受支持的 no-clobber 移动原语，拒绝撤销新文件")


def _probe_global_rule_no_replace(parent: Path) -> None:
    """在目标卷真实探测 no-clobber rename，确保首次发布和回滚使用同一能力。"""
    descriptor, source_name = tempfile.mkstemp(prefix=".jojo-probe-source-", dir=str(parent))
    os.close(descriptor)
    source = Path(source_name)
    destination = _reserve_unused_global_rule_path(source, "probe-destination")
    try:
        _move_global_rule_file_no_replace(source, destination)
        if source.exists() or not destination.is_file():
            raise RuntimeError(f"no-clobber rename 探测结果异常：{parent}")
    finally:
        for path in (source, destination):
            try:
                path.unlink()
            except FileNotFoundError:
                continue


def _reserve_unused_global_rule_path(target: Path, label: str) -> Path:
    """在目标目录取得一个本轮独占、当前不存在的恢复路径。"""
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.jojo-{label}-",
        dir=str(target.parent),
    )
    os.close(descriptor)
    path = Path(name)
    path.unlink()
    return path


def _global_rule_same_object_and_data(
    actual: _GlobalRuleSnapshot,
    expected: _GlobalRuleSnapshot,
) -> bool:
    """只在文件身份和主数据都一致时认定为同一个已发布对象。"""
    return bool(
        actual.exists
        and expected.exists
        and actual.identity is not None
        and expected.identity is not None
        and actual.identity[:2] == expected.identity[:2]
        and actual.data == expected.data
    )


def _global_rule_same_object_except_windows_dacl(
    actual: _GlobalRuleSnapshot,
    expected: _GlobalRuleSnapshot,
) -> bool:
    """比较发布对象，并只容许 ReplaceFileW 可预测地改写 DACL 继承标志。"""
    if not _global_rule_same_object_and_data(actual, expected) or actual.mode != expected.mode:
        return False
    if actual.metadata is None or expected.metadata is None:
        return actual.metadata == expected.metadata
    return bool(
        actual.metadata.windows_attributes == expected.metadata.windows_attributes
        and actual.metadata.alternate_streams == expected.metadata.alternate_streams
        and actual.metadata.xattrs == expected.metadata.xattrs
        and actual.metadata.uid == expected.metadata.uid
        and actual.metadata.gid == expected.metadata.gid
        and actual.metadata.file_flags == expected.metadata.file_flags
        and actual.metadata.darwin_acl == expected.metadata.darwin_acl
        and _normalize_windows_global_rule_dacl_for_replace(
            actual.metadata.windows_dacl
        )
        == _normalize_windows_global_rule_dacl_for_replace(
            expected.metadata.windows_dacl
        )
    )


def _normalize_windows_global_rule_dacl_for_replace(
    descriptor_data: bytes | None,
) -> tuple[int, bytes | None] | None:
    """提取规则文件 DACL 语义，忽略 ReplaceFileW 的后代继承传播位。"""
    if descriptor_data is None:
        return None
    if len(descriptor_data) < 20:
        raise RuntimeError("Windows DACL 安全描述符过短")
    # SE_SELF_RELATIVE、SE_DACL_DEFAULTED、SE_DACL_AUTO_INHERIT_REQ 和
    # SE_DACL_AUTO_INHERITED 只描述序列化/继承过程；SE_DACL_PROTECTED 保持可见。
    control = int.from_bytes(descriptor_data[2:4], "little") & ~0x8508
    dacl_offset = int.from_bytes(descriptor_data[16:20], "little")
    if dacl_offset == 0:
        return control, None
    if dacl_offset + 8 > len(descriptor_data):
        raise RuntimeError("Windows DACL 偏移越出安全描述符")
    acl_size = int.from_bytes(descriptor_data[dacl_offset + 2:dacl_offset + 4], "little")
    ace_count = int.from_bytes(descriptor_data[dacl_offset + 4:dacl_offset + 6], "little")
    acl_end = dacl_offset + acl_size
    if acl_size < 8 or acl_end > len(descriptor_data):
        raise RuntimeError("Windows ACL 长度无效")
    normalized_acl = bytearray(descriptor_data[dacl_offset:acl_end])
    cursor = 8
    aces: list[bytes] = []
    for _ in range(ace_count):
        if cursor + 4 > len(normalized_acl):
            raise RuntimeError("Windows ACE 头越出 ACL")
        ace_size = int.from_bytes(normalized_acl[cursor + 2:cursor + 4], "little")
        if ace_size < 4 or cursor + ace_size > len(normalized_acl):
            raise RuntimeError("Windows ACE 长度无效")
        # 规则目标始终是普通文件：这四个标志只控制其假想后代的继承，
        # ReplaceFileW 可按新父目录重新计算它们。INHERIT_ONLY_ACE (0x08)
        # 会改变当前文件的有效访问权，故必须保留；ACE 类型、顺序与掩码也保留。
        normalized_acl[cursor + 1] &= ~0x17
        aces.append(bytes(normalized_acl[cursor:cursor + ace_size]))
        cursor += ace_size
    if cursor > len(normalized_acl):
        raise RuntimeError("Windows ACE 列表越出 ACL")
    # GitHub Windows 的 ReplaceFileW 会把整组继承 ACE 精确重复。DACL 不包含
    # 审计 ACE，完整序列的逐字节重复不改变 allow/deny 的有效授权；仅折叠这种
    # 周期性完整重复，不合并不同 ACE、不重排、也不放宽访问掩码。
    for period in range(1, len(aces)):
        if len(aces) % period or any(
            ace != aces[index % period] for index, ace in enumerate(aces)
        ):
            continue
        reduced_aces = aces[:period]
        reduced = bytearray(normalized_acl[:8])
        reduced_size = 8 + sum(len(ace) for ace in reduced_aces)
        reduced[2:4] = reduced_size.to_bytes(2, "little")
        reduced[4:6] = period.to_bytes(2, "little")
        return control, bytes(reduced + b"".join(reduced_aces))
    return control, bytes(normalized_acl)


def _require_secure_private_directory_support() -> None:
    """旧版 Windows Python 的 0700 目录不私有；隔离删除必须在安全补丁级上运行。"""
    if os.name != "nt":
        return
    required_micro = {
        (3, 9): 20,
        (3, 10): 15,
        (3, 11): 10,
        (3, 12): 4,
    }
    branch = (sys.version_info[0], sys.version_info[1])
    if branch < (3, 9):
        raise RuntimeError("Windows 私有隔离目录要求 Python 3.9.20 或更高安全版本")
    minimum = required_micro.get(branch)
    if minimum is not None and sys.version_info[2] < minimum:
        required = f"{branch[0]}.{branch[1]}.{minimum}"
        raise RuntimeError(
            f"当前 Windows Python 无法保证 0700 私有目录 DACL；请升级到 {required} 或更高补丁版本"
        )


def _unlink_verified_global_rule_recovery(
    path: Path,
    expected: _GlobalRuleSnapshot,
) -> None:
    """把预期对象移入本轮私有目录后删除；身份不明时原位恢复并报告。"""
    actual = _global_rule_snapshot(path)
    if not actual.exists:
        return
    if not _global_rule_snapshots_equivalent(actual, expected, require_same_object=True):
        raise RuntimeError(f"恢复路径已被替换，拒绝删除：{path}")
    _require_secure_private_directory_support()
    quarantine_root = Path(
        tempfile.mkdtemp(
            prefix=f".{path.name}.jojo-discard-",
            dir=str(path.parent),
        )
    )
    quarantine = quarantine_root / "owned"
    moved = False
    delete_expected = expected
    try:
        _move_global_rule_file_no_replace(path, quarantine)
        moved = True
        captured = _global_rule_snapshot(quarantine)
        if not _global_rule_snapshots_equivalent(
            captured,
            expected,
            require_same_object=True,
        ):
            if not path.exists():
                _move_global_rule_file_no_replace(quarantine, path)
                moved = False
            raise RuntimeError(f"删除窗口捕获到外部对象，已拒绝删除：{path}")
        if os.name == "nt" and captured.metadata is not None:
            attributes = captured.metadata.windows_attributes or 0
            if attributes & 0x1:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                set_attributes = kernel32.SetFileAttributesW
                set_attributes.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32)
                set_attributes.restype = ctypes.c_int
                if not set_attributes(str(quarantine), attributes & ~0x1):
                    raise ctypes.WinError(ctypes.get_last_error())
                after_clear = _global_rule_snapshot(quarantine)
                after_metadata = after_clear.metadata
                if (
                    not _global_rule_same_object_and_data(after_clear, captured)
                    or after_metadata is None
                    or after_metadata.windows_attributes != attributes & ~0x1
                    or after_metadata.windows_dacl != captured.metadata.windows_dacl
                    or after_metadata.alternate_streams != captured.metadata.alternate_streams
                    or after_metadata.xattrs != captured.metadata.xattrs
                    or after_metadata.uid != captured.metadata.uid
                    or after_metadata.gid != captured.metadata.gid
                    or after_metadata.file_flags != captured.metadata.file_flags
                    or after_metadata.darwin_acl != captured.metadata.darwin_acl
                ):
                    raise RuntimeError(f"清除只读属性时隔离文件发生其他变化：{quarantine}")
                delete_expected = after_clear
        # 随机 0700 私有目录隔离了原路径的并发保存；删除前再核对一次捕获对象。
        before_delete = _global_rule_snapshot(quarantine)
        if not _global_rule_snapshots_equivalent(
            before_delete,
            delete_expected,
            require_same_object=True,
        ):
            raise RuntimeError(f"隔离文件在清理前发生变化，已保留：{quarantine}")
        os.unlink(quarantine)
        moved = False
    except BaseException:
        if moved:
            try:
                if path.exists() or path.is_symlink():
                    raise RuntimeError(f"原恢复路径已被占用：{path}")
                if expected.metadata is not None:
                    _apply_global_rule_metadata(
                        quarantine,
                        expected.metadata,
                        mode=expected.mode,
                    )
                elif expected.mode is not None:
                    os.chmod(quarantine, expected.mode)
                _move_global_rule_file_no_replace(quarantine, path)
                moved = False
            except BaseException as restore_error:
                raise RuntimeError(
                    f"隔离清理失败且无法安全放回原路径；对象保留在：{quarantine}"
                ) from restore_error
        raise
    finally:
        if not moved:
            try:
                quarantine_root.rmdir()
            except FileNotFoundError:
                pass


def _restore_global_rule_backup_atomically(
    target: Path,
    replacement_path: Path,
    replacement_snapshot: _GlobalRuleSnapshot,
    expected_current: _GlobalRuleSnapshot,
    *,
    preserve_displaced: bool = False,
) -> _GlobalRuleSnapshot:
    """原子放回用户版本，同时捕获恢复窗口内再次出现的外部保存。"""
    _assert_global_rule_snapshot(target, expected_current)
    quarantine = _reserve_unused_global_rule_path(target, "recovery")
    displaced_path: Path | None = None
    try:
        returned = _replace_global_rule_file_with_backup(
            target,
            replacement_path,
            quarantine,
        )
        displaced_path = returned or (quarantine if os.name == "nt" else replacement_path)
    except BaseException as error:
        # ReplaceFileW 1177 会把当前 target 放到 quarantine、保留 replacement，且 target 消失。
        # 仅在这三个路径与该文档化状态完全吻合时，用 no-clobber move 补完恢复。
        target_after_error = _global_rule_snapshot(target)
        replacement_after_error = _global_rule_snapshot(replacement_path)
        quarantine_after_error = _global_rule_snapshot(quarantine)
        if (
            not target_after_error.exists
            and replacement_after_error.exists
            and quarantine_after_error.exists
        ):
            _move_global_rule_file_no_replace(replacement_path, target)
            displaced_path = quarantine
        else:
            locations = ", ".join(
                str(path)
                for path, snapshot in (
                    (target, target_after_error),
                    (replacement_path, replacement_after_error),
                    (quarantine, quarantine_after_error),
                )
                if snapshot.exists
            )
            raise RuntimeError(
                "恢复原文件的原子交换失败；未删除任何不明对象；保留位置：" + locations
            ) from error

    if replacement_snapshot.metadata is not None:
        _apply_global_rule_metadata(
            target,
            replacement_snapshot.metadata,
            mode=replacement_snapshot.mode,
        )
    elif replacement_snapshot.mode is not None:
        os.chmod(target, replacement_snapshot.mode)
    restored = _global_rule_snapshot(target)
    if not _global_rule_snapshots_equivalent(
        restored,
        replacement_snapshot,
        require_same_object=True,
    ):
        raise RuntimeError(
            f"用户版本已放回但元数据复核失败；被置换版本保留在：{displaced_path}"
        )
    if displaced_path is None:
        raise RuntimeError("恢复原文件后没有取得被置换对象的恢复路径")
    displaced = _global_rule_snapshot(displaced_path)
    if not _global_rule_snapshots_equivalent(
        displaced,
        expected_current,
        require_same_object=True,
    ):
        raise RuntimeError(f"恢复窗口出现再次编辑；实际外部版本已隔离在：{displaced_path}")
    if preserve_displaced:
        raise RuntimeError(f"写入后复核失败；被置换版本已隔离在：{displaced_path}")
    _unlink_verified_global_rule_recovery(displaced_path, displaced)
    return restored


def _recover_failed_global_rule_replace(
    target: Path,
    owned_temporary: Path,
    requested_backup: Path,
    staged: _GlobalRuleSnapshot,
    expected: _GlobalRuleSnapshot,
    published_baseline: _GlobalRuleSnapshot | None = None,
) -> Path | None:
    """依据三个实际路径恢复失败/中断后的状态，并返回可安全清理的自有暂存。"""
    current = _global_rule_snapshot(target)
    candidates: list[tuple[Path, _GlobalRuleSnapshot]] = []
    for path in (requested_backup, owned_temporary):
        if any(path == candidate for candidate, _ in candidates):
            continue
        snapshot = _global_rule_snapshot(path)
        if snapshot.exists:
            candidates.append((path, snapshot))

    displaced_candidates = [
        (path, snapshot)
        for path, snapshot in candidates
        if not _global_rule_snapshots_equivalent(
            snapshot,
            staged,
            require_same_object=True,
        )
    ]
    if len(displaced_candidates) > 1:
        locations = ", ".join(str(path) for path, _ in displaced_candidates)
        raise RuntimeError(f"替换失败后出现多个不明用户版本，均已保留：{locations}")
    if displaced_candidates:
        displaced_path, displaced = displaced_candidates[0]
        if not current.exists:
            _move_global_rule_file_no_replace(displaced_path, target)
            if displaced.metadata is not None:
                _apply_global_rule_metadata(target, displaced.metadata, mode=displaced.mode)
            restored = _global_rule_snapshot(target)
            if not _global_rule_snapshots_equivalent(
                restored,
                displaced,
                require_same_object=True,
            ):
                raise RuntimeError(f"部分替换失败后用户版本恢复复核失败：{target}")
        elif (
            _global_rule_snapshots_equivalent(
                current,
                staged,
                require_same_object=True,
            )
            or (
                published_baseline is not None
                and _global_rule_snapshots_equivalent(
                    current,
                    published_baseline,
                    require_same_object=True,
                )
            )
            or (
                published_baseline is None
                and _global_rule_same_object_except_windows_dacl(current, staged)
            )
        ):
            _restore_global_rule_backup_atomically(
                target,
                displaced_path,
                displaced,
                current,
            )
        elif _global_rule_same_object_and_data(current, staged):
            try:
                _restore_global_rule_backup_atomically(
                    target,
                    displaced_path,
                    displaced,
                    current,
                    preserve_displaced=True,
                )
            except RuntimeError as error:
                raise RuntimeError(
                    f"暂存对象元数据发生并发变化；原版本已恢复，变化版本已保留：{error}"
                ) from error
        else:
            raise RuntimeError(
                f"替换失败后目标又发生变化；目标保留在 {target}，先前版本保留在 {displaced_path}"
            )

    current_after = _global_rule_snapshot(target)
    owned_after = _global_rule_snapshot(owned_temporary)
    if (
        owned_after.exists
        and _global_rule_snapshots_equivalent(
            owned_after,
            staged,
            require_same_object=True,
        )
        and (
            current_after == expected
            or not _global_rule_snapshots_equivalent(
                current_after,
                staged,
                require_same_object=True,
            )
        )
    ):
        return owned_temporary
    if owned_after.exists:
        raise RuntimeError(f"替换失败后暂存路径不再属于本轮，已保留：{owned_temporary}")
    return None


def _write_global_rule_file(
    target: Path,
    data: bytes,
    expected: _GlobalRuleSnapshot,
    *,
    mode: int | None = None,
) -> _GlobalRuleSnapshot:
    """同目录暂存后以可恢复置换提交，并验证实际被置换版本与文件元数据。"""
    _assert_global_rule_snapshot(target, expected)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.jojo-sync-",
        dir=str(target.parent),
    )
    temporary: Path | None = Path(temporary_name)
    owned_temporary_identity = _global_rule_file_identity(os.fstat(descriptor))[:2]
    staged: _GlobalRuleSnapshot | None = None
    actual: _GlobalRuleSnapshot | None = None
    backup: Path | None = None
    published_baseline: _GlobalRuleSnapshot | None = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        desired_mode = mode if mode is not None else expected.mode
        if expected.metadata is not None:
            _apply_global_rule_metadata(
                temporary,
                expected.metadata,
                mode=desired_mode,
            )
        elif desired_mode is not None:
            os.chmod(temporary, desired_mode)
        staged = _global_rule_snapshot(temporary)
        staged_metadata_matches = expected.metadata is None or staged.metadata == expected.metadata
        staged_mode_matches = desired_mode is None or staged.mode == desired_mode
        if (
            not staged.exists
            or staged.data != data
            or not staged_metadata_matches
            or not staged_mode_matches
        ):
            raise RuntimeError(f"暂存文件字节、权限或附加元数据写后复核失败：{target}")
        _assert_global_rule_snapshot(target, expected)

        if expected.exists:
            requested_backup = _reserve_unused_global_rule_path(target, "backup")
            backup = requested_backup
            owned_temporary = temporary
            # 一旦把路径交给 exchange，它就可能承载用户的实际被置换版本；finally 不再裸删该名字。
            temporary = None
            try:
                returned_backup = _replace_global_rule_file_with_backup(
                    target,
                    owned_temporary,
                    requested_backup,
                )
            except BaseException:
                temporary = _recover_failed_global_rule_replace(
                    target,
                    owned_temporary,
                    requested_backup,
                    staged,
                    expected,
                )
                raise
            backup = returned_backup or (
                requested_backup if os.name == "nt" else owned_temporary
            )
            try:
                displaced = _global_rule_snapshot(backup)
                if not _global_rule_snapshots_equivalent(
                    displaced,
                    expected,
                    require_same_object=True,
                ):
                    current_before_restore = _global_rule_snapshot(target)
                    if not _global_rule_snapshots_equivalent(
                        current_before_restore,
                        staged,
                        require_same_object=True,
                    ):
                        raise RuntimeError(
                            f"提交后目标再次发生变化；当前版本保留在 {target}，"
                            f"替换窗口版本保留在 {backup}"
                        )
                    _restore_global_rule_backup_atomically(
                        target,
                        backup,
                        displaced,
                        current_before_restore,
                    )
                    backup = None
                    raise RuntimeError(f"捕获到最后替换窗口的并发修改，已恢复外部内容：{target}")
                current_object = _global_rule_snapshot(target)
                if not _global_rule_same_object_except_windows_dacl(
                    current_object,
                    staged,
                ):
                    raise RuntimeError(f"提交后目标再次被并发修改；原版本保留在：{backup}")
                published_baseline = current_object
                if (
                    os.name == "nt"
                    and expected.metadata is not None
                    and expected.metadata.windows_dacl is not None
                ):
                    _reapply_windows_global_rule_dacl_pinned(
                        target,
                        expected.metadata.windows_dacl,
                        current_object,
                    )
                actual = _global_rule_snapshot(target)
            except BaseException:
                try:
                    temporary = _recover_failed_global_rule_replace(
                        target,
                        owned_temporary,
                        requested_backup,
                        staged,
                        expected,
                        published_baseline,
                    )
                    backup = None
                except BaseException as recovery_error:
                    raise RuntimeError(
                        f"提交后复核失败且无法安全恢复：{recovery_error}；"
                        f"原版本可能仍位于：{backup}"
                    ) from recovery_error
                raise
        else:
            _install_new_global_rule_file(target, temporary)
            temporary = None

        if actual is None:
            actual = _global_rule_snapshot(target)
        metadata_matches = expected.metadata is None or actual.metadata == expected.metadata
        mode_matches = desired_mode is None or actual.mode == desired_mode
        staged_object_matches = _global_rule_snapshots_equivalent(
            actual,
            staged,
            require_same_object=True,
        )
        if (
            not actual.exists
            or actual.data != data
            or not metadata_matches
            or not mode_matches
            or not staged_object_matches
        ):
            if (
                backup is not None
                and _global_rule_snapshot(backup).exists
                and staged_object_matches
            ):
                original_backup = _global_rule_snapshot(backup)
                _restore_global_rule_backup_atomically(
                    target,
                    backup,
                    original_backup,
                    actual,
                    preserve_displaced=True,
                )
                backup = None
            raise RuntimeError(f"写入后文件身份、字节、权限或附加元数据复核失败：{target}")
        recovery_path = backup
        backup = None
        if recovery_path is not None:
            return _GlobalRuleSnapshot(
                actual.exists,
                actual.data,
                actual.identity,
                actual.mode,
                actual.metadata,
                recovery_path,
            )
        return actual
    finally:
        if temporary is not None and (temporary.exists() or temporary.is_symlink()):
            cleanup_snapshot = _global_rule_snapshot(temporary)
            if (
                cleanup_snapshot.identity is None
                or cleanup_snapshot.identity[:2] != owned_temporary_identity
            ):
                raise RuntimeError(f"暂存路径已被替换，拒绝删除：{temporary}")
            _unlink_verified_global_rule_recovery(temporary, cleanup_snapshot)
        # backup 只会在无法安全恢复时保留；绝不能在异常路径静默删除用户原版本。


def _restore_global_rule_write(record: _GlobalRuleWrite) -> None:
    """仅当目标仍是本轮写入值时回滚，绝不覆盖并发用户修改。"""
    current = _global_rule_snapshot(record.target)
    if _global_rule_snapshots_equivalent(
        current,
        record.original,
        require_same_object=True,
    ):
        if record.committed is not None and record.committed.recovery_path is not None:
            _unlink_verified_global_rule_recovery(
                record.committed.recovery_path,
                record.original,
            )
        return
    if record.committed is None:
        raise RuntimeError("writer 未返回已提交文件身份，无法确认目标属于本轮写入，已拒绝覆盖")
    if current != record.committed:
        raise RuntimeError("目标已有并发修改，已保留外部内容")

    if record.original.exists:
        restored = _write_global_rule_file(
            record.target,
            record.original.data,
            current,
            mode=record.original.mode,
        )
        if restored.data != record.original.data or restored.mode != record.original.mode:
            raise RuntimeError("恢复原字节或权限后复核失败")
        if restored.metadata != record.original.metadata:
            raise RuntimeError("恢复原附加元数据后复核失败")
        if restored.recovery_path is not None:
            _unlink_verified_global_rule_recovery(restored.recovery_path, current)
        if record.committed is not None and record.committed.recovery_path is not None:
            _unlink_verified_global_rule_recovery(
                record.committed.recovery_path,
                record.original,
            )
    else:
        quarantine_descriptor, quarantine_name = tempfile.mkstemp(
            prefix=f".{record.target.name}.jojo-rollback-",
            dir=str(record.target.parent),
        )
        os.close(quarantine_descriptor)
        quarantine = Path(quarantine_name)
        quarantine.unlink()
        _move_global_rule_file_no_replace(record.target, quarantine)
        moved = _global_rule_snapshot(quarantine)
        if not _global_rule_snapshots_equivalent(moved, current, require_same_object=True):
            if not record.target.exists():
                _move_global_rule_file_no_replace(quarantine, record.target)
                restored = _global_rule_snapshot(record.target)
                if not _global_rule_snapshots_equivalent(restored, moved):
                    raise RuntimeError(f"并发版本已隔离但恢复复核失败：{quarantine}")
                raise RuntimeError("删除窗口出现并发修改，已恢复外部内容")
            raise RuntimeError(f"删除窗口出现并发修改，外部内容保留在：{quarantine}")
        _unlink_verified_global_rule_recovery(quarantine, moved)


def _check_global_rules(findings: list[Finding], preview: bool = False) -> None:
    """只读检查两个用户级全局规则目标中的自动加载节。"""
    source = _global_rule_section_source_path()
    if not source.is_file():
        findings.append(Finding("BLOCKED", "全局规则", "自动加载节源文件", f"不存在：{source}"))
        return
    try:
        source_data = source.read_bytes()
        _global_rule_section_text(source_data)
    except (OSError, RuntimeError) as error:
        findings.append(Finding("BLOCKED", "全局规则", "自动加载节源文件", str(error)))
        return
    for target in _global_rule_target_paths():
        try:
            snapshot = _global_rule_snapshot(target)
            proposed = _upsert_global_rule_section(
                snapshot.data,
                source_data,
                create_title=not snapshot.exists,
            )
        except (OSError, RuntimeError) as error:
            findings.append(Finding("BLOCKED", "全局规则", str(target), str(error)))
            continue
        if not snapshot.exists:
            message = "目标不存在；确认后将创建普通标题和 jojo-code-guard 自动加载节"
            if preview:
                message += "\n" + _global_rule_diff(target, b"", proposed)
            findings.append(Finding("ACTION_REQUIRED", "全局规则", str(target), message))
            continue
        target_data = snapshot.data
        if target_data == proposed:
            findings.append(Finding("OK", "全局规则", str(target), "jojo-code-guard 自动加载节已是最新内容"))
            continue
        message = (
            "jojo-code-guard 自动加载节缺失、陈旧或重复；同步只会增改该节；"
            f"目标：{_global_rule_info(target_data)}"
        )
        if preview:
            message += "\n" + _global_rule_diff(target, target_data, proposed)
        findings.append(Finding("WARNING", "全局规则", str(target), message))


def _sync_global_rules() -> list[str]:
    """只新增或更新两个用户级全局规则目标中的自动加载节。"""
    source = _global_rule_section_source_path()
    if not source.is_file():
        raise RuntimeError(f"自动加载节源文件不存在：{source}")
    source_data = source.read_bytes()
    _global_rule_section_text(source_data)
    plans: list[tuple[Path, bytes, _GlobalRuleSnapshot]] = []
    for target in _global_rule_target_paths():
        snapshot = _global_rule_snapshot(target)
        data = _upsert_global_rule_section(
            snapshot.data,
            source_data,
            create_title=not snapshot.exists,
        )
        plans.append((target, data, snapshot))

    if any(not snapshot.exists or snapshot.data != data for _, data, snapshot in plans):
        _require_secure_private_directory_support()

    changed: list[str] = []
    written: list[_GlobalRuleWrite] = []
    created_directories: list[Path] = []
    try:
        for target, _, _ in plans:
            _ensure_global_rule_parent(target.parent, created_directories)
        write_parents = {
            target.parent
            for target, data, snapshot in plans
            if not snapshot.exists or snapshot.data != data
        }
        for parent in sorted(write_parents, key=str):
            _probe_global_rule_no_replace(parent)
        for target, data, snapshot in plans:
            if snapshot.exists and snapshot.data == data:
                continue
            record = _GlobalRuleWrite(target, snapshot, data)
            written.append(record)
            record.committed = _write_global_rule_file(target, data, snapshot)
            changed.append(str(target))
        written_by_target = {record.target: record for record in written}
        for target, _, snapshot in plans:
            current = _global_rule_snapshot(target)
            record = written_by_target.get(target)
            if record is not None:
                if record.committed is None or current != record.committed:
                    raise RuntimeError(
                        f"写入后文件身份、字节、权限或附加元数据复核失败：{target}"
                    )
            elif current != snapshot:
                raise RuntimeError(f"预检后目标发生并发修改：{target}")
        for record in written:
            if record.committed is None or record.committed.recovery_path is None:
                continue
            _unlink_verified_global_rule_recovery(
                record.committed.recovery_path,
                record.original,
            )
    except BaseException as error:
        rollback_errors: list[str] = []
        for record in reversed(written):
            try:
                _restore_global_rule_write(record)
            except (OSError, RuntimeError) as rollback_error:
                rollback_errors.append(f"{record.target}: {rollback_error}")
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except FileNotFoundError:
                continue
            except OSError as rollback_error:
                rollback_errors.append(f"{directory}: 无法清理本轮新建目录：{rollback_error}")
        if isinstance(error, (KeyboardInterrupt, SystemExit)) and not rollback_errors:
            raise
        message = f"写入失败并已回滚：{error}"
        if rollback_errors:
            message = f"写入失败：{error}；回滚失败或存在并发冲突：" + "；".join(rollback_errors)
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


def _check_worktree_status(findings: list[Finding], repo: Path) -> None:
    """区分干净工作区与 Git 状态查询失败，避免失败时误报安全。"""
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        findings.append(
            Finding(
                "BLOCKED",
                "工作区",
                "未提交修改",
                "git status 执行失败，无法确认工作区是否干净" + (f"：{detail}" if detail else ""),
            )
        )
        return
    status = result.stdout.decode("utf-8", errors="replace").strip()
    if status:
        findings.append(Finding("WARNING", "工作区", "未提交修改", "存在；修复配置前不要覆盖这些修改"))
    else:
        findings.append(Finding("OK", "工作区", "未提交修改", "干净"))


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
    _check_worktree_status(findings, repo)
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


def _template(name: str) -> bytes:
    """返回只用于缺失文件的保守模板。"""
    templates = {
        ".editorconfig": """root = true\n\n[*]\n# 老项目不强制全局编码和换行，避免编辑器保存时改写历史文件。\ncharset = unset\nend_of_line = unset\ninsert_final_newline = unset\ntrim_trailing_whitespace = false\nindent_style = space\nindent_size = 4\n""",
        ".gitattributes": (
            "# 老项目默认保留文件原始字节，避免 Git 自动转换换行。\n"
            "* -text\n\n"
            "# Windows 批处理必须以 CRLF 检出，避免 cmd.exe 解析异常。\n"
            "*.bat text eol=crlf\n"
            "*.cmd text eol=crlf\n"
        ),
        ".gitignore": """# 常见 C++ 构建和 IDE 输出\n/build/\n/out/\n/.vs/\n/CMakeFiles/\nCMakeCache.txt\ncompile_commands.json\n\n# 仅共享项目级 VS Code 设置\n/.vscode/*\n!/.vscode/settings.json\n""",
    }
    return templates[name].encode("utf-8")


def _repair_file_identity(path: Path, label: str) -> tuple[int, ...] | None:
    """记录待修复规则文件身份，并拒绝链接、reparse 和多链接对象。"""
    _assert_global_rule_path_safe(path)
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"{label}不是普通文件：{path}")
    if details.st_nlink != 1:
        raise RuntimeError(f"{label}是硬链接或多链接文件：{path}")
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(stat.S_IFMT(details.st_mode)),
        int(details.st_size),
        int(details.st_mtime_ns),
        int(details.st_nlink),
    )


def _write_missing_repair_template(path: Path, data: bytes, label: str) -> None:
    """用 O_EXCL 创建缺失模板，拒绝最后窗口出现的链接或文件。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o666)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise RuntimeError(f"无法独占创建{label}：{path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _repair_file_identity(path, label) is None or path.read_bytes() != data:
        raise RuntimeError(f"{label}创建后复核失败：{path}")


def _read_verified_repair_file(
    path: Path,
    expected: tuple[int, ...],
    label: str,
) -> tuple[bytes, str | None]:
    """从身份已钉住的普通文件读取严格 UTF-8 文本。"""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        opened_identity = (
            int(opened.st_dev),
            int(opened.st_ino),
            int(stat.S_IFMT(opened.st_mode)),
            int(opened.st_size),
            int(opened.st_mtime_ns),
            int(opened.st_nlink),
        )
        if opened_identity != expected or not stat.S_ISREG(opened.st_mode):
            raise RuntimeError(f"{label}在读取前发生变化：{path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            data = stream.read()
            after = os.fstat(stream.fileno())
            after_identity = (
                int(after.st_dev),
                int(after.st_ino),
                int(stat.S_IFMT(after.st_mode)),
                int(after.st_size),
                int(after.st_mtime_ns),
                int(after.st_nlink),
            )
            if after_identity != expected:
                raise RuntimeError(f"{label}在读取期间发生变化：{path}")
    except OSError as error:
        raise RuntimeError(f"无法安全读取{label}：{path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _repair_file_identity(path, label) != expected:
        raise RuntimeError(f"{label}路径在读取期间发生变化：{path}")
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        text = None
    return data, text


def _append_verified_repair_file(
    path: Path,
    expected: tuple[int, ...],
    original: bytes,
    addition: str,
    label: str,
) -> None:
    """通过钉住的普通文件描述符追加文本，并复核没有越界或并发改写。"""
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    addition_data = addition.encode("utf-8")
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        opened_identity = (
            int(opened.st_dev),
            int(opened.st_ino),
            int(stat.S_IFMT(opened.st_mode)),
            int(opened.st_size),
            int(opened.st_mtime_ns),
            int(opened.st_nlink),
        )
        if opened_identity != expected or not stat.S_ISREG(opened.st_mode):
            raise RuntimeError(f"{label}在写入前发生变化：{path}")
        pending = memoryview(addition_data)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise OSError(f"追加{label}时没有进展")
            pending = pending[written:]
        os.fsync(descriptor)
    except OSError as error:
        raise RuntimeError(f"无法安全追加{label}：{path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    current = _repair_file_identity(path, label)
    if current is None or current[:3] != expected[:3]:
        raise RuntimeError(f"{label}写入后身份发生变化：{path}")
    if path.read_bytes() != original + addition_data:
        raise RuntimeError(f"{label}写入后内容复核失败：{path}")


def _attributes_repair_preview(repo: Path) -> str | None:
    """返回批处理属性修复的拟议差异，不修改仓库。"""
    path = repo / ".gitattributes"
    if not path.exists():
        before = ""
        after = _template(".gitattributes").decode("utf-8")
    else:
        before = _read_utf8(path)
        if before is None:
            return None
        probes = [".jojo-code-guard-probe.bat", ".jojo-code-guard-probe.cmd"]
        effective = _check_attr(repo, probes)
        if all(_batch_attributes_are_crlf(effective.get(probe, {})) for probe in probes):
            return ""
        newline = "\r\n" if "\r\n" in before else "\n"
        separator = "" if before.endswith(("\n", "\r")) else newline
        after = (
            before
            + separator
            + newline
            + "# Windows 批处理必须以 CRLF 检出，避免 cmd.exe 解析异常。"
            + newline
            + "*.bat text eol=crlf"
            + newline
            + "*.cmd text eol=crlf"
            + newline
        )
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/.gitattributes",
            tofile="b/.gitattributes",
        )
    )


def repair_repo(repo: Path, install_hook: bool = False) -> list[str]:
    """创建缺失规则文件，并校正已明确授权的仓库本地 Git 保护项。"""
    created: list[str] = []
    paths = {name: repo / name for name in (".editorconfig", ".gitattributes", ".gitignore")}
    identities = {
        name: _repair_file_identity(path, name)
        for name, path in paths.items()
    }
    for name in (".editorconfig", ".gitattributes", ".gitignore"):
        path = paths[name]
        if identities[name] is None:
            _write_missing_repair_template(path, _template(name), name)
            identities[name] = _repair_file_identity(path, name)
            created.append(name)
    attributes_path = repo / ".gitattributes"
    attributes_identity = identities[".gitattributes"]
    if attributes_identity is None:
        raise RuntimeError(f".gitattributes 创建后身份缺失：{attributes_path}")
    attributes_data, attributes = _read_verified_repair_file(
        attributes_path,
        attributes_identity,
        ".gitattributes",
    )
    if attributes is not None:
        probes = [".jojo-code-guard-probe.bat", ".jojo-code-guard-probe.cmd"]
        effective = _check_attr(repo, probes)
        if any(not _batch_attributes_are_crlf(effective.get(probe, {})) for probe in probes):
            newline = "\r\n" if "\r\n" in attributes else "\n"
            separator = "" if attributes.endswith(("\n", "\r")) else newline
            addition = (
                separator
                + newline
                + "# Windows 批处理必须以 CRLF 检出，避免 cmd.exe 解析异常。"
                + newline
                + "*.bat text eol=crlf"
                + newline
                + "*.cmd text eol=crlf"
                + newline
            )
            _append_verified_repair_file(
                attributes_path,
                attributes_identity,
                attributes_data,
                addition,
                ".gitattributes",
            )
            created.append(".gitattributes 批处理 CRLF 规则（未执行 renormalize，未修改脚本或暂存区）")
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


def _install_tools(findings: list[Finding]) -> None:
    """用解析后的包管理器绝对路径安装缺失工具；提升由安装器自行处理。"""
    system = platform.system()
    commands: list[list[str]] = []
    package_manager: str | None = None
    if system == "Windows" and (winget := shutil.which("winget")):
        package_manager = str(Path(winget).expanduser().resolve())
        for tool, package in (("PowerShell 7", "Microsoft.PowerShell"), ("gsudo", "gerardog.gsudo"), ("ripgrep", "BurntSushi.ripgrep.MSVC")):
            executable = "pwsh" if tool == "PowerShell 7" else "gsudo" if tool == "gsudo" else "rg"
            if shutil.which(executable):
                continue
            commands.append(
                [
                    package_manager,
                    "install",
                    "--id",
                    package,
                    "--exact",
                    "--source",
                    "winget",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ]
            )
    elif system == "Darwin" and (brew := shutil.which("brew")) and not shutil.which("rg"):
        package_manager = str(Path(brew).expanduser().resolve())
        commands.append([package_manager, "install", "ripgrep"])
    if commands:
        for command in commands:
            code, output = _run(command)
            findings.append(
                Finding(
                    "OK" if code == 0 else "BLOCKED",
                    "设备安装",
                    " ".join(command[:4]),
                    output or "安装命令已执行；如安装器请求 UAC，必须由使用者在系统提示中确认",
                )
            )
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
        action="store_true",
        help="只新增或更新用户级全局规则中的 jojo-code-guard 自动加载节",
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
                    "OK",
                    "设备",
                    "Git Bash",
                    f"已安装于 {git_bash}；即使 bash 不在 Windows PATH，Claude 的 Bash shell 与包内 Windows 启动器仍可定位它",
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
    _check_plugin_update(findings, expected_version)
    _check_claude_hooks(findings, expected_version=expected_version)
    _check_codex_plugin(findings, expected_version=expected_version)
    _check_global_rules(findings, preview=options.sync_global_rules)

    has_action = options.repair or options.install_hook or options.install_tools or options.sync_global_rules
    if has_action:
        if not options.yes:
            if options.repair and repo is not None:
                preview = _attributes_repair_preview(repo)
                if preview:
                    findings.append(
                        Finding(
                            "ACTION_REQUIRED",
                            "修复",
                            ".gitattributes 拟议差异",
                            preview
                            + "添加后，后续 checkout/reset/暂存可能把现有 .bat/.cmd 转换为 CRLF；"
                            "不会执行 git add --renormalize，也不会修改脚本或暂存区",
                        )
                    )
            if options.sync_global_rules:
                findings.append(
                    Finding(
                        "ACTION_REQUIRED",
                        "全局规则",
                        "确认",
                        "已选择自动加载节同步；确认节级差异后添加 --yes",
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
                    changed = _sync_global_rules()
                    message = "、".join(changed) if changed else "目标已是期望内容，无需写入"
                    findings.append(Finding("OK", "全局规则", "自动加载节", message))
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
            print("[5] 预览自动加载节差异：doctor.py --sync-global-rules")
    return 1 if any(item.level == "BLOCKED" for item in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())

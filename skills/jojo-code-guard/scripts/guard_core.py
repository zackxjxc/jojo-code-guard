#!/usr/bin/env python3
"""啾啾代码守护的跨平台字节级检查核心。"""

from __future__ import annotations

import dataclasses
import difflib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
from typing import Collection, Iterable, Mapping, Optional, Sequence


UTF8_BOM = b"\xef\xbb\xbf"
UTF16_LE_BOM = b"\xff\xfe"
UTF16_BE_BOM = b"\xfe\xff"
UTF32_LE_BOM = b"\xff\xfe\x00\x00"
UTF32_BE_BOM = b"\x00\x00\xfe\xff"

TEXT_SUFFIXES = {
    ".bat", ".c", ".cc", ".cfg", ".cmake", ".cmd", ".cpp", ".cs", ".css",
    ".cxx", ".h", ".hh", ".hlsl", ".hpp", ".htm", ".html", ".hxx", ".ini",
    ".inl", ".ipp", ".ixx", ".java", ".js", ".json", ".jsonc", ".md", ".m", ".mm",
    ".frag", ".glsl", ".inc", ".log", ".mk", ".make", ".patch", ".props", ".proto",
    ".ps1", ".py", ".rc", ".rc2", ".rst", ".sln", ".sh", ".sql", ".svg", ".tex",
    ".targets", ".toml", ".ts", ".txt", ".vcxproj", ".vert", ".xml", ".yaml", ".yml",
}
TEXT_NAMES = {
    ".editorconfig", ".gitattributes", ".gitignore", ".gitmodules", "AGENTS.md", "CMakeLists.txt",
    "Dockerfile", "Makefile", "post-write-check", "session-start",
}
# 常见二进制后缀不参与未知路径的文本推断，避免误报资源文件。
BINARY_SUFFIXES = {
    ".7z", ".a", ".avi", ".bmp", ".bz2", ".class", ".dll", ".dylib", ".eot", ".exe",
    ".flac", ".gif", ".gz", ".ico", ".jar", ".jpeg", ".jpg", ".lib", ".m4a", ".mkv",
    ".mov", ".mp3", ".mp4", ".o", ".obj", ".otf", ".pdb", ".pdf", ".png", ".pyc",
    ".rar", ".so", ".tar", ".tif", ".tiff", ".ttf", ".wasm", ".wav", ".webm", ".webp",
    ".woff", ".woff2", ".xz", ".zip",
}
INITIAL_BASELINE_RELAXABLE_CODES = {
    "NEW_BOM",
    "NEW_ENCODING",
    "NEW_EOL",
    "NEW_FINAL_NEWLINE",
}
TOOL_TEXT_SUFFIXES = {
    ".css", ".frag", ".glsl", ".hlsl", ".html", ".props", ".proto", ".sln", ".svg", ".targets", ".vcxproj", ".vert", ".xml",
}
MAX_PROTECTED_FILE_BYTES = 16 * 1024 * 1024
MIGRATION_KINDS = frozenset({"encoding", "bom", "eol"})
MIGRATION_ENVIRONMENT = "JOJO_CODE_GUARD_ALLOW_MIGRATIONS"


class FileSizeLimitError(RuntimeError):
    """表示候选文本超过守护允许读取的字节上限。"""

    def __init__(self, path: str, size: int, limit: int) -> None:
        super().__init__(f"{path} 为 {size} 字节，超过检查上限 {limit} 字节")
        self.path = path
        self.size = size
        self.limit = limit


@dataclasses.dataclass(frozen=True)
class TextInfo:
    """描述文件的可验证字节属性。"""

    encoding: str
    bom: str
    eol: str
    final_newline: bool
    text: Optional[str]
    binary: bool
    error: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class Diagnostic:
    """描述一条检查结果。"""

    level: str
    code: str
    path: str
    message: str


def parse_migration_allowances(values: Iterable[str]) -> dict[str, set[str]]:
    """解析重复的 KIND:PATH 显式迁移许可。"""
    allowances: dict[str, set[str]] = {}
    for value in values:
        kind, separator, raw_path = value.partition(":")
        kind = kind.strip().lower()
        normalized = raw_path.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        path = pathlib.PurePosixPath(normalized)
        if not separator or kind not in MIGRATION_KINDS:
            raise ValueError(
                f"迁移许可必须使用 KIND:PATH，KIND 只能是 {', '.join(sorted(MIGRATION_KINDS))}：{value}"
            )
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"迁移许可必须使用仓库内的精确相对路径：{value}")
        canonical = path.as_posix()
        allowances.setdefault(canonical, set()).add(kind)
    return allowances


def migration_allowances_from_environment() -> dict[str, set[str]]:
    """从 JSON 字符串数组读取 Hook 可用的显式迁移许可。"""
    raw = os.environ.get(MIGRATION_ENVIRONMENT, "").strip()
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{MIGRATION_ENVIRONMENT} 必须是 JSON 字符串数组：{error}") from error
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"{MIGRATION_ENVIRONMENT} 必须是 JSON 字符串数组")
    return parse_migration_allowances(values)


def merge_migration_allowances(
    *items: Mapping[str, Collection[str]],
) -> dict[str, set[str]]:
    """合并 CLI 与环境提供的精确路径迁移许可。"""
    merged: dict[str, set[str]] = {}
    for item in items:
        for path, kinds in item.items():
            merged.setdefault(path, set()).update(kinds)
    return merged


def is_known_binary_path(path: str) -> bool:
    """在读取内容前识别明确的二进制后缀。"""
    item = pathlib.PurePosixPath(path.replace("\\", "/"))
    return item.suffix.lower() in BINARY_SUFFIXES


def _large_file_diagnostic(error: FileSizeLimitError) -> Diagnostic:
    """把有界读取异常转换为可操作的阻断诊断。"""
    return Diagnostic(
        "BLOCKED",
        "FILE_TOO_LARGE",
        error.path,
        f"候选文本为 {error.size} 字节，超过 {error.limit} 字节检查上限；请拆分文件或明确调整守护上限",
    )


def run_git(repo: pathlib.Path, arguments: Sequence[str], check: bool = True) -> bytes:
    """执行 Git 并保留原始输出字节。"""
    result = subprocess.run(
        ["git", *arguments],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("Git 命令失败：git %s\n%s" % (" ".join(arguments), message))
    return result.stdout


def check_conversion_policy(repo: pathlib.Path, staged: bool) -> list[Diagnostic]:
    """在 Git 可能改写工作区换行时提示，避免丢失老文件基线。"""
    diff_arguments = ["diff"]
    if staged:
        diff_arguments.insert(1, "--cached")
    diff_result = subprocess.run(
        ["git", *diff_arguments, "--quiet"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if diff_result.returncode == 0:
        return []
    changed_paths = _decode_paths(run_git(repo, diff_arguments + ["--name-only", "-z"], check=False))
    changed_text = False
    for path in changed_paths:
        if is_known_binary_path(path):
            continue
        try:
            data = _blob_from_index(repo, path) if staged else _read_worktree(repo, path)
        except FileSizeLimitError:
            changed_text = True
            break
        if data is not None and is_text_path(path, data):
            changed_text = True
            break
    if not changed_text:
        return []

    config_values: list[tuple[str, str, str]] = []
    for scope in ("--system", "--global", "--local"):
        for key in ("core.autocrlf", "core.eol"):
            result = subprocess.run(
                ["git", "config", scope, "--get", key],
                cwd=str(repo),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            value = result.stdout.decode("utf-8", errors="replace").strip()
            if value:
                config_values.append((key, value, scope))

    effective_values: dict[str, str] = {}
    for key in ("core.autocrlf", "core.eol"):
        result = subprocess.run(
            ["git", "config", "--get", key],
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        value = result.stdout.decode("utf-8", errors="replace").strip()
        if value:
            effective_values[key] = value
    risky = [
        (key, value)
        for key, value in effective_values.items()
        if (key == "core.autocrlf" and value.lower() not in {"false", "0"})
        or (key == "core.eol" and value.lower() != "unset")
    ]
    if not risky:
        return []
    details = ", ".join(
        f"{key}={value}（来源：{','.join(scope for item_key, _, scope in config_values if item_key == key) or '默认'}）"
        for key, value in risky
    )
    level = "BLOCKED" if staged else "WARNING"
    remedies: list[str] = []
    for key, _ in risky:
        if key == "core.autocrlf":
            remedies.append("git config --local core.autocrlf false")
        elif key == "core.eol":
            origins = [scope for item_key, _, scope in config_values if item_key == key]
            if "--local" in origins:
                remedies.append("git config --local --unset core.eol")
            elif "--global" in origins:
                remedies.append("git config --global --unset core.eol（需确认全局影响）")
            elif "--system" in origins:
                remedies.append("请管理员执行 git config --system --unset core.eol")
            else:
                remedies.append("按 git config --show-origin --get-regexp '^core\\.eol$' 的来源处理")
    return [
        Diagnostic(
            level,
            "GIT_CONVERSION_POLICY",
            "Git",
            f"检测到 {details}；Git 可能已改写工作区换行，无法可靠恢复老文件基线。请先设置 "
            + "；".join(remedies)
            + "，并确认 .gitattributes，再检查 diff",
        )
    ]


def find_repo(start: pathlib.Path | str = ".") -> pathlib.Path:
    """定位当前工作树根目录，并兼容 Git worktree。"""
    start_path = pathlib.Path(start).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise RuntimeError(f"无法访问工作目录：{start_path}（{error}）") from error
    if result.returncode != 0:
        raise RuntimeError("当前目录不是 Git 工作树")
    return pathlib.Path(os.fsdecode(result.stdout.strip())).resolve()


def inspect_bytes(data: bytes) -> TextInfo:
    """严格识别常见编码、BOM 和换行，不使用有损文件解码。"""
    encoding = "utf-8"
    bom = "none"
    payload = data
    try:
        if data.startswith(UTF32_LE_BOM):
            encoding, bom, payload = "utf-32le", "utf-32le", data[4:]
            text = payload.decode("utf-32-le", errors="strict")
        elif data.startswith(UTF32_BE_BOM):
            encoding, bom, payload = "utf-32be", "utf-32be", data[4:]
            text = payload.decode("utf-32-be", errors="strict")
        elif data.startswith(UTF8_BOM):
            encoding, bom, payload = "utf-8", "utf-8", data[3:]
            text = payload.decode("utf-8", errors="strict")
        elif data.startswith(UTF16_LE_BOM):
            encoding, bom, payload = "utf-16le", "utf-16le", data[2:]
            text = payload.decode("utf-16-le", errors="strict")
        elif data.startswith(UTF16_BE_BOM):
            encoding, bom, payload = "utf-16be", "utf-16be", data[2:]
            text = payload.decode("utf-16-be", errors="strict")
        else:
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                try:
                    encoding = "cp936"
                    text = data.decode("cp936", errors="strict")
                except UnicodeDecodeError:
                    encoding = "gb18030"
                    text = data.decode("gb18030", errors="strict")
    except (UnicodeDecodeError, LookupError) as error:
        binary = b"\x00" in data
        return TextInfo(
            "binary" if binary else "unknown",
            bom,
            "binary" if binary else "unknown",
            False,
            None,
            binary,
            str(error),
        )

    if b"\x00" in data and encoding not in {"utf-16le", "utf-16be", "utf-32le", "utf-32be"}:
        return TextInfo("binary", bom, "binary", False, None, True, "包含 NUL 字节")

    crlf_count = text.count("\r\n")
    remaining = text.replace("\r\n", "")
    lf_count = remaining.count("\n")
    cr_count = remaining.count("\r")
    kinds = sum(bool(value) for value in (crlf_count, lf_count, cr_count))
    if kinds == 0:
        eol = "none"
    elif kinds > 1:
        eol = "mixed"
    elif crlf_count:
        eol = "crlf"
    elif lf_count:
        eol = "lf"
    else:
        eol = "cr"
    return TextInfo(encoding, bom, eol, text.endswith(("\n", "\r")), text, False)


def _has_disallowed_controls(text: str) -> bool:
    """判断文本中是否包含不应出现在源码里的控制字符。"""
    return _count_disallowed_controls(text) > 0


def _count_disallowed_controls(text: str) -> int:
    """统计文本中的源码控制字符数量。"""
    return sum(
        1
        for char in text
        if ord(char) < 32 and char not in {"\t", "\n", "\r", "\f", "\b"}
    )


def is_text_path(path: str, data: Optional[bytes] = None) -> bool:
    """根据文件名和字节内容筛选需要保护的文本文件。"""
    item = pathlib.PurePosixPath(path.replace("\\", "/"))
    if item.name in TEXT_NAMES or item.suffix.lower() in TEXT_SUFFIXES:
        return True
    if is_known_binary_path(path) or data is None:
        return False
    info = inspect_bytes(data)
    # 未知路径只要不是明确二进制，就作为候选文本处理；控制字符会在新增检查中阻断。
    return not info.binary


def _line_parts(text: str) -> list[tuple[str, str]]:
    """保留每一行的原始换行符。"""
    parts: list[tuple[str, str]] = []
    start = 0
    index = 0
    while index < len(text):
        if text[index] == "\r":
            ending = "\r\n" if index + 1 < len(text) and text[index + 1] == "\n" else "\r"
            parts.append((text[start:index], ending))
            index += len(ending)
            start = index
        elif text[index] == "\n":
            parts.append((text[start:index], "\n"))
            index += 1
            start = index
        else:
            index += 1
    if start < len(text) or not parts:
        parts.append((text[start:], ""))
    return parts


def compare_existing(
    path: str,
    old_data: bytes,
    new_data: bytes,
    allowed_migrations: Collection[str] = (),
) -> list[Diagnostic]:
    """检查已有文件是否发生了隐式编码、BOM 或换行迁移。"""
    old = inspect_bytes(old_data)
    new = inspect_bytes(new_data)
    allowed = set(allowed_migrations) & MIGRATION_KINDS
    diagnostics: list[Diagnostic] = []
    if old.binary or new.binary:
        if old.binary != new.binary:
            diagnostics.append(Diagnostic("BLOCKED", "BINARY_TEXT_CHANGED", path, "二进制/文本类型发生变化"))
        return diagnostics
    if old.encoding != new.encoding and "encoding" not in allowed:
        diagnostics.append(
            Diagnostic("BLOCKED", "ENCODING_CHANGED", path, f"编码发生变化：{old.encoding} -> {new.encoding}")
        )
    if old.bom != new.bom and "bom" not in allowed:
        diagnostics.append(Diagnostic("BLOCKED", "BOM_CHANGED", path, f"BOM 发生变化：{old.bom} -> {new.bom}"))
    if old.error or new.error or old.text is None or new.text is None:
        diagnostics.append(
            Diagnostic("BLOCKED", "UNKNOWN_ENCODING", path, new.error or old.error or "无法严格识别编码")
        )
        return diagnostics

    old_controls = _count_disallowed_controls(old.text)
    new_controls = _count_disallowed_controls(new.text)
    if new_controls > old_controls:
        diagnostics.append(Diagnostic("BLOCKED", "CONTROL_CHARACTER", path, "修改后新增源码控制字符"))

    old_normal = old.text.replace("\r\n", "\n").replace("\r", "\n")
    new_normal = new.text.replace("\r\n", "\n").replace("\r", "\n")
    if old_normal == new_normal and old_data != new_data:
        changed_properties: set[str] = set()
        if old.encoding != new.encoding:
            changed_properties.add("encoding")
        if old.bom != new.bom:
            changed_properties.add("bom")
        if old.text != new.text:
            changed_properties.add("eol")
        if changed_properties and changed_properties <= allowed:
            return diagnostics
        diagnostics.append(Diagnostic("BLOCKED", "PURE_TEXT_REWRITE", path, "内容未变，疑似仅重写编码或换行"))
        return diagnostics

    if old.eol != new.eol and "eol" not in allowed:
        diagnostics.append(Diagnostic("BLOCKED", "EOL_CHANGED", path, f"换行类型发生变化：{old.eol} -> {new.eol}"))
    if old.final_newline != new.final_newline and "eol" not in allowed:
        diagnostics.append(Diagnostic("BLOCKED", "FINAL_NEWLINE_CHANGED", path, "文件末尾换行状态发生变化"))
    old_replacements = old.text.count("\ufffd")
    new_replacements = new.text.count("\ufffd")
    if new_replacements > old_replacements:
        diagnostics.append(Diagnostic("BLOCKED", "REPLACEMENT_CHARACTER", path, "修改后新增 U+FFFD 替换字符"))
    old_embedded_bom = old.text.count("\ufeff")
    new_embedded_bom = new.text.count("\ufeff")
    if new_embedded_bom > old_embedded_bom:
        diagnostics.append(Diagnostic("BLOCKED", "REPEATED_BOM", path, "修改后新增正文 BOM 字符 U+FEFF"))

    if "eol" in allowed:
        return diagnostics

    old_parts = _line_parts(old.text)
    new_parts = _line_parts(new.text)
    matcher = difflib.SequenceMatcher(
        None,
        [part[0] for part in old_parts],
        [part[0] for part in new_parts],
        autojunk=False,
    )
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            if any(
                old_parts[old_start + offset][1] != new_parts[new_start + offset][1]
                for offset in range(old_end - old_start)
            ):
                diagnostics.append(
                    Diagnostic("BLOCKED", "UNCHANGED_EOL_CHANGED", path, "未修改行的换行符发生变化")
                )
                break
        elif tag == "replace":
            # 等长替换通常表示内容被编辑但行数未变；此时换行符也必须保持逐行一致。
            common = min(old_end - old_start, new_end - new_start)
            if any(
                old_parts[old_start + offset][1] != new_parts[new_start + offset][1]
                for offset in range(common)
            ):
                diagnostics.append(
                    Diagnostic("BLOCKED", "EOL_CHANGED", path, "修改行的换行符发生变化")
                )
                break
    return diagnostics


def check_new(path: str, data: bytes) -> list[Diagnostic]:
    """检查新增文本文件的默认跨平台规范。"""
    suffix = pathlib.PurePosixPath(path).suffix.lower()
    if not is_text_path(path, data):
        return []
    info = inspect_bytes(data)
    if info.binary:
        return [Diagnostic("BLOCKED", "BINARY_SOURCE", path, "源码或配置文件被识别为二进制")]
    if info.error:
        return [Diagnostic("BLOCKED", "UNKNOWN_ENCODING", path, info.error)]
    if info.text and _has_disallowed_controls(info.text):
        return [Diagnostic("BLOCKED", "CONTROL_CHARACTER", path, "源码或配置文件包含控制字符")]
    if suffix == ".ps1":
        return _check_new_powershell(path, info)
    if suffix in TOOL_TEXT_SUFFIXES:
        diagnostics: list[Diagnostic] = []
        if info.encoding != "utf-8":
            diagnostics.append(
                Diagnostic("BLOCKED", "NEW_ENCODING", path, f"新增工具文件必须使用 UTF-8，当前为 {info.encoding}")
            )
        if info.eol not in {"none", "lf"}:
            diagnostics.append(Diagnostic("BLOCKED", "NEW_EOL", path, "新增工具文件默认使用 LF 换行"))
        if info.bom != "none":
            diagnostics.append(Diagnostic("BLOCKED", "NEW_BOM", path, f"新增工具文件不能带 BOM，当前为 {info.bom}"))
        if info.text and not info.final_newline:
            diagnostics.append(Diagnostic("BLOCKED", "NEW_FINAL_NEWLINE", path, "新增工具文件必须以换行结束"))
        if info.text and "\ufffd" in info.text:
            diagnostics.append(Diagnostic("BLOCKED", "REPLACEMENT_CHARACTER", path, "工具文件包含 U+FFFD 替换字符"))
        if info.text and "\ufeff" in info.text:
            diagnostics.append(Diagnostic("BLOCKED", "REPEATED_BOM", path, "工具文件正文包含额外 BOM 字符 U+FEFF"))
        return diagnostics
    expected_bom = "utf-8" if suffix in {".rc", ".rc2"} else "none"
    expected_eol = "crlf" if suffix in {".bat", ".cmd"} else "lf"
    diagnostics: list[Diagnostic] = []
    if info.encoding != "utf-8":
        diagnostics.append(Diagnostic("BLOCKED", "NEW_ENCODING", path, f"新增文件必须使用 UTF-8，当前为 {info.encoding}"))
    if info.bom != expected_bom:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_BOM", path, f"BOM 应为 {expected_bom}，当前为 {info.bom}"))
    if info.eol not in {"none", expected_eol}:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_EOL", path, f"换行应为 {expected_eol}，当前为 {info.eol}"))
    if info.text and not info.final_newline:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_FINAL_NEWLINE", path, "新增文本文件必须以换行结束"))
    if info.text and "\ufffd" in info.text:
        diagnostics.append(Diagnostic("BLOCKED", "REPLACEMENT_CHARACTER", path, "包含 U+FFFD 替换字符"))
    if info.text and "\ufeff" in info.text:
        diagnostics.append(Diagnostic("BLOCKED", "REPEATED_BOM", path, "正文包含额外 BOM 字符 U+FEFF"))
    return diagnostics


def _batch_attributes_require_crlf(repo: pathlib.Path, path: str) -> bool:
    """检查批处理路径最终生效的 Git 属性。"""
    suffix = pathlib.PurePosixPath(path).suffix.lower()
    if suffix not in {".bat", ".cmd"}:
        return False
    output = run_git(
        repo,
        ["--literal-pathspecs", "check-attr", "-z", "text", "eol", "--", path],
    )
    fields = output.split(b"\0")
    values: dict[str, str] = {}
    for index in range(0, len(fields) - 2, 3):
        returned_path = fields[index].decode("utf-8", errors="surrogateescape")
        if returned_path != path:
            raise RuntimeError(f"Git 属性查询返回了意外路径：期望 {path}，实际 {returned_path}")
        name = fields[index + 1].decode("ascii", errors="replace")
        values[name] = fields[index + 2].decode("utf-8", errors="replace")
    return values.get("text") == "set" and values.get("eol") == "crlf"


def _check_batch_crlf(path: str, data: bytes) -> list[Diagnostic]:
    """按有效 Git 属性检查批处理工作区原始字节。"""
    info = inspect_bytes(data)
    diagnostics: list[Diagnostic] = []
    if info.error or info.encoding != "utf-8":
        detail = info.error or f"当前为 {info.encoding}"
        diagnostics.append(Diagnostic("BLOCKED", "BATCH_ENCODING", path, f"批处理必须使用 UTF-8：{detail}"))
    if info.bom != "none":
        diagnostics.append(Diagnostic("BLOCKED", "BATCH_BOM", path, f"批处理必须为 UTF-8 无 BOM，当前为 {info.bom}"))
    if info.eol == "mixed":
        diagnostics.append(Diagnostic("BLOCKED", "BATCH_EOL", path, "批处理存在 LF/CRLF 混合换行，必须统一为 CRLF"))
    elif info.eol == "lf":
        diagnostics.append(Diagnostic("BLOCKED", "BATCH_EOL", path, "批处理当前为 LF，必须使用 CRLF"))
    elif info.eol not in {"none", "crlf"}:
        diagnostics.append(Diagnostic("BLOCKED", "BATCH_EOL", path, f"批处理换行为 {info.eol}，必须使用 CRLF"))
    return diagnostics


def _normalize_batch_for_index(data: bytes) -> bytes:
    """把已验证的 CRLF 工作区字节转换为 Git 索引使用的 LF。"""
    return data.replace(b"\r\n", b"\n")


def _check_tracked_batch_worktree(
    repo: pathlib.Path,
    max_file_bytes: int = MAX_PROTECTED_FILE_BYTES,
) -> list[Diagnostic]:
    """扫描标准 Git 属性覆盖的批处理，防止 clean 结果掩盖工作区 LF。"""
    paths = [
        path
        for path in _decode_paths(run_git(repo, ["ls-files", "-z"]))
        if pathlib.PurePosixPath(path).suffix.lower() in {".bat", ".cmd"}
    ]
    diagnostics: list[Diagnostic] = []
    for path in paths:
        if not _batch_attributes_require_crlf(repo, path):
            continue
        try:
            data = _read_worktree(repo, path, max_bytes=max_file_bytes)
        except FileSizeLimitError as error:
            diagnostics.append(_large_file_diagnostic(error))
            continue
        if data is not None:
            diagnostics.extend(_check_batch_crlf(path, data))
    return diagnostics


def _check_new_powershell(path: str, info: TextInfo) -> list[Diagnostic]:
    """按 PowerShell 运行目标检查新增脚本的 BOM 和换行。"""
    diagnostics: list[Diagnostic] = []
    if info.encoding != "utf-8":
        diagnostics.append(
            Diagnostic("BLOCKED", "NEW_ENCODING", path, f"新增 PowerShell 脚本必须使用 UTF-8，当前为 {info.encoding}")
        )
    if info.eol == "mixed":
        diagnostics.append(Diagnostic("BLOCKED", "NEW_EOL", path, "PowerShell 脚本不能混用 LF 和 CRLF"))
    elif info.eol not in {"none", "lf"}:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_EOL", path, "新增 PowerShell 脚本默认使用 LF 换行"))
    if info.bom not in {"none", "utf-8"}:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_BOM", path, f"PowerShell 脚本 BOM 只能是 none 或 utf-8，当前为 {info.bom}"))
    if info.text and not info.final_newline:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_FINAL_NEWLINE", path, "新增 PowerShell 脚本必须以换行结束"))
    if info.text and "\ufffd" in info.text:
        diagnostics.append(Diagnostic("BLOCKED", "REPLACEMENT_CHARACTER", path, "PowerShell 脚本包含 U+FFFD 替换字符"))
    if info.text and "\ufeff" in info.text:
        diagnostics.append(Diagnostic("BLOCKED", "REPEATED_BOM", path, "PowerShell 脚本正文包含额外 BOM 字符 U+FEFF"))

    if platform.system() == "Windows" and info.text and info.bom == "none":
        has_non_ascii = any(ord(char) > 127 for char in info.text)
        if has_non_ascii and not shutil.which("pwsh"):
            diagnostics.append(
                Diagnostic(
                    "BLOCKED",
                    "PS5_BOM_REQUIRED",
                    path,
                    "当前未找到 PowerShell 7；含中文的脚本需使用 UTF-8 BOM，或先安装并使用 pwsh",
                )
            )
        elif has_non_ascii:
            diagnostics.append(
                Diagnostic(
                    "WARNING",
                    "PS5_BOM_COMPATIBILITY",
                    path,
                    "脚本含非 ASCII 字符；若明确使用 Windows PowerShell 5.1，请改为 UTF-8 BOM，否则优先使用 pwsh",
                )
            )
    if platform.system() != "Windows" and info.bom == "utf-8" and info.text and info.text.startswith("#!"):
        diagnostics.append(Diagnostic("BLOCKED", "SHEBANG_BOM", path, "Unix shebang PowerShell 脚本不能带 UTF-8 BOM"))
    return diagnostics


def _decode_paths(output: bytes) -> list[str]:
    """解码 Git NUL 分隔路径并保留非法本地字节。"""
    return [item.decode("utf-8", errors="surrogateescape") for item in output.split(b"\0") if item]


def _changed_entries(repo: pathlib.Path, staged: bool) -> list[tuple[str, str, Optional[str]]]:
    """读取新增、修改和重命名记录。"""
    arguments = ["diff", "--name-status", "-z", "-M", "--diff-filter=AMR"]
    if staged:
        arguments.insert(1, "--cached")
    fields = _decode_paths(run_git(repo, arguments))
    entries: list[tuple[str, str, Optional[str]]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith("R"):
            old_path, new_path = fields[index], fields[index + 1]
            entries.append(("R", new_path, old_path))
            index += 2
        else:
            entries.append((status[:1], fields[index], None))
            index += 1
    return entries


def _blob_by_oid(
    repo: pathlib.Path,
    oid: str,
    path: str,
    max_bytes: int = MAX_PROTECTED_FILE_BYTES,
) -> bytes:
    """在读取对象正文前先检查 Git blob 大小。"""
    raw_size = run_git(repo, ["cat-file", "-s", oid]).strip()
    try:
        size = int(raw_size)
    except ValueError as error:
        raise RuntimeError(f"Git 返回了无效 blob 大小：{path}") from error
    if size > max_bytes:
        raise FileSizeLimitError(path, size, max_bytes)
    data = run_git(repo, ["cat-file", "blob", oid])
    if len(data) > max_bytes:
        raise FileSizeLimitError(path, len(data), max_bytes)
    return data


def _blob_from_tree(
    repo: pathlib.Path,
    revision: str,
    path: str,
    max_bytes: int = MAX_PROTECTED_FILE_BYTES,
) -> Optional[bytes]:
    """按对象 ID 读取树中的 blob，避免依赖工作区编码。"""
    output = run_git(repo, ["--literal-pathspecs", "ls-tree", "-z", revision, "--", path])
    record = output.split(b"\0", 1)[0]
    if not record or b"\t" not in record:
        return None
    metadata, raw_path = record.split(b"\t", 1)
    returned_path = raw_path.decode("utf-8", errors="surrogateescape")
    if returned_path != path:
        raise RuntimeError(f"Git 树查询返回了意外路径：期望 {path}，实际 {returned_path}")
    fields = metadata.split()
    if len(fields) < 3 or fields[1] != b"blob" or fields[0] in {b"120000", b"160000"}:
        return None
    return _blob_by_oid(repo, fields[2].decode("ascii"), path, max_bytes=max_bytes)


def _blob_from_index(
    repo: pathlib.Path,
    path: str,
    max_bytes: int = MAX_PROTECTED_FILE_BYTES,
) -> Optional[bytes]:
    """按对象 ID 读取暂存区 blob。"""
    output = run_git(repo, ["--literal-pathspecs", "ls-files", "--stage", "-z", "--", path])
    for record in output.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        returned_path = raw_path.decode("utf-8", errors="surrogateescape")
        if returned_path != path:
            raise RuntimeError(f"Git 索引查询返回了意外路径：期望 {path}，实际 {returned_path}")
        fields = metadata.split()
        if len(fields) >= 3 and fields[2] == b"0" and fields[0] not in {b"120000", b"160000"}:
            return _blob_by_oid(repo, fields[1].decode("ascii"), path, max_bytes=max_bytes)
    return None


def _relax_initial_baseline(items: Iterable[Diagnostic]) -> list[Diagnostic]:
    """仅放宽可解释的历史编码/EOL属性，不放过乱码或二进制类型错误。"""
    return [
        dataclasses.replace(item, level="WARNING", code="INITIAL_" + item.code)
        if item.level == "BLOCKED" and item.code in INITIAL_BASELINE_RELAXABLE_CODES
        else item
        for item in items
    ]


def check_changes(
    repo: pathlib.Path,
    staged: bool,
    include_untracked: bool = True,
    allow_initial_baseline: bool = False,
    migration_allowances: Optional[Mapping[str, Collection[str]]] = None,
    max_file_bytes: int = MAX_PROTECTED_FILE_BYTES,
) -> list[Diagnostic]:
    """检查暂存区或工作区变更。"""
    diagnostics: list[Diagnostic] = []
    allowances = migration_allowances or {}
    unborn = staged and subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    ).returncode != 0
    for status, path, old_path in _changed_entries(repo, staged):
        baseline_path = old_path or path
        if is_known_binary_path(path) and (status == "A" or is_known_binary_path(baseline_path)):
            continue
        try:
            new_data = (
                _blob_from_index(repo, path, max_bytes=max_file_bytes)
                if staged
                else _read_worktree(repo, path, max_bytes=max_file_bytes)
            )
        except FileSizeLimitError as error:
            diagnostics.append(_large_file_diagnostic(error))
            continue
        if new_data is None:
            continue
        old_data = None
        if status != "A":
            try:
                old_data = (
                    _blob_from_tree(repo, "HEAD", baseline_path, max_bytes=max_file_bytes)
                    if staged
                    else _blob_from_index(repo, baseline_path, max_bytes=max_file_bytes)
                )
            except FileSizeLimitError as error:
                diagnostics.append(_large_file_diagnostic(error))
                continue
        if not is_text_path(path, new_data) and not (
            old_data is not None and is_text_path(baseline_path, old_data)
        ):
            continue
        batch_crlf = _batch_attributes_require_crlf(repo, path)
        try:
            working_data = (
                _read_worktree(repo, path, max_bytes=max_file_bytes) if batch_crlf else None
            )
        except FileSizeLimitError as error:
            diagnostics.append(_large_file_diagnostic(error))
            working_data = None
        if batch_crlf and working_data is not None:
            diagnostics.extend(_check_batch_crlf(path, working_data))
        if status == "A":
            policy_data = working_data if batch_crlf and working_data is not None else new_data
            new_diagnostics = check_new(path, policy_data)
            if unborn and allow_initial_baseline:
                new_diagnostics = _relax_initial_baseline(new_diagnostics)
            diagnostics.extend(new_diagnostics)
            if staged and not batch_crlf:
                # Git 属性可能在索引和工作区之间做 clean/smudge；新增文件同时检查工作区字节，
                # 防止 CRLF 被规范化后掩盖实际保存格式。
                try:
                    working_data = _read_worktree(repo, path, max_bytes=max_file_bytes)
                except FileSizeLimitError as error:
                    diagnostics.append(_large_file_diagnostic(error))
                    working_data = None
                if working_data is not None and working_data != new_data:
                    working_diagnostics = check_new(path, working_data)
                    if unborn and allow_initial_baseline:
                        working_diagnostics = _relax_initial_baseline(working_diagnostics)
                    diagnostics.extend(working_diagnostics)
            continue
        if old_data is not None:
            comparison_old = _normalize_batch_for_index(old_data) if batch_crlf else old_data
            comparison_new = _normalize_batch_for_index(new_data) if batch_crlf else new_data
            diagnostics.extend(
                compare_existing(
                    path,
                    comparison_old,
                    comparison_new,
                    allowed_migrations=allowances.get(path, ()),
                )
            )

    if not staged and include_untracked:
        for path in _decode_paths(run_git(repo, ["ls-files", "--others", "--exclude-standard", "-z"])):
            if is_known_binary_path(path):
                continue
            try:
                data = _read_worktree(repo, path, max_bytes=max_file_bytes)
            except FileSizeLimitError as error:
                diagnostics.append(_large_file_diagnostic(error))
                continue
            if data is not None and is_text_path(path, data):
                diagnostics.extend(check_new(path, data))
    if not staged:
        diagnostics.extend(_check_tracked_batch_worktree(repo, max_file_bytes=max_file_bytes))
    return _deduplicate(diagnostics)


def _filter_new_policy_migrations(
    items: Iterable[Diagnostic],
    allowed_migrations: Collection[str],
) -> list[Diagnostic]:
    """仅为精确路径过滤已显式授权的新增/提交树属性。"""
    allowed = set(allowed_migrations) & MIGRATION_KINDS
    categories = {
        "NEW_ENCODING": "encoding",
        "NEW_BOM": "bom",
        "PS5_BOM_REQUIRED": "bom",
        "NEW_EOL": "eol",
        "NEW_FINAL_NEWLINE": "eol",
    }
    return [item for item in items if categories.get(item.code) not in allowed]


def _tracked_whitespace_diagnostics(path: str, text: str) -> list[Diagnostic]:
    """对提交树全文执行 Git 常用空白规则。"""
    diagnostics: list[Diagnostic] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    content_lines = lines[:-1] if normalized.endswith("\n") else lines
    for number, line in enumerate(content_lines, start=1):
        if line.endswith((" ", "\t")):
            diagnostics.append(
                Diagnostic("BLOCKED", "TRACKED_WHITESPACE", path, f"第 {number} 行包含尾随空白")
            )
            break
    for number, line in enumerate(content_lines, start=1):
        indent = re.match(r"^[ \t]*", line).group(0)
        if " \t" in indent:
            diagnostics.append(
                Diagnostic("BLOCKED", "TRACKED_WHITESPACE", path, f"第 {number} 行缩进中存在 tab 前空格")
            )
            break
    if normalized.endswith("\n") and len(lines) >= 2 and lines[-2].strip(" \t") == "":
        diagnostics.append(Diagnostic("BLOCKED", "TRACKED_WHITESPACE", path, "文件末尾存在多余空白行"))
    return diagnostics


def check_tracked_revision(
    repo: pathlib.Path,
    revision: str,
    migration_allowances: Optional[Mapping[str, Collection[str]]] = None,
    max_file_bytes: int = MAX_PROTECTED_FILE_BYTES,
) -> list[Diagnostic]:
    """扫描指定提交中的全部普通 tracked blob，供 clean checkout 的 CI 使用。"""
    expression = f"{revision}^{{tree}}"
    tree_oid = run_git(
        repo,
        ["rev-parse", "--verify", "--end-of-options", expression],
    ).decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", tree_oid):
        raise RuntimeError(f"无法把 revision 解析为 Git tree：{revision}")
    output = run_git(repo, ["ls-tree", "-r", "-z", tree_oid])
    allowances = migration_allowances or {}
    diagnostics: list[Diagnostic] = []
    for record in output.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        fields = metadata.split()
        if len(fields) < 3 or fields[1] != b"blob" or fields[0] in {b"120000", b"160000"}:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if is_known_binary_path(path):
            continue
        try:
            data = _blob_by_oid(
                repo,
                fields[2].decode("ascii"),
                path,
                max_bytes=max_file_bytes,
            )
        except FileSizeLimitError as error:
            diagnostics.append(_large_file_diagnostic(error))
            continue
        if not is_text_path(path, data):
            continue
        policy_diagnostics = check_new(path, data)
        info = inspect_bytes(data)
        suffix = pathlib.PurePosixPath(path).suffix.lower()
        if suffix in {".bat", ".cmd"} and info.eol == "lf":
            # text/eol=crlf 的提交树规范表示本来就是 LF；工作区 CRLF 由属性检查负责。
            policy_diagnostics = [item for item in policy_diagnostics if item.code != "NEW_EOL"]
        diagnostics.extend(
            _filter_new_policy_migrations(policy_diagnostics, allowances.get(path, ()))
        )
        if info.text is not None:
            diagnostics.extend(_tracked_whitespace_diagnostics(path, info.text))
    return _deduplicate(diagnostics)


def _numstat_entries(output: bytes) -> list[tuple[str, str, str]]:
    """解析 `git diff --numstat -z`，保留原始 Unicode 路径和 rename 目标。"""
    records = output.split(b"\0")
    entries: list[tuple[str, str, str]] = []
    index = 0
    while index < len(records):
        header = records[index]
        index += 1
        if not header:
            continue
        fields = header.split(b"\t", 2)
        if len(fields) != 3:
            raise RuntimeError("git diff --numstat -z 返回了无法解析的记录")
        added = fields[0].decode("ascii", errors="replace")
        deleted = fields[1].decode("ascii", errors="replace")
        raw_path = fields[2]
        if not raw_path:
            if index + 1 >= len(records):
                raise RuntimeError("git diff --numstat -z 返回了不完整的重命名记录")
            index += 1  # old path 仅用于展示差异；检查应匹配 rename 后路径。
            raw_path = records[index]
            index += 1
        path = raw_path.decode("utf-8", errors="surrogateescape")
        entries.append((added, deleted, path))
    return entries


def check_diff_size(repo: pathlib.Path, staged: bool, block_format_only: bool = False) -> list[Diagnostic]:
    """识别异常膨胀或疑似仅格式变化的单文件 diff。"""
    arguments = ["diff", "--numstat", "-z"]
    if staged:
        arguments.insert(1, "--cached")
    output = run_git(repo, arguments)
    diagnostics: list[Diagnostic] = []
    for added_text, deleted_text, path in _numstat_entries(output):
        if added_text == "-" or deleted_text == "-":
            continue
        try:
            added, deleted = int(added_text), int(deleted_text)
        except ValueError:
            continue
        changed = added + deleted
        if changed < 200:
            continue
        ignore_args = ["--literal-pathspecs", "diff"]
        if staged:
            ignore_args.append("--cached")
        ignore_args.extend(["--ignore-all-space", "--numstat", "-z", "--", path])
        ignored = run_git(repo, ignore_args)
        ignored_entries = _numstat_entries(ignored)
        unexpected = [returned_path for _, _, returned_path in ignored_entries if returned_path != path]
        if unexpected:
            raise RuntimeError(
                f"Git diff 路径查询返回了意外路径：期望 {path}，实际 {unexpected[0]}"
            )
        if not ignored_entries:
            level = "BLOCKED" if block_format_only else "WARNING"
            diagnostics.append(Diagnostic(level, "FORMAT_ONLY_LARGE_DIFF", path, f"{changed} 行变化在忽略空白后消失，疑似大面积格式污染"))
        else:
            diagnostics.append(Diagnostic("WARNING", "LARGE_DIFF", path, f"单文件新增+删除 {changed} 行，需人工确认是否为必要改动"))
    return diagnostics


def check_filemode_changes(repo: pathlib.Path, staged: bool) -> list[Diagnostic]:
    """阻止未明确授权的已有文件权限位变化。"""
    arguments = ["diff", "--summary"]
    if staged:
        arguments.insert(1, "--cached")
    output = run_git(repo, arguments, check=False).decode("utf-8", errors="replace")
    diagnostics: list[Diagnostic] = []
    for line in output.splitlines():
        match = re.match(r"\s*mode change (\d+) => (\d+) (.+)$", line)
        if match:
            old_mode, new_mode, path = match.groups()
            diagnostics.append(
                Diagnostic(
                    "BLOCKED",
                    "FILEMODE_CHANGED",
                    path,
                    f"已有文件权限位发生变化：{old_mode} -> {new_mode}；请确认后再显式调整",
                )
            )
        elif line.lstrip().startswith("typechange "):
            path = line.split(" ", 1)[1]
            diagnostics.append(Diagnostic("BLOCKED", "FILETYPE_CHANGED", path, "文件类型发生变化（普通文件/符号链接）"))
    return diagnostics


def _read_worktree(
    repo: pathlib.Path,
    path: str,
    max_bytes: int = MAX_PROTECTED_FILE_BYTES,
) -> Optional[bytes]:
    """读取普通工作区文件，跳过目录和符号链接。"""
    candidate = repo / pathlib.Path(path)
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        size = candidate.stat().st_size
        if size > max_bytes:
            raise FileSizeLimitError(path, size, max_bytes)
        with candidate.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FileSizeLimitError(path, max(size, len(data)), max_bytes)
        return data
    except FileSizeLimitError:
        raise
    except OSError:
        return None


def _deduplicate(items: Iterable[Diagnostic]) -> list[Diagnostic]:
    """去除同一路径的重复诊断。"""
    seen: set[tuple[str, str, str]] = set()
    result: list[Diagnostic] = []
    for item in items:
        key = (item.code, item.path, item.message)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

#!/usr/bin/env python3
"""从发布仓库根目录原子生成完整的 Codex 插件适配包。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import uuid


PLUGIN_NAME = "jojo-code-guard"
TRANSACTION_FORMAT = 1
OWNERSHIP_FORMAT = 1
OWNERSHIP_MARKER_NAME = ".jojo-code-guard-owned.json"
SYNC_CLIENT = "codex"
PUBLIC_SKILL_ENTRYPOINTS = frozenset(
    {
        "skills/jojo-code-guard/SKILL.md",
        "skills/jojo-code-guard-doctor/SKILL.md",
        "skills/jojo-code-guard-check-diff/SKILL.md",
        "skills/jojo-code-guard-help/SKILL.md",
    }
)
CODEX_EMPTY_COMPONENT_FILES = (".mcp.json", ".app.json")
CODEX_MANIFEST_ALLOWED_KEYS = frozenset(
    {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "keywords",
        "skills",
        "interface",
    }
)
# 仅精确匹配本地发布历史生成的完整无 marker 适配包，不能按单个旧文件名取得删除授权。
LEGACY_PACKAGE_TREE_SHA256 = {
    "0.2.0": frozenset({"b367a00f79d6fe841405b12e6ddc4e1e30d2ddc64692b47954fe599fed44973e"}),
    "0.2.1": frozenset({"ae30c905b17b8764a68905b01d9ce6403141159dc0863fa155730ff4227d7d16"}),
    "0.2.2": frozenset({"9094de1980b7ab14bac41893e0b7b68f962609fdc359875ababf86939a8082dd"}),
    "0.2.3": frozenset({"4beb0aa5feeea588975b59fa8901574d116e7e85efb2240605334c17ac54c4ef"}),
    "0.2.4": frozenset({"b23742e4f8d522892539fa4cd03b9b3f6d100cb67b9c8cb384a728bf00cdfa27"}),
    "0.2.5": frozenset({"0f826dba44fcf8b9b2a93b8070eec842d10e0981772a27d461c880635bb2b05d"}),
    "0.2.6": frozenset({"761ef848c71a799a3bfa78c87bb69bdc7775a9e41494d54cdd0d381051c686b4"}),
    "0.2.7": frozenset({"9311f5414ac4c9fadd872d3772d94e2528717ab671bb1a8d4969d5a1bf9859ff"}),
    "0.2.8": frozenset({"70faa964d60b64a2039d498836700f87cba4ed6f16e928b03792a42adb3290d8"}),
    "0.2.9": frozenset({"9d924e29930cb6c37925555ff51cff9dfd31d467f969d8fb3e83e3703341278e"}),
    "0.2.10": frozenset({"991ce85bb8e9eb4d6d30851e362033b648a17362cfd096a93de1ac534a8c79f1"}),
    "0.2.11": frozenset({"fbbc94870737fb92a31b726cb47c366986d37cfeb934ea216effc23961a5c9a6"}),
    "0.2.12": frozenset({"a3156024d7ad6f66ba0f5b611d2baf7e87ae537dda86d6b595d9d62665309299"}),
    "0.2.13": frozenset(
        {
            "31cb25efc482103493d48533d6b7b59ec3299008e2d5ca1dbbbed087f4610542",
            "623784323dce184224a69626655c4b396f36bac77a0d33cf0bb41a7802ccd1e7",
            "6abe2d8a1d5970517455cde85a64f768f07d4f343062dc87d0473d80709df124",
            "4de608ae81633ff43414dc3e8267f6149c1deb66555a42be8fe5224b42fae0e3",
            "fbd48dbd59b8d5734b1774c6fbded1425868f99aafa8054de04b77414e20ee84",
        }
    ),
}


def _path_is_link_like(path: pathlib.Path) -> bool:
    """识别符号链接和 Windows junction 等 reparse point。"""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_point)


def _path_exists_without_following(path: pathlib.Path) -> bool:
    """判断目录项是否存在，包括断开的符号链接。"""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _first_link_like_ancestor(path: pathlib.Path) -> pathlib.Path | None:
    """从目标向文件系统根检查每个现存路径组件。"""
    current = path.absolute()
    while True:
        if _path_is_link_like(current):
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _absolute(path: pathlib.Path) -> pathlib.Path:
    """返回不解析链接的绝对路径。"""
    return pathlib.Path(os.path.abspath(os.fspath(path)))


def _identity_from_stat(details: os.stat_result) -> dict[str, int]:
    """从已打开对象的 stat 结果提取可跨 rename 比较的身份。"""
    return {
        "device": int(details.st_dev),
        "inode": int(details.st_ino),
        "type": int(stat.S_IFMT(details.st_mode)),
    }


def _path_identity(path: pathlib.Path) -> dict[str, int]:
    """记录可跨同卷 rename 比较的目录项身份。"""
    try:
        details = path.lstat()
    except OSError as error:
        raise RuntimeError(f"无法读取路径身份：{path}") from error
    return _identity_from_stat(details)


def _require_secure_private_directory_support() -> None:
    """拒绝 Windows 上尚未修复 tempfile owner-only DACL 的 Python。"""
    if os.name != "nt":
        return
    version = tuple(int(part) for part in sys.version_info[:3])
    minimum_micro = {
        (3, 9): 20,
        (3, 10): 15,
        (3, 11): 10,
        (3, 12): 4,
    }
    branch = version[:2]
    if branch < (3, 9) or (
        branch in minimum_micro and version[2] < minimum_micro[branch]
    ):
        raise RuntimeError(
            "当前 Windows Python 无法安全创建 owner-only 私有目录；"
            "最低安全补丁版为 3.9.20、3.10.15、3.11.10 或 3.12.4，3.13+ 也受支持"
        )


def _fsync_directory(path: pathlib.Path) -> None:
    """在支持目录 fsync 的平台持久化同目录 journal 与 rename 顺序。"""
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        )
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.FlushFileBuffers.argtypes = (ctypes.c_void_p,)
        kernel32.FlushFileBuffers.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.CreateFileW(
            str(_absolute(path)),
            0x40000000,
            0x1 | 0x2 | 0x4,
            None,
            3,
            0x02000000,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error), str(path))
        try:
            if not kernel32.FlushFileBuffers(handle):
                error = ctypes.get_last_error()
                raise OSError(error, ctypes.FormatError(error), str(path))
        finally:
            kernel32.CloseHandle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _move_entry_no_replace(source: pathlib.Path, target: pathlib.Path) -> None:
    """同卷原子移动且不覆盖最后窗口出现的文件或目录。"""
    if os.name == "nt":
        _fsync_directory(source.parent)
        if target.parent != source.parent:
            _fsync_directory(target.parent)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.MoveFileExW.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        )
        kernel32.MoveFileExW.restype = ctypes.c_int
        if not kernel32.MoveFileExW(str(source), str(target), 0x8):
            error = ctypes.get_last_error()
            if error in {80, 183}:
                raise FileExistsError(error, ctypes.FormatError(error), str(target))
            raise OSError(error, ctypes.FormatError(error), str(target))
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise RuntimeError("当前 libc 不提供 renameat2，拒绝覆盖式目录切换")
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 0x1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise RuntimeError("当前系统不提供 renamex_np，拒绝覆盖式目录切换")
        renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex_np.restype = ctypes.c_int
        if renamex_np(os.fsencode(source), os.fsencode(target), 0x4) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    else:
        raise RuntimeError("当前平台没有安全的 no-clobber rename 原语")
    _fsync_directory(source.parent)
    if target.parent != source.parent:
        _fsync_directory(target.parent)


def _same_identity(path: pathlib.Path, expected: object) -> bool:
    if not isinstance(expected, dict):
        return False
    try:
        return _path_identity(path) == expected
    except RuntimeError:
        return False


def _assert_identity(path: pathlib.Path, expected: object, label: str) -> None:
    if _path_is_link_like(path):
        raise RuntimeError(f"{label}变为链接/reparse 路径：{path}")
    if not _same_identity(path, expected):
        raise RuntimeError(f"{label}身份在操作期间发生变化：{path}")


def _capture_ancestor_snapshot(path: pathlib.Path) -> list[dict[str, object]]:
    """记录现存父路径链，供每个破坏性操作前复核。"""
    current = _absolute(path)
    snapshot: list[dict[str, object]] = []
    while True:
        if _path_is_link_like(current):
            raise RuntimeError(f"拒绝经由链接型安装路径操作目录：{current}")
        if not _path_exists_without_following(current):
            raise RuntimeError(f"安装路径的父目录不存在：{current}")
        snapshot.append({"path": str(current), "identity": _path_identity(current)})
        parent = current.parent
        if parent == current:
            return snapshot
        current = parent


def _assert_ancestor_snapshot(snapshot: object) -> None:
    if not isinstance(snapshot, list) or not snapshot:
        raise RuntimeError("事务记录中的父路径身份无效")
    for item in snapshot:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RuntimeError("事务记录中的父路径身份无效")
        path = pathlib.Path(item["path"])
        _assert_identity(path, item.get("identity"), "安装路径父目录")


def _regular_file_sha256(path: pathlib.Path, expected: os.stat_result) -> str:
    """从身份已钉住的普通文件读取内容摘要，并拒绝读取期间的变化。"""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    expected_identity = _identity_from_stat(expected)
    expected_open_state = (
        int(expected.st_size),
        int(expected.st_mtime_ns),
        int(expected.st_nlink),
    )
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        opened_state = (
            int(opened.st_size),
            int(opened.st_mtime_ns),
            int(opened.st_nlink),
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _identity_from_stat(opened) != expected_identity
            or opened_state != expected_open_state
        ):
            raise RuntimeError(f"普通文件在读取前发生变化：{path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        after_state = (
            int(after.st_size),
            int(after.st_mtime_ns),
            int(after.st_nlink),
        )
        if _identity_from_stat(after) != expected_identity or after_state != expected_open_state:
            raise RuntimeError(f"普通文件在读取期间发生变化：{path}")
    except OSError as error:
        raise RuntimeError(f"无法安全读取普通文件：{path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        current = path.lstat()
    except OSError as error:
        raise RuntimeError(f"普通文件在读取后消失：{path}") from error
    current_state = (
        int(current.st_size),
        int(current.st_mtime_ns),
        int(stat.S_IMODE(current.st_mode)),
        int(current.st_nlink),
    )
    expected_path_state = (
        int(expected.st_size),
        int(expected.st_mtime_ns),
        int(stat.S_IMODE(expected.st_mode)),
        int(expected.st_nlink),
    )
    if _identity_from_stat(current) != expected_identity or current_state != expected_path_state:
        raise RuntimeError(f"普通文件路径在读取期间发生变化：{path}")
    return digest.hexdigest()


def _safe_tree_snapshot(root: pathlib.Path) -> dict[str, dict[str, object]]:
    """不跟随链接遍历整棵树，并拒绝 reparse、特殊文件和硬链接。"""
    root = _absolute(root)
    snapshot: dict[str, dict[str, object]] = {}

    def visit(path: pathlib.Path, relative: pathlib.PurePath) -> None:
        if _path_is_link_like(path):
            raise RuntimeError(f"目录树包含链接/reparse 路径：{path}")
        try:
            details = path.lstat()
        except OSError as error:
            raise RuntimeError(f"目录树在遍历期间发生变化：{path}") from error
        mode_type = stat.S_IFMT(details.st_mode)
        key = "." if str(relative) == "." else relative.as_posix()
        record = {
            "device": int(details.st_dev),
            "inode": int(details.st_ino),
            "type": int(mode_type),
            "size": int(details.st_size),
            "mtime_ns": int(details.st_mtime_ns),
            "mode": int(stat.S_IMODE(details.st_mode)),
            "links": int(details.st_nlink),
        }
        identity = {
            "device": record["device"],
            "inode": record["inode"],
            "type": record["type"],
        }
        snapshot[key] = record
        if stat.S_ISREG(details.st_mode):
            if details.st_nlink > 1:
                raise RuntimeError(f"目录树包含普通文件硬链接：{path}")
            record["sha256"] = _regular_file_sha256(path, details)
            return
        if not stat.S_ISDIR(details.st_mode):
            raise RuntimeError(f"目录树包含未允许的特殊文件：{path}")
        try:
            with os.scandir(path) as iterator:
                names = sorted((entry.name for entry in iterator), key=str.casefold)
        except OSError as error:
            raise RuntimeError(f"无法安全遍历目录：{path}") from error
        _assert_identity(path, identity, "目录")
        for name in names:
            visit(path / name, relative / name)
        _assert_identity(path, identity, "目录")

    visit(root, pathlib.PurePath("."))
    return snapshot


def _fsync_regular_file(path: pathlib.Path, expected: dict[str, object]) -> None:
    """钉住并持久化一个 staging 普通文件，同时复核其身份与基础状态。"""
    if _path_is_link_like(path):
        raise RuntimeError(f"待持久化文件是链接/reparse 路径：{path}")
    flags = (os.O_RDWR if os.name == "nt" else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        details = os.fstat(descriptor)
        state = {
            "device": int(details.st_dev),
            "inode": int(details.st_ino),
            "type": int(stat.S_IFMT(details.st_mode)),
            "size": int(details.st_size),
            "mtime_ns": int(details.st_mtime_ns),
            "links": int(details.st_nlink),
        }
        if state != {key: expected[key] for key in state} or not stat.S_ISREG(details.st_mode):
            raise RuntimeError(f"待持久化文件在打开前发生变化：{path}")
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        after_state = {
            "device": int(after.st_dev),
            "inode": int(after.st_ino),
            "type": int(stat.S_IFMT(after.st_mode)),
            "size": int(after.st_size),
            "mtime_ns": int(after.st_mtime_ns),
            "links": int(after.st_nlink),
        }
        if after_state != state:
            raise RuntimeError(f"待持久化文件在 fsync 期间发生变化：{path}")
    except OSError as error:
        raise RuntimeError(f"无法持久化 staging 文件：{path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _assert_identity(
        path,
        {key: int(expected[key]) for key in ("device", "inode", "type")},
        "待持久化文件",
    )


def _fsync_generated_tree(
    root: pathlib.Path,
    expected: dict[str, dict[str, object]],
) -> None:
    """先持久化所有文件，再自底向上持久化目录项，并复核整树未变化。"""
    if _safe_tree_snapshot(root) != expected:
        raise RuntimeError(f"staging 目录树在持久化前发生变化：{root}")
    files = [relative for relative, record in expected.items() if "sha256" in record]
    for relative in sorted(files, key=str.casefold):
        _fsync_regular_file(root / pathlib.PurePosixPath(relative), expected[relative])
    directories = [relative for relative, record in expected.items() if "sha256" not in record]
    for relative in sorted(
        directories,
        key=lambda value: (-len(pathlib.PurePosixPath(value).parts), value.casefold()),
    ):
        path = root if relative == "." else root / pathlib.PurePosixPath(relative)
        _fsync_directory(path)
    if _safe_tree_snapshot(root) != expected:
        raise RuntimeError(f"staging 目录树在持久化期间发生变化：{root}")


def _safe_file_identity(path: pathlib.Path, label: str) -> dict[str, int]:
    if _path_is_link_like(path):
        raise RuntimeError(f"{label}是链接/reparse 路径：{path}")
    try:
        details = path.lstat()
    except OSError as error:
        raise FileNotFoundError(f"{label}不存在：{path}") from error
    if not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"{label}不是普通文件：{path}")
    if details.st_nlink > 1:
        raise RuntimeError(f"{label}是普通文件硬链接：{path}")
    return _path_identity(path)


def _tree_content_digest(snapshot: dict[str, dict[str, object]]) -> str:
    """生成不含对象身份与时间戳的稳定目录内容摘要。"""
    entries: list[dict[str, object]] = []
    for relative in sorted(snapshot, key=str.casefold):
        if relative == OWNERSHIP_MARKER_NAME:
            continue
        record = snapshot[relative]
        item: dict[str, object] = {
            "path": relative,
            "type": record["type"],
        }
        if "sha256" in record:
            item["size"] = record["size"]
            item["sha256"] = record["sha256"]
        entries.append(item)
    payload = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_owned_marker(path: pathlib.Path) -> dict[str, object]:
    """从独占普通文件读取有界 ownership marker。"""
    identity = _safe_file_identity(path, "插件 ownership marker")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _identity_from_stat(opened) != identity
        ):
            raise RuntimeError(f"插件 ownership marker 在读取前发生变化：{path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(64 * 1024 + 1)
            if len(payload) > 64 * 1024:
                raise RuntimeError(f"插件 ownership marker 超过 64 KiB：{path}")
            after = os.fstat(stream.fileno())
            if _identity_from_stat(after) != identity or after.st_nlink != 1:
                raise RuntimeError(f"插件 ownership marker 在读取期间发生变化：{path}")
    except OSError as error:
        raise RuntimeError(f"无法读取插件 ownership marker：{path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _assert_identity(path, identity, "插件 ownership marker")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"插件 ownership marker 已损坏：{path}") from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"插件 ownership marker 格式无效：{path}")
    return parsed


def _write_ownership_marker(
    root: pathlib.Path,
    expected_tree: dict[str, dict[str, object]],
) -> None:
    """为本次生成树写入独立于 journal 的内容所有权证明。"""
    marker = root / OWNERSHIP_MARKER_NAME
    if _path_exists_without_following(marker):
        raise RuntimeError(f"插件 ownership marker 已存在：{marker}")
    snapshot = _safe_tree_snapshot(root)
    if snapshot != expected_tree:
        raise RuntimeError(f"插件目录树在 ownership marker 写入前发生变化：{root}")
    record = {
        "format": OWNERSHIP_FORMAT,
        "plugin": PLUGIN_NAME,
        "client": SYNC_CLIENT,
        "tree_sha256": _tree_content_digest(snapshot),
    }
    payload = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = -1
    try:
        descriptor = os.open(marker, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise RuntimeError(f"无法写入插件 ownership marker：{marker}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _safe_file_identity(marker, "插件 ownership marker")
    _fsync_directory(root)


def _validate_owned_tree(root: pathlib.Path) -> dict[str, dict[str, object]]:
    """校验 builder 写入的独立 ownership marker 与完整内容摘要。"""
    marker = root / OWNERSHIP_MARKER_NAME
    record = _read_owned_marker(marker)
    if set(record) != {"format", "plugin", "client", "tree_sha256"}:
        raise RuntimeError(f"插件 ownership marker 字段无效：{marker}")
    if (
        record.get("format") != OWNERSHIP_FORMAT
        or record.get("plugin") != PLUGIN_NAME
        or record.get("client") != SYNC_CLIENT
        or not isinstance(record.get("tree_sha256"), str)
    ):
        raise RuntimeError(f"插件 ownership marker 身份无效：{marker}")
    snapshot = _safe_tree_snapshot(root)
    if record["tree_sha256"] != _tree_content_digest(snapshot):
        raise RuntimeError(f"插件 ownership marker 与目录内容不一致：{root}")
    if _safe_tree_snapshot(root) != snapshot:
        raise RuntimeError(f"插件目录树在 ownership 校验期间发生变化：{root}")
    return snapshot


def _validate_owned_adapter(root: pathlib.Path) -> dict[str, dict[str, object]]:
    """校验适配包运行契约及 builder 写入的完整内容摘要。"""
    _validate_adapter(root)
    return _validate_owned_tree(root)


def _validate_manifest(path: pathlib.Path) -> dict[str, object]:
    """拒绝 manifest 通过自定义组件字段引入白名单外入口。"""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Codex manifest 无法安全读取：{path}") from error
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Codex manifest 顶层必须是对象：{path}")
    unexpected = sorted(set(manifest) - CODEX_MANIFEST_ALLOWED_KEYS)
    if unexpected:
        raise RuntimeError("Codex manifest 包含未允许字段：" + ", ".join(unexpected))
    if manifest.get("name") != PLUGIN_NAME:
        raise RuntimeError("Codex manifest 的插件名未允许")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise RuntimeError("Codex manifest 的版本无效")
    if manifest.get("skills") != "./skills/":
        raise RuntimeError("Codex manifest 的 skills 路径未允许")
    return manifest


def _skill_entrypoints(root: pathlib.Path) -> list[pathlib.Path]:
    """递归枚举 Skill，并阻止目录链接隐藏新的发现入口。"""
    if _path_is_link_like(root):
        return [root]
    if not root.is_dir():
        return []
    candidates: list[pathlib.Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        current = pathlib.Path(directory)
        for name in list(directory_names):
            path = current / name
            if _path_is_link_like(path):
                candidates.append(path)
                directory_names.remove(name)
        candidates.extend(current / name for name in file_names if name.casefold() == "skill.md")
    return candidates


def _unexpected_plugin_entrypoints(destination: pathlib.Path) -> list[str]:
    """列出 Codex 会自动发现、但本插件没有声明的运行入口。"""
    skills_root = destination / "skills"
    candidates = _skill_entrypoints(skills_root)
    for relative in CODEX_EMPTY_COMPONENT_FILES:
        path = destination / relative
        if _path_exists_without_following(path):
            candidates.append(path)
    return sorted(
        {
            path.relative_to(destination).as_posix()
            for path in candidates
            if path.relative_to(destination).as_posix() not in PUBLIC_SKILL_ENTRYPOINTS
        }
    )


def _copy(source: pathlib.Path, destination: pathlib.Path) -> None:
    """从钉住的源文件复制到 O_EXCL 目标，绝不覆盖并发出现的 staging 文件。"""
    source_identity = _safe_file_identity(source, "发布资源")
    if _path_is_link_like(destination.parent) or not destination.parent.is_dir():
        raise RuntimeError(f"复制目标父目录不安全：{destination.parent}")
    parent_identity = _path_identity(destination.parent)
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    source_descriptor = -1
    destination_descriptor = -1
    destination_identity: dict[str, int] | None = None
    try:
        source_descriptor = os.open(source, source_flags)
        source_details = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(source_details.st_mode)
            or source_details.st_nlink != 1
            or _identity_from_stat(source_details) != source_identity
        ):
            raise RuntimeError(f"发布资源在打开前发生变化：{source}")
        destination_descriptor = os.open(destination, destination_flags, 0o600)
        destination_identity = _identity_from_stat(os.fstat(destination_descriptor))
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            pending = memoryview(chunk)
            while pending:
                written = os.write(destination_descriptor, pending)
                if written <= 0:
                    raise OSError("写入 staging 文件时没有进展")
                pending = pending[written:]
        if os.name != "nt" and hasattr(os, "fchmod"):
            os.fchmod(destination_descriptor, stat.S_IMODE(source_details.st_mode))
        copied_details = os.fstat(destination_descriptor)
        source_after = os.fstat(source_descriptor)
        if (
            _identity_from_stat(source_after) != source_identity
            or source_after.st_nlink != 1
            or source_after.st_size != source_details.st_size
            or source_after.st_mtime_ns != source_details.st_mtime_ns
            or copied_details.st_size != source_details.st_size
        ):
            raise RuntimeError(f"发布资源在复制期间发生变化：{source}")
    except OSError as error:
        raise RuntimeError(f"无法独占复制发布资源到 staging：{destination}") from error
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)
    _assert_identity(source, source_identity, "发布资源")
    _assert_identity(destination.parent, parent_identity, "复制目标父目录")
    copied_identity = _safe_file_identity(destination, "复制后的发布资源")
    if destination_identity is None or copied_identity != destination_identity:
        raise RuntimeError(f"复制后的 staging 文件身份发生变化：{destination}")


def _copy_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
    """先后复核源树和副本，不跟随任何链接地复制发布目录。"""
    source = _absolute(source)
    destination = _absolute(destination)
    source_snapshot = _safe_tree_snapshot(source)
    if destination.exists() or _path_exists_without_following(destination):
        raise FileExistsError(f"复制目标已存在：{destination}")

    def copy_directory(current_source: pathlib.Path, current_destination: pathlib.Path) -> None:
        source_directory_identity = _path_identity(current_source)
        _assert_identity(current_source, source_directory_identity, "发布目录")
        current_destination.mkdir()
        try:
            with os.scandir(current_source) as iterator:
                names = sorted((entry.name for entry in iterator), key=str.casefold)
        except OSError as error:
            raise RuntimeError(f"无法安全遍历发布目录：{current_source}") from error
        for name in names:
            source_path = current_source / name
            if name == "__pycache__" or source_path.suffix.casefold() == ".pyc":
                continue
            if _path_is_link_like(source_path):
                raise RuntimeError(f"发布目录包含链接/reparse 路径：{source_path}")
            try:
                details = source_path.lstat()
            except OSError as error:
                raise RuntimeError(f"发布目录在复制期间发生变化：{source_path}") from error
            if stat.S_ISDIR(details.st_mode):
                copy_directory(source_path, current_destination / name)
            elif stat.S_ISREG(details.st_mode):
                if details.st_nlink > 1:
                    raise RuntimeError(f"发布目录包含普通文件硬链接：{source_path}")
                _copy(source_path, current_destination / name)
            else:
                raise RuntimeError(f"发布目录包含未允许的特殊文件：{source_path}")
        _assert_identity(current_source, source_directory_identity, "发布目录")

    # 复制失败时保留 partial staging：异常后无法证明所有目录项均由本进程创建，
    # 递归清理可能误删并发写入；顶层事务会失败关闭并报告 staging 的精确路径。
    copy_directory(source, destination)
    if _safe_tree_snapshot(source) != source_snapshot:
        raise RuntimeError(f"发布目录在复制期间发生变化：{source}")
    _safe_tree_snapshot(destination)


def _validate_adapter(destination: pathlib.Path) -> None:
    """确认生成目录包含 Codex 自动守护所需的全部资源。"""
    required = (
        destination / ".codex-plugin" / "plugin.json",
        destination / "hooks" / "hooks.json",
        destination / "hooks" / "session-start",
        destination / "hooks" / "post-write-check",
        destination / "hooks" / "run-hook.cmd",
        destination / "skills" / "jojo-code-guard" / "SKILL.md",
        destination / "skills" / "jojo-code-guard" / "PowerShell规则.md",
        destination / "skills" / "jojo-code-guard" / "references" / "通用行为规则.md",
        destination / "skills" / "jojo-code-guard" / "references" / "通用文件守护.md",
        destination / "skills" / "jojo-code-guard" / "references" / "C++专项规则.md",
        destination / "skills" / "jojo-code-guard" / "references" / "Git操作规则.md",
        destination / "skills" / "jojo-code-guard" / "references" / "usage.md",
        destination / "skills" / "jojo-code-guard" / "references" / "自动加载规则.md",
        destination / "skills" / "jojo-code-guard" / "scripts" / "doctor.py",
        destination / "skills" / "jojo-code-guard" / "scripts" / "check_diff.py",
        destination / "skills" / "jojo-code-guard" / "scripts" / "guard_core.py",
        destination / "skills" / "jojo-code-guard" / "scripts" / "hook_baseline.py",
        destination / "skills" / "jojo-code-guard" / "scripts" / "hook_check.py",
        destination / "skills" / "jojo-code-guard" / "scripts" / "install_hook.py",
        destination / "skills" / "jojo-code-guard-doctor" / "SKILL.md",
        destination / "skills" / "jojo-code-guard-check-diff" / "SKILL.md",
        destination / "skills" / "jojo-code-guard-help" / "SKILL.md",
        destination / "skills" / "jojo-code-guard" / "agents" / "openai.yaml",
        destination / "skills" / "jojo-code-guard-doctor" / "agents" / "openai.yaml",
        destination / "skills" / "jojo-code-guard-check-diff" / "agents" / "openai.yaml",
        destination / "skills" / "jojo-code-guard-help" / "agents" / "openai.yaml",
    )
    linked = sorted(
        {
            str(link)
            for path in required
            if (link := _first_link_like_ancestor(path)) is not None
        }
    )
    if linked:
        raise RuntimeError(
            "Codex 适配包包含未声明公开入口（链接型必需资源）：" + ", ".join(linked)
        )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Codex 适配包缺少资源：" + ", ".join(missing))
    _validate_manifest(destination / ".codex-plugin" / "plugin.json")
    unexpected = _unexpected_plugin_entrypoints(destination)
    if unexpected:
        raise RuntimeError("Codex 适配包包含未声明公开入口：" + ", ".join(unexpected))
    _safe_tree_snapshot(destination)


def _journal_path(destination: pathlib.Path) -> pathlib.Path:
    return destination.parent / f".{destination.name}.sync-transaction.json"


def _journal_identity(path: pathlib.Path) -> dict[str, int]:
    identity = _safe_file_identity(path, "插件同步事务记录")
    try:
        if path.lstat().st_nlink != 1:
            raise RuntimeError(f"插件同步事务记录不能是硬链接：{path}")
    except OSError as error:
        raise RuntimeError(f"无法复核插件同步事务记录：{path}") from error
    return identity


def _read_journal_bytes(path: pathlib.Path, expected: dict[str, int]) -> bytes:
    """从已钉住的独占普通文件读取有界 journal，不跟随替换后的路径。"""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        opened_identity = _identity_from_stat(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened_identity != expected
            or _journal_identity(path) != opened_identity
        ):
            raise RuntimeError(f"插件同步事务记录在读取前发生变化：{path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise RuntimeError(f"插件同步事务记录超过 1 MiB：{path}")
            after = os.fstat(stream.fileno())
            if _identity_from_stat(after) != opened_identity or after.st_nlink != 1:
                raise RuntimeError(f"插件同步事务记录在读取期间发生变化：{path}")
    except OSError as error:
        raise RuntimeError(f"无法读取插件同步事务记录：{path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _journal_identity(path) != expected:
        raise RuntimeError(f"插件同步事务记录路径在读取期间发生变化：{path}")
    return payload


def _write_journal_record(
    journal: pathlib.Path,
    record: dict[str, object],
    journal_identity: dict[str, int] | None,
    create: bool = False,
) -> dict[str, int]:
    """追加并 fsync 一个完整阶段；恢复时忽略不完整的最后一行。"""
    _assert_ancestor_snapshot(record["parent_snapshot"])
    if create:
        if _path_exists_without_following(journal):
            raise RuntimeError(f"插件同步事务记录已存在：{journal}")
    else:
        _assert_identity(journal, journal_identity, "插件同步事务记录")
        _journal_identity(journal)
    payload = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_CREAT | os.O_EXCL if create else os.O_APPEND
    descriptor = -1
    try:
        descriptor = os.open(journal, flags, 0o600)
        opened = os.fstat(descriptor)
        opened_identity = _identity_from_stat(opened)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RuntimeError(f"插件同步事务记录不是独占普通文件：{journal}")
        if journal_identity is not None and opened_identity != journal_identity:
            raise RuntimeError(f"插件同步事务记录身份在打开前发生变化：{journal}")
        if _journal_identity(journal) != opened_identity:
            raise RuntimeError(f"插件同步事务记录路径与已打开对象不一致：{journal}")
        with os.fdopen(descriptor, "ab" if not create else "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise RuntimeError(f"无法持久化插件同步事务记录：{journal}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = _journal_identity(journal)
    _assert_ancestor_snapshot(record["parent_snapshot"])
    if journal_identity is not None and identity != journal_identity:
        raise RuntimeError(f"插件同步事务记录身份在写入期间发生变化：{journal}")
    if create:
        _fsync_directory(journal.parent)
    return identity


def _read_journal(
    destination: pathlib.Path,
) -> tuple[dict[str, object], dict[str, int]] | None:
    journal = _journal_path(destination)
    if not _path_exists_without_following(journal):
        return None
    identity = _journal_identity(journal)
    payload = _read_journal_bytes(journal, identity)
    records: list[dict[str, object]] = []
    for raw_line in payload.splitlines(keepends=True):
        if not raw_line.endswith((b"\n", b"\r")):
            continue
        try:
            parsed = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"插件同步事务记录已损坏：{journal}") from error
        if not isinstance(parsed, dict):
            raise RuntimeError(f"插件同步事务记录格式无效：{journal}")
        records.append(parsed)
    if not records:
        raise RuntimeError(f"插件同步事务记录没有完整阶段：{journal}")
    invariant_keys = (
        "format",
        "destination",
        "staging",
        "backup",
        "parent_snapshot",
        "staging_identity",
        "destination_identity",
        "staging_tree",
        "destination_tree",
    )
    first = records[0]
    for record in records[1:]:
        if any(record.get(key) != first.get(key) for key in invariant_keys):
            raise RuntimeError(f"插件同步事务记录的身份字段发生变化：{journal}")
    record = records[-1]
    if record.get("format") != TRANSACTION_FORMAT:
        raise RuntimeError(f"插件同步事务记录版本不受支持：{journal}")
    if record.get("phase") not in {"prepared", "backed_up", "committed"}:
        raise RuntimeError(f"插件同步事务阶段无效：{journal}")
    if record.get("destination") != str(destination):
        raise RuntimeError(f"插件同步事务目标与当前安装目录不一致：{journal}")
    parent_snapshot = record.get("parent_snapshot")
    _assert_ancestor_snapshot(parent_snapshot)
    parent = destination.parent
    if (
        not isinstance(parent_snapshot, list)
        or not isinstance(parent_snapshot[0], dict)
        or parent_snapshot[0].get("path") != str(parent)
    ):
        raise RuntimeError(f"插件同步事务的父目录身份与目标不一致：{journal}")
    staging_value = record.get("staging")
    backup_value = record.get("backup")
    staging = pathlib.Path(staging_value) if isinstance(staging_value, str) else None
    if (
        staging is None
        or not staging.is_absolute()
        or _absolute(staging).parent != parent
        or not staging.name.startswith(f".{destination.name}.sync-")
        or staging in {destination, journal}
    ):
        raise RuntimeError(f"插件同步事务的暂存路径越界：{journal}")
    if backup_value is not None:
        if not isinstance(backup_value, str):
            raise RuntimeError(f"插件同步事务的备份路径无效：{journal}")
        backup_path = pathlib.Path(backup_value)
        backup = _absolute(backup_path)
        if (
            not backup_path.is_absolute()
            or backup.parent != parent
            or not backup.name.startswith(f".{destination.name}.backup-")
            or backup in {destination, staging, journal}
        ):
            raise RuntimeError(f"插件同步事务的备份路径越界：{journal}")
    return record, identity


def _transaction_guard(
    destination: pathlib.Path,
    record: dict[str, object],
    journal_identity: dict[str, int],
) -> None:
    _assert_ancestor_snapshot(record["parent_snapshot"])
    journal = _journal_path(destination)
    _assert_identity(journal, journal_identity, "插件同步事务记录")
    _journal_identity(journal)


def _remove_journal(
    destination: pathlib.Path,
    record: dict[str, object],
    journal_identity: dict[str, int],
) -> None:
    _transaction_guard(destination, record, journal_identity)
    journal = _journal_path(destination)
    _require_secure_private_directory_support()
    cleanup_root = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{journal.name}.discard-", dir=journal.parent)
    )
    quarantine = cleanup_root / "owned"
    moved = False
    try:
        _move_entry_no_replace(journal, quarantine)
        moved = True
        captured = _journal_identity(quarantine)
        if captured != journal_identity:
            if not _path_exists_without_following(journal):
                try:
                    _move_entry_no_replace(quarantine, journal)
                except OSError as restore_error:
                    raise RuntimeError(
                        f"journal 清理窗口捕获到外部文件；对象保留在：{quarantine}"
                    ) from restore_error
                moved = False
                raise RuntimeError(f"journal 清理窗口捕获到外部文件，已安全恢复：{journal}")
            raise RuntimeError(f"journal 清理窗口捕获到外部文件；对象保留在：{quarantine}")
        os.unlink(quarantine)
        moved = False
        _fsync_directory(cleanup_root)
    except OSError as error:
        raise RuntimeError(f"无法清理插件同步事务记录：{journal}") from error
    finally:
        if not moved:
            try:
                cleanup_root.rmdir()
            except FileNotFoundError:
                pass
    _assert_ancestor_snapshot(record["parent_snapshot"])


def _entry_state(path: pathlib.Path, expected: object) -> str:
    if not _path_exists_without_following(path):
        return "missing"
    if _path_is_link_like(path) or not _same_identity(path, expected):
        return "unexpected"
    return "expected"


def _preservation_error(record: dict[str, object], reason: str) -> RuntimeError:
    backup = record.get("backup")
    suffix = f"；备份仍位于：{backup}" if backup else ""
    return RuntimeError(
        f"{reason}；检测到并发目录变化，已保留 destination、staging 和 journal{suffix}"
    )


def _guarded_directory_replace(
    source: pathlib.Path,
    target: pathlib.Path,
    expected_source: object,
    destination: pathlib.Path,
    record: dict[str, object],
    journal_identity: dict[str, int],
    expected_source_tree: object | None = None,
) -> None:
    _transaction_guard(destination, record, journal_identity)
    _assert_identity(source, expected_source, "待切换目录")
    source_tree = _safe_tree_snapshot(source)
    if expected_source_tree is not None and source_tree != expected_source_tree:
        raise _preservation_error(record, f"待切换目录树在校验后发生变化：{source}")
    _assert_identity(source, expected_source, "待切换目录")
    if _path_exists_without_following(target):
        raise _preservation_error(record, f"目录切换目标已被占用：{target}")
    _transaction_guard(destination, record, journal_identity)
    try:
        _move_entry_no_replace(source, target)
    except FileExistsError as error:
        raise _preservation_error(record, f"目录切换目标被并发创建：{target}") from error
    except OSError as error:
        if _path_exists_without_following(target):
            raise _preservation_error(record, f"目录切换目标被并发创建：{target}") from error
        raise
    _transaction_guard(destination, record, journal_identity)
    _assert_identity(target, expected_source, "切换后的目录")
    if expected_source_tree is not None and _safe_tree_snapshot(target) != expected_source_tree:
        raise _preservation_error(record, f"切换后的目录树内容不匹配：{target}")
    if _path_exists_without_following(source):
        raise RuntimeError(f"目录切换后源路径仍然存在：{source}")


def _remove_owned_tree(
    path: pathlib.Path,
    expected: object,
    parent_snapshot: object,
    destination: pathlib.Path | None = None,
    record: dict[str, object] | None = None,
    journal_identity: dict[str, int] | None = None,
    expected_tree: object | None = None,
) -> None:
    _assert_ancestor_snapshot(parent_snapshot)
    if destination is not None and record is not None and journal_identity is not None:
        _transaction_guard(destination, record, journal_identity)
    _assert_identity(path, expected, "待清理目录")
    tree = _safe_tree_snapshot(path)
    if expected_tree is not None and tree != expected_tree:
        raise RuntimeError(f"待清理目录树内容在校验后发生变化：{path}")
    _assert_identity(path, expected, "待清理目录")
    _assert_ancestor_snapshot(parent_snapshot)
    if destination is not None and record is not None and journal_identity is not None:
        _transaction_guard(destination, record, journal_identity)
    _require_secure_private_directory_support()
    cleanup_root = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{path.name}.discard-", dir=path.parent)
    )
    quarantine = cleanup_root / "owned"
    moved = False
    try:
        _move_entry_no_replace(path, quarantine)
        moved = True
        if not _same_identity(quarantine, expected):
            if not _path_exists_without_following(path):
                try:
                    _move_entry_no_replace(quarantine, path)
                except OSError as restore_error:
                    raise RuntimeError(
                        f"目录清理窗口捕获到外部对象；对象保留在：{quarantine}"
                    ) from restore_error
                moved = False
                raise RuntimeError(f"目录清理窗口捕获到外部对象，已安全恢复：{path}")
            raise RuntimeError(f"目录清理窗口捕获到外部对象；对象保留在：{quarantine}")
        quarantined_tree = _safe_tree_snapshot(quarantine)
        if expected_tree is not None and quarantined_tree != expected_tree:
            raise RuntimeError(f"隔离后的待清理目录树内容不匹配：{quarantine}")
        try:
            shutil.rmtree(quarantine)
        except BaseException as error:
            raise RuntimeError(f"目录清理失败；受管对象保留在：{quarantine}") from error
        moved = False
        _fsync_directory(cleanup_root)
    finally:
        if not moved:
            try:
                cleanup_root.rmdir()
            except FileNotFoundError:
                pass
    _assert_ancestor_snapshot(parent_snapshot)
    if _path_exists_without_following(path):
        raise RuntimeError(f"目录清理后原路径被并发创建：{path}")


def _rollback_transaction(
    destination: pathlib.Path,
    record: dict[str, object],
    journal_identity: dict[str, int],
) -> None:
    staging = pathlib.Path(str(record["staging"]))
    backup_value = record.get("backup")
    backup = pathlib.Path(str(backup_value)) if backup_value is not None else None
    old_identity = record.get("destination_identity")
    new_identity = record.get("staging_identity")
    destination_state_old = _entry_state(destination, old_identity) if old_identity else "missing"
    destination_state_new = _entry_state(destination, new_identity)
    staging_state = _entry_state(staging, new_identity)
    backup_state = _entry_state(backup, old_identity) if backup is not None else "missing"

    if staging_state == "unexpected" or backup_state == "unexpected":
        raise _preservation_error(record, "回滚所需目录身份不匹配")
    if destination_state_old == "unexpected" and destination_state_new == "unexpected":
        raise _preservation_error(record, "安装目标在回滚前被并发创建或替换")

    if old_identity is not None:
        if destination_state_old == "expected" and backup_state == "missing":
            pass
        elif destination_state_new == "expected" and staging_state == "missing" and backup_state == "expected":
            _guarded_directory_replace(
                destination,
                staging,
                new_identity,
                destination,
                record,
                journal_identity,
                record.get("staging_tree"),
            )
            _guarded_directory_replace(
                backup,
                destination,
                old_identity,
                destination,
                record,
                journal_identity,
                record.get("destination_tree"),
            )
        elif not _path_exists_without_following(destination) and backup_state == "expected":
            _guarded_directory_replace(
                backup,
                destination,
                old_identity,
                destination,
                record,
                journal_identity,
                record.get("destination_tree"),
            )
        else:
            raise _preservation_error(record, "无法判定目录替换的安全回滚状态")
    else:
        if destination_state_new == "expected" and staging_state == "missing":
            _guarded_directory_replace(
                destination,
                staging,
                new_identity,
                destination,
                record,
                journal_identity,
                record.get("staging_tree"),
            )
        elif not _path_exists_without_following(destination) and staging_state == "expected":
            pass
        else:
            raise _preservation_error(record, "无法判定首次安装的安全回滚状态")
    _remove_journal(destination, record, journal_identity)


def _cleanup_committed_backup(
    destination: pathlib.Path,
    record: dict[str, object],
    journal_identity: dict[str, int],
    expected_tree: object | None = None,
) -> None:
    backup_value = record.get("backup")
    if backup_value is None:
        return
    backup = pathlib.Path(str(backup_value))
    if not _path_exists_without_following(backup):
        return
    if _entry_state(backup, record.get("destination_identity")) != "expected":
        raise _preservation_error(record, "已提交事务的备份身份不匹配")
    _remove_owned_tree(
        backup,
        record.get("destination_identity"),
        record["parent_snapshot"],
        destination,
        record,
        journal_identity,
        expected_tree if expected_tree is not None else record.get("destination_tree"),
    )


def _recover_transaction(destination: pathlib.Path) -> None:
    loaded = _read_journal(destination)
    if loaded is None:
        return
    record, journal_identity = loaded
    staging = pathlib.Path(str(record["staging"]))
    backup_value = record.get("backup")
    backup = pathlib.Path(str(backup_value)) if backup_value is not None else None
    old_identity = record.get("destination_identity")
    new_identity = record.get("staging_identity")
    destination_state_old = _entry_state(destination, old_identity) if old_identity else "missing"
    destination_state_new = _entry_state(destination, new_identity)
    staging_state = _entry_state(staging, new_identity)
    backup_state = _entry_state(backup, old_identity) if backup is not None else "missing"
    if staging_state == "unexpected" or backup_state == "unexpected":
        raise _preservation_error(record, "事务恢复所需目录身份不匹配")
    if destination_state_old == "unexpected" and destination_state_new == "unexpected":
        raise _preservation_error(record, "事务恢复发现未知安装目标")

    # journal 只是恢复线索，不单独构成删除授权；实际目录仍须符合本客户端的受管契约。
    staging_tree: object | None = None
    destination_new_tree: object | None = None
    destination_old_tree: object | None = None
    backup_tree: object | None = None
    if staging_state == "expected":
        staging_tree = _validate_owned_adapter(staging)
    if destination_state_new == "expected":
        destination_new_tree = _validate_owned_adapter(destination)
    elif destination_state_old == "expected":
        destination_old_tree = _validate_managed_destination(destination)["tree"]
    if backup_state == "expected" and backup is not None:
        backup_tree = _validate_managed_destination(backup)["tree"]

    expected_staging_tree = record.get("staging_tree")
    expected_destination_tree = record.get("destination_tree")
    if expected_staging_tree is not None and staging_tree is not None and staging_tree != expected_staging_tree:
        raise _preservation_error(record, "事务恢复发现暂存目录内容变化")
    if (
        expected_destination_tree is not None
        and destination_old_tree is not None
        and destination_old_tree != expected_destination_tree
    ):
        raise _preservation_error(record, "事务恢复发现旧安装内容变化")
    if (
        expected_destination_tree is not None
        and backup_tree is not None
        and backup_tree != expected_destination_tree
    ):
        raise _preservation_error(record, "事务恢复发现备份内容变化")
    if (
        expected_staging_tree is not None
        and destination_new_tree is not None
        and destination_new_tree != expected_staging_tree
    ):
        raise _preservation_error(record, "事务恢复发现新安装内容变化")

    if destination_state_new == "expected" and staging_state == "missing":
        try:
            _cleanup_committed_backup(
                destination,
                record,
                journal_identity,
                backup_tree,
            )
            _remove_journal(destination, record, journal_identity)
        except BaseException as error:
            raise RuntimeError(
                f"新安装已生效；备份清理未完成：{error}"
            ) from error
        return

    if old_identity is not None:
        if not _path_exists_without_following(destination) and backup_state == "expected":
            _guarded_directory_replace(
                backup,
                destination,
                old_identity,
                destination,
                record,
                journal_identity,
                backup_tree,
            )
        elif destination_state_old != "expected" or backup_state != "missing":
            raise _preservation_error(record, "事务恢复无法确认旧安装位置")
    elif _path_exists_without_following(destination):
        raise _preservation_error(record, "首次安装恢复发现未知目标")

    if staging_state == "expected":
        expected_prefix = f".{destination.name}.sync-"
        if not staging.name.startswith(expected_prefix):
            raise _preservation_error(record, "事务暂存目录名称不受管")
        _remove_owned_tree(
            staging,
            new_identity,
            record["parent_snapshot"],
            destination,
            record,
            journal_identity,
            staging_tree,
        )
    _remove_journal(destination, record, journal_identity)


def _replace_directory(
    staging: pathlib.Path,
    destination: pathlib.Path,
    expected_destination_identity: dict[str, object] | None = None,
    expected_parent_snapshot: list[dict[str, object]] | None = None,
    expected_staging_identity: dict[str, object] | None = None,
) -> None:
    """用带身份记录和可恢复 journal 的事务替换已校验目录。"""
    staging = _absolute(staging)
    destination = _absolute(destination)
    if staging.parent != destination.parent:
        raise RuntimeError("暂存目录与安装目标必须位于同一父目录")
    linked = _first_link_like_ancestor(destination)
    if linked is not None:
        raise RuntimeError(f"拒绝经由链接型安装路径覆盖目录：{linked}")
    if expected_parent_snapshot is not None:
        _assert_ancestor_snapshot(expected_parent_snapshot)
    _recover_transaction(destination)
    parent_snapshot = expected_parent_snapshot or _capture_ancestor_snapshot(destination.parent)
    _assert_ancestor_snapshot(parent_snapshot)
    staging_tree = _safe_tree_snapshot(staging)
    staging_identity = _path_identity(staging)
    if expected_staging_identity is not None:
        expected_staging_root = expected_staging_identity
        expected_staging_tree: object | None = None
        if "identity" in expected_staging_identity:
            expected_staging_root = expected_staging_identity.get("identity")  # type: ignore[assignment]
            expected_staging_tree = expected_staging_identity.get("tree")
        if staging_identity != expected_staging_root or (
            expected_staging_tree is not None and staging_tree != expected_staging_tree
        ):
            raise RuntimeError(f"暂存目录树内容在校验后发生变化：{staging}")
    old_identity: dict[str, int] | None = None
    old_tree: dict[str, dict[str, object]] | None = None
    if _path_exists_without_following(destination):
        if _path_is_link_like(destination) or not destination.is_dir():
            raise RuntimeError(f"拒绝覆盖非普通目录安装目标：{destination}")
        old_tree = _safe_tree_snapshot(destination)
        old_identity = _path_identity(destination)
        if expected_destination_identity is not None:
            expected_old_root = expected_destination_identity
            expected_old_tree: object | None = None
            if "identity" in expected_destination_identity:
                expected_old_root = expected_destination_identity.get("identity")  # type: ignore[assignment]
                expected_old_tree = expected_destination_identity.get("tree")
            if old_identity != expected_old_root or (
                expected_old_tree is not None and old_tree != expected_old_tree
            ):
                raise RuntimeError(f"安装目标目录树内容在校验后发生变化：{destination}")
    elif expected_destination_identity is not None:
        raise RuntimeError(f"安装目标在校验后消失：{destination}")

    backup = (
        destination.parent / f".{destination.name}.backup-{uuid.uuid4().hex}"
        if old_identity is not None
        else None
    )
    record: dict[str, object] = {
        "format": TRANSACTION_FORMAT,
        "phase": "prepared",
        "destination": str(destination),
        "staging": str(staging),
        "backup": str(backup) if backup is not None else None,
        "parent_snapshot": parent_snapshot,
        "staging_identity": staging_identity,
        "destination_identity": old_identity,
        "staging_tree": staging_tree,
        "destination_tree": old_tree,
    }
    journal = _journal_path(destination)
    journal_identity = _write_journal_record(journal, record, None, create=True)
    try:
        if backup is not None:
            _guarded_directory_replace(
                destination,
                backup,
                old_identity,
                destination,
                record,
                journal_identity,
                old_tree,
            )
            record = dict(record, phase="backed_up")
            journal_identity = _write_journal_record(journal, record, journal_identity)
        _guarded_directory_replace(
            staging,
            destination,
            staging_identity,
            destination,
            record,
            journal_identity,
            staging_tree,
        )
        record = dict(record, phase="committed")
        journal_identity = _write_journal_record(journal, record, journal_identity)
    except BaseException as original_error:
        try:
            _rollback_transaction(destination, record, journal_identity)
        except BaseException as rollback_error:
            if isinstance(rollback_error, RuntimeError):
                raise rollback_error from original_error
            raise RuntimeError(
                f"插件目录替换失败且旧安装恢复失败，备份仍位于：{backup}"
            ) from rollback_error
        raise

    try:
        _cleanup_committed_backup(destination, record, journal_identity)
        _remove_journal(destination, record, journal_identity)
    except BaseException as cleanup_error:
        raise RuntimeError(
            f"新安装已生效；备份清理未完成：{cleanup_error}"
        ) from cleanup_error


def _source_package_snapshot() -> dict[str, dict[str, object]]:
    """按适配包布局组合当前发布源的稳定内容契约。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    combined: dict[str, dict[str, object]] = {
        ".": {"type": int(stat.S_IFDIR), "mode": 0}
    }
    for source, prefix in (
        (root / ".codex-plugin", ".codex-plugin"),
        (root / "hooks", "hooks"),
        (root / "skills", "skills"),
    ):
        for relative, record in _safe_tree_snapshot(source).items():
            parts = pathlib.PurePosixPath(relative).parts
            if "__pycache__" in parts or pathlib.PurePosixPath(relative).suffix.casefold() == ".pyc":
                continue
            target = prefix if relative == "." else f"{prefix}/{relative}"
            combined[target] = record
    return combined


def _validate_managed_destination(destination: pathlib.Path) -> dict[str, object]:
    """只允许替换带 ownership marker 或明确历史结构的安全目录。"""
    if _path_is_link_like(destination) or not destination.is_dir():
        raise RuntimeError(f"现有安装目标不是受管插件目录：{destination}")
    initial_snapshot = _safe_tree_snapshot(destination)
    manifest_path = destination / ".codex-plugin" / "plugin.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"现有安装目标缺少 Codex manifest，拒绝覆盖：{destination}")
    manifest = _validate_manifest(manifest_path)
    if manifest.get("name") != PLUGIN_NAME:
        raise RuntimeError(f"现有安装目标不是受管 Codex 插件：{destination}")
    marker = destination / OWNERSHIP_MARKER_NAME
    if _path_exists_without_following(marker):
        final_snapshot = _validate_owned_adapter(destination)
    else:
        actual_digest = _tree_content_digest(initial_snapshot)
        current_digest = _tree_content_digest(_source_package_snapshot())
        version = manifest.get("version")
        historical_digests = (
            LEGACY_PACKAGE_TREE_SHA256.get(version, frozenset())
            if isinstance(version, str)
            else frozenset()
        )
        if actual_digest != current_digest and actual_digest not in historical_digests:
            raise RuntimeError(
                "现有 Codex 安装缺少 ownership marker，且完整内容摘要不符合当前或已知历史发布契约："
                f"{destination}"
            )
        final_snapshot = _safe_tree_snapshot(destination)
    if final_snapshot != initial_snapshot:
        raise RuntimeError(f"现有 Codex 安装目录树在校验期间发生变化：{destination}")
    return {"identity": _path_identity(destination), "tree": final_snapshot}


def _build_adapter(root: pathlib.Path, staging: pathlib.Path) -> None:
    """在空暂存目录内构建完整适配包。"""
    if not _path_exists_without_following(staging):
        staging.mkdir()
    initial_tree = _safe_tree_snapshot(staging)
    if set(initial_tree) != {"."}:
        raise RuntimeError(f"Codex staging 构建前不是空目录：{staging}")
    source_manifest = root / ".codex-plugin" / "plugin.json"
    if not source_manifest.is_file():
        raise FileNotFoundError(f"Codex manifest 不存在：{source_manifest}")

    manifest_directory = staging / ".codex-plugin"
    manifest_directory.mkdir()
    manifest_directory_identity = _path_identity(manifest_directory)
    _copy(source_manifest, manifest_directory / "plugin.json")
    _assert_identity(manifest_directory, manifest_directory_identity, "Codex manifest 目录")
    _copy_tree(root / "hooks", staging / "hooks")
    _copy_tree(root / "skills", staging / "skills")
    unsigned_tree = _safe_tree_snapshot(staging)
    source_tree = _source_package_snapshot()
    if set(unsigned_tree) != set(source_tree) or _tree_content_digest(
        unsigned_tree
    ) != _tree_content_digest(source_tree):
        raise RuntimeError(f"Codex staging 内容不符合当前发布源契约：{staging}")
    _fsync_generated_tree(staging, unsigned_tree)
    _write_ownership_marker(staging, unsigned_tree)
    _validate_owned_adapter(staging)


def main() -> int:
    """复制 Codex manifest、标准 Hook 目录和共享 Skill。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    codex_home = pathlib.Path(
        os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))
    ).expanduser()
    destination = _absolute(pathlib.Path(
        os.environ.get(
            "JOJO_CODEX_PLUGIN_DIR",
            str(codex_home / "plugins" / "jojo-code-guard"),
        )
    ).expanduser())

    if os.path.normcase(str(destination)) == os.path.normcase(str(_absolute(root))):
        _validate_adapter(root)
        print(f"Codex plugin already uses source tree: {destination}")
        return 0

    linked = _first_link_like_ancestor(destination)
    if linked is not None:
        raise RuntimeError(f"拒绝经由链接型安装路径创建目录：{linked}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _recover_transaction(destination)
    parent_snapshot = _capture_ancestor_snapshot(destination.parent)
    destination_identity = (
        _validate_managed_destination(destination)
        if _path_exists_without_following(destination)
        else None
    )
    _require_secure_private_directory_support()
    staging = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.sync-", dir=destination.parent)
    )
    staging_identity = _path_identity(staging)
    empty_staging_tree = _safe_tree_snapshot(staging)
    try:
        _build_adapter(root, staging)
        staging_tree = _validate_owned_adapter(staging)
        _replace_directory(
            staging,
            destination,
            destination_identity,
            parent_snapshot,
            {"identity": staging_identity, "tree": staging_tree},
        )
    finally:
        if (
            _path_exists_without_following(staging)
            and not _path_exists_without_following(_journal_path(destination))
        ):
            if _path_exists_without_following(staging / OWNERSHIP_MARKER_NAME):
                cleanup_tree = _validate_owned_tree(staging)
            else:
                cleanup_tree = _safe_tree_snapshot(staging)
                if cleanup_tree != empty_staging_tree:
                    raise RuntimeError(
                        f"暂存目录缺少 ownership marker，已保留供人工检查：{staging}"
                    )
            _remove_owned_tree(
                staging,
                staging_identity,
                parent_snapshot,
                expected_tree=cleanup_tree,
            )
    print(f"Synced Codex plugin: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

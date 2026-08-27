#!/usr/bin/env python3
"""保存单个工具调用前后的轻量工作区快照。"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterable


STATE_VERSION = 2
STATE_RETENTION_SECONDS = 24 * 60 * 60
MAX_HASH_BYTES = 16 * 1024 * 1024


def _cache_root() -> pathlib.Path:
    """返回业务仓库之外的 Hook 状态目录。"""
    override = (
        os.environ.get("JOJO_CODE_GUARD_STATE_DIR")
        or os.environ.get("PLUGIN_DATA")
        or os.environ.get("CLAUDE_PLUGIN_DATA")
    )
    if override:
        return pathlib.Path(override).expanduser() / "hook-state"
    return pathlib.Path(tempfile.gettempdir()) / "jojo-code-guard" / "hook-state"


def state_directory(
    session_id: str,
    turn_id: str,
    agent_id: str = "",
) -> pathlib.Path | None:
    """为当前会话、回合和代理生成隔离目录。"""
    if not session_id or not turn_id:
        return None
    identity = "\0".join((session_id, turn_id, agent_id))
    digest = hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()
    return _cache_root() / digest


def state_path(
    directory: pathlib.Path | None,
    repo: pathlib.Path,
    tool_use_id: str,
) -> pathlib.Path | None:
    """为不同仓库和并发工具调用生成互不覆盖的状态文件。"""
    if directory is None or not tool_use_id:
        return None
    identity = "\0".join((str(repo), tool_use_id))
    digest = hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()
    return directory / f"{digest}.json"


def state_paths(directory: pathlib.Path | None) -> list[pathlib.Path]:
    """列出当前回合由本工具创建的普通状态文件。"""
    if directory is None or not directory.is_dir():
        return []
    result: list[tuple[int, pathlib.Path]] = []
    for path in directory.glob("*.json"):
        try:
            metadata = path.lstat()
            if stat.S_ISREG(metadata.st_mode):
                result.append((metadata.st_mtime_ns, path))
        except OSError:
            continue
    return [path for _, path in sorted(result, key=lambda item: (item[0], str(item[1])))]


def _safe_repo_path(repo: pathlib.Path, value: str) -> pathlib.Path | None:
    """把 Git 路径限制在当前仓库普通相对路径内。"""
    pure = pathlib.PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts:
        return None
    candidate = repo.joinpath(*pure.parts)
    try:
        candidate.relative_to(repo)
    except ValueError:
        return None
    return candidate


def file_fingerprint(repo: pathlib.Path, value: object) -> dict[str, object] | None:
    """记录诊断路径的类型、权限和内容指纹。"""
    if not isinstance(value, str):
        return None
    target = _safe_repo_path(repo, value)
    if target is None:
        return None
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return {"kind": "missing"}
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        return {"kind": "symlink", "mode": mode, "target": os.readlink(target)}
    if not stat.S_ISREG(metadata.st_mode):
        return {"kind": "other", "mode": mode, "type": stat.S_IFMT(metadata.st_mode)}
    if metadata.st_size > MAX_HASH_BYTES:
        return {
            "kind": "large-file",
            "mode": mode,
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
        }
    digest = hashlib.sha256()
    try:
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return {
        "kind": "file",
        "mode": mode,
        "size": metadata.st_size,
        "sha256": digest.hexdigest(),
    }


def _status_paths(output: bytes) -> list[str]:
    """解析 `git status --porcelain=v1 -z` 中的当前路径。"""
    records = output.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status_text = record[:2].decode("ascii", errors="replace")
        paths.append(record[3:].decode("utf-8", errors="surrogateescape"))
        if "R" in status_text or "C" in status_text:
            index += 1
    return paths


def workspace_snapshot(repo: pathlib.Path) -> dict[str, object]:
    """使用一次 Git 状态查询和必要的文件散列生成稳定快照。"""
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("无法读取工作区状态" + (f"：{detail}" if detail else ""))
    files = {path: file_fingerprint(repo, path) for path in _status_paths(result.stdout)}
    digest = hashlib.sha256(result.stdout)
    digest.update(json.dumps(files, ensure_ascii=True, sort_keys=True).encode("utf-8"))
    return {"fingerprint": digest.hexdigest(), "files": files}


def _cleanup_stale_states(root: pathlib.Path) -> None:
    """只清理本工具目录内过期的普通 JSON 状态。"""
    cutoff = time.time() - STATE_RETENTION_SECONDS
    try:
        entries: Iterable[pathlib.Path] = root.glob("*/*.json")
        for path in entries:
            try:
                metadata = path.lstat()
                if stat.S_ISREG(metadata.st_mode) and metadata.st_mtime < cutoff:
                    path.unlink()
                    try:
                        path.parent.rmdir()
                    except OSError:
                        pass
            except OSError:
                continue
    except OSError:
        return


def write_state(path: pathlib.Path, repo: pathlib.Path, payload: dict[str, object]) -> None:
    """原子写入单个回合的 Hook 状态。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_states(_cache_root())
    value = {"version": STATE_VERSION, "repo": str(repo), **payload}
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_state_record(path: pathlib.Path) -> dict[str, object] | None:
    """读取并验证未过期状态的结构和仓库标识。"""
    try:
        if time.time() - path.stat().st_mtime > STATE_RETENTION_SECONDS:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != STATE_VERSION or not isinstance(payload.get("repo"), str):
        return None
    return payload


def load_state(path: pathlib.Path, repo: pathlib.Path) -> dict[str, object] | None:
    """读取并验证属于当前仓库的状态。"""
    payload = load_state_record(path)
    return payload if payload is not None and payload.get("repo") == str(repo) else None


def remove_state(path: pathlib.Path | None) -> None:
    """删除当前回合状态；不存在时保持幂等。"""
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    try:
        path.parent.rmdir()
    except OSError:
        pass


def remove_states(paths: Iterable[pathlib.Path]) -> None:
    """删除一组工具状态，并在目录为空时清理回合目录。"""
    for path in paths:
        remove_state(path)


def unchanged_preexisting_paths(
    repo: pathlib.Path,
    before: object,
    diagnostics: Iterable[dict[str, object]],
) -> set[str]:
    """返回工具调用前后字节未变化的历史诊断路径。"""
    if not isinstance(before, dict) or not isinstance(before.get("files"), dict):
        return set()
    known = before["files"]
    unchanged: set[str] = set()
    for item in diagnostics:
        path = item.get("path")
        if isinstance(path, str) and path in known and file_fingerprint(repo, path) == known[path]:
            unchanged.add(path)
    return unchanged

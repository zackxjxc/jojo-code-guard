#!/usr/bin/env python3
"""记录每回合守护基线，并把未变化的历史诊断降级为警告。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
from collections.abc import Iterable


BASELINE_VERSION = 1
BASELINE_RETENTION_SECONDS = 7 * 24 * 60 * 60


def _cache_root() -> pathlib.Path:
    """返回不写入业务仓库的临时基线目录。"""
    override = os.environ.get("JOJO_CODE_GUARD_BASELINE_DIR")
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path(tempfile.gettempdir()) / "jojo-code-guard" / "turn-baselines"


def _baseline_path(
    repo: pathlib.Path,
    session_id: str,
    turn_id: str,
) -> pathlib.Path | None:
    """使用会话、回合和仓库生成不泄露路径的缓存键。"""
    if not session_id:
        return None
    identity = "\0".join((session_id, turn_id or "current", str(repo)))
    digest = hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()
    return _cache_root() / f"{digest}.json"


def _safe_diagnostic_path(repo: pathlib.Path, value: object) -> pathlib.Path | None:
    """把诊断中的 Git 路径限制在当前仓库内。"""
    if not isinstance(value, str) or not value:
        return None
    pure = pathlib.PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    candidate = repo.joinpath(*pure.parts)
    try:
        candidate.relative_to(repo)
    except ValueError:
        return None
    return candidate


def _file_fingerprint(repo: pathlib.Path, path: object) -> dict[str, object] | None:
    """记录内容、文件类型和权限，识别本轮对历史问题文件的改动。"""
    target = _safe_diagnostic_path(repo, path)
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


def _diagnostic_key(item: dict[str, object]) -> tuple[object, object, object]:
    """用稳定字段匹配同一诊断，不把级别变化误认为新问题。"""
    return item.get("code"), item.get("path"), item.get("message")


def _read_diagnostics() -> list[dict[str, object]]:
    """严格读取检查器输出的 JSON 诊断数组。"""
    value = json.load(sys.stdin)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("diagnostics must be a JSON array of objects")
    return value


def _cleanup_stale_baselines(root: pathlib.Path) -> None:
    """只清理本工具目录内过期的普通 JSON 文件。"""
    cutoff = time.time() - BASELINE_RETENTION_SECONDS
    try:
        entries: Iterable[pathlib.Path] = root.glob("*.json")
        for path in entries:
            try:
                metadata = path.lstat()
                if stat.S_ISREG(metadata.st_mode) and metadata.st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        return


def _write_baseline(
    path: pathlib.Path,
    repo: pathlib.Path,
    diagnostics: list[dict[str, object]],
) -> None:
    """原子写入当前回合问题集合及文件指纹。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_baselines(path.parent)
    issues = [
        {
            "code": item.get("code"),
            "path": item.get("path"),
            "message": item.get("message"),
            "fingerprint": _file_fingerprint(repo, item.get("path")),
        }
        for item in diagnostics
    ]
    payload = {"version": BASELINE_VERSION, "repo": str(repo), "issues": issues}
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_baseline(path: pathlib.Path, repo: pathlib.Path) -> list[dict[str, object]] | None:
    """读取并验证对应仓库的基线；损坏或过期缓存按不存在处理。"""
    try:
        if time.time() - path.stat().st_mtime > BASELINE_RETENTION_SECONDS:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != BASELINE_VERSION or payload.get("repo") != str(repo):
        return None
    issues = payload.get("issues")
    if not isinstance(issues, list) or not all(isinstance(item, dict) for item in issues):
        return None
    return issues


def record_baseline(repo: pathlib.Path, session_id: str, turn_id: str) -> None:
    """保存 UserPromptSubmit 发生时的完整诊断状态。"""
    diagnostics = _read_diagnostics()
    path = _baseline_path(repo, session_id, turn_id)
    if path is not None:
        _write_baseline(path, repo, diagnostics)


def filter_diagnostics(repo: pathlib.Path, session_id: str, turn_id: str) -> None:
    """把内容和诊断均未变化的历史问题标记为非阻断。"""
    diagnostics = _read_diagnostics()
    path = _baseline_path(repo, session_id, turn_id)
    baseline = _load_baseline(path, repo) if path is not None else None
    if baseline is None:
        json.dump(diagnostics, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    known = {
        _diagnostic_key(item): item.get("fingerprint")
        for item in baseline
    }
    filtered: list[dict[str, object]] = []
    for original in diagnostics:
        item = dict(original)
        key = _diagnostic_key(item)
        fingerprint = _file_fingerprint(repo, item.get("path"))
        if key in known and fingerprint is not None and known[key] == fingerprint:
            if item.get("level") == "BLOCKED":
                item["level"] = "WARNING"
            item["origin"] = "pre_existing"
            item["introduced_by_current_turn"] = False
        filtered.append(item)
    json.dump(filtered, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main(arguments: list[str] | None = None) -> int:
    """执行基线记录或诊断过滤。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("record", "filter"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--turn-id", default="")
    options = parser.parse_args(arguments)
    repo = pathlib.Path(options.repo).resolve()
    try:
        if options.mode == "record":
            record_baseline(repo, options.session_id, options.turn_id)
        else:
            filter_diagnostics(repo, options.session_id, options.turn_id)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        print(f"hook baseline error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Codex/Claude 生命周期 Hook：仅在工作区真实变化时运行完整守护。"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from collections.abc import Iterable


PLUGIN_ROOT = pathlib.Path(
    os.environ.get("PLUGIN_ROOT")
    or os.environ.get("CLAUDE_PLUGIN_ROOT")
    or os.environ.get("CODEX_PLUGIN_ROOT")
    or pathlib.Path(__file__).resolve().parents[1]
).resolve()
SCRIPT_ROOT = PLUGIN_ROOT / "skills" / "jojo-code-guard" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from check_diff import collect_diagnostics  # noqa: E402
from guard_core import find_repo  # noqa: E402
from hook_baseline import (  # noqa: E402
    load_state,
    load_state_record,
    remove_state,
    remove_states,
    state_directory,
    state_path,
    state_paths,
    unchanged_preexisting_paths,
    workspace_snapshot,
    write_state,
)


READ_ONLY_COMMANDS = frozenset(
    {
        "cd",
        "dir",
        "get-childitem",
        "get-content",
        "get-item",
        "get-location",
        "git",
        "head",
        "ls",
        "pwd",
        "resolve-path",
        "rg",
        "select-string",
        "stat",
        "tail",
        "test-path",
        "type",
        "wc",
    }
)
READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"diff", "log", "ls-files", "rev-parse", "show", "status"}
)


def _configure_output() -> None:
    """统一生命周期 JSON 的 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _read_input() -> dict[str, object]:
    """只解析一次 Hook 输入。"""
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError(f"Hook 输入不是有效 JSON：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("Hook 输入顶层必须是对象")
    return value


def _event_payload(event: str, context: str, *, stop: bool = False) -> dict[str, object]:
    """生成精简且不复制工具输出或最终答案的反馈。"""
    if stop:
        return {"decision": "block", "reason": context}
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }


def _diagnostic_dicts(items: Iterable[object]) -> list[dict[str, object]]:
    """把检查器数据类转换为可过滤的普通对象。"""
    return [dict(item.__dict__) for item in items]


def _blocking_context(items: list[dict[str, object]]) -> str:
    """限制模型可见诊断数量和单条长度。"""
    lines = ["jojo-code-guard: 本轮写入引入了阻断项，请最小修复后重新检查："]
    for item in items[:20]:
        level = str(item.get("level", "BLOCKED"))
        code = str(item.get("code", "UNKNOWN"))
        path = str(item.get("path", ""))
        message = str(item.get("message", ""))[:500]
        lines.append(f"- {level} {code} {path}: {message}")
    if len(items) > 20:
        lines.append(f"- 另有 {len(items) - 20} 条阻断项；修复后运行 check-diff 查看完整结果")
    lines.append("不要恢复、覆盖或删除无法可靠归因于本轮的用户内容。")
    return "\n".join(lines)


def _repo_from_input(value: dict[str, object]) -> pathlib.Path | None:
    """非 Git 目录保持静默，由主 Skill 执行目标文件级验证。"""
    cwd = value.get("cwd")
    start = cwd if isinstance(cwd, str) and cwd else "."
    try:
        return find_repo(start)
    except RuntimeError:
        return None


def _confirmed_read_only(value: dict[str, object]) -> bool:
    """仅对白名单中的单一只读 shell 命令跳过所有仓库查询。"""
    tool_name = value.get("tool_name")
    if tool_name not in {"Bash", "PowerShell"}:
        return False
    tool_input = value.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    stripped = command.strip()
    if not stripped or any(token in stripped for token in ("\n", "\r", ";", "|", "&", ">", "<", "`")):
        return False
    parts = stripped.split()
    executable = pathlib.PurePath(parts[0].strip('"\'')).name.casefold()
    if executable not in READ_ONLY_COMMANDS:
        return False
    if executable != "git":
        return True
    index = 1
    while index < len(parts):
        option = parts[index].casefold()
        if option == "-c" and index + 1 < len(parts):
            index += 2
            continue
        if option in {"--no-pager", "-p", "--literal-pathspecs"}:
            index += 1
            continue
        break
    if index >= len(parts) or parts[index].casefold() not in READ_ONLY_GIT_SUBCOMMANDS:
        return False
    return not any(part.startswith("--output") for part in parts[index + 1 :])


def _state_directory(value: dict[str, object]) -> pathlib.Path | None:
    """按会话、回合和代理定位不依赖仓库查询的状态目录。"""
    session_id = value.get("session_id")
    turn_id = value.get("turn_id")
    agent_id = value.get("agent_id")
    return state_directory(
        session_id if isinstance(session_id, str) else "",
        turn_id if isinstance(turn_id, str) else "",
        agent_id if isinstance(agent_id, str) else "",
    )


def _tool_state_path(
    value: dict[str, object],
    directory: pathlib.Path | None,
    repo: pathlib.Path,
) -> pathlib.Path | None:
    """按仓库和工具调用定位状态文件。"""
    tool_use_id = value.get("tool_use_id")
    return state_path(directory, repo, tool_use_id if isinstance(tool_use_id, str) else "")


def _handle_pre(repo: pathlib.Path, path: pathlib.Path | None) -> None:
    """工具执行前只记录一次轻量状态，不运行编码检查。"""
    if path is None:
        return
    write_state(path, repo, {"before": workspace_snapshot(repo), "checked": False})


def _new_blocking(
    repo: pathlib.Path,
    before: object,
    diagnostics: list[dict[str, object]],
) -> list[dict[str, object]]:
    """忽略工具执行前后字节未变化的既有阻断项。"""
    unchanged = unchanged_preexisting_paths(repo, before, diagnostics)
    return [
        item
        for item in diagnostics
        if item.get("level") == "BLOCKED" and item.get("path") not in unchanged
    ]


def _run_full_check(repo: pathlib.Path, before: object) -> list[dict[str, object]]:
    """工作区变化后执行确定性守护并过滤未变化历史问题。"""
    diagnostics = _diagnostic_dicts(collect_diagnostics(repo, skip_clean_check=True))
    return _new_blocking(repo, before, diagnostics)


def _handle_post(
    repo: pathlib.Path,
    path: pathlib.Path | None,
) -> dict[str, object] | None:
    """只有工具真实改变工作区时才运行完整检查。"""
    after = workspace_snapshot(repo)
    state = load_state(path, repo) if path is not None else None
    before = state.get("before") if isinstance(state, dict) else None
    if isinstance(before, dict) and before.get("fingerprint") == after.get("fingerprint"):
        remove_state(path)
        return None
    blocking = _run_full_check(repo, before)
    if blocking:
        if path is not None:
            write_state(path, repo, {"before": before, "checked": False})
        return _event_payload("PostToolUse", _blocking_context(blocking))
    if path is not None:
        write_state(path, repo, {"before": before, "after": after, "checked": True})
    return None


def _handle_stop(
    value: dict[str, object],
    directory: pathlib.Path | None,
) -> dict[str, object] | None:
    """逐仓库兜底本轮尚未通过写后检查或检查后又变化的工作区。"""
    if value.get("stop_hook_active") is True or directory is None:
        return None
    paths = state_paths(directory)
    records = [(path, load_state_record(path)) for path in paths]
    valid = [(path, state) for path, state in records if isinstance(state, dict)]
    if len(valid) != len(paths):
        return _event_payload("Stop", "jojo-code-guard: 本轮 Hook 状态损坏，无法确认写后检查。", stop=True)
    groups: dict[str, list[tuple[pathlib.Path, dict[str, object]]]] = {}
    for path, state in valid:
        repo_text = state.get("repo")
        if isinstance(repo_text, str):
            groups.setdefault(repo_text, []).append((path, state))
    all_blocking: list[dict[str, object]] = []
    for repo_text, group in groups.items():
        repo = pathlib.Path(repo_text).resolve()
        try:
            if find_repo(repo) != repo:
                raise RuntimeError(f"状态仓库根目录已变化：{repo}")
            current = workspace_snapshot(repo)
            all_checked = all(state.get("checked") is True for _, state in group)
            latest = group[-1][1]
            after = latest.get("after")
            if all_checked and isinstance(after, dict):
                if after.get("fingerprint") == current.get("fingerprint"):
                    remove_states(path for path, _ in group)
                    continue
            before = group[0][1].get("before")
            blocking = _run_full_check(repo, before)
        except (OSError, RuntimeError, ValueError) as error:
            blocking = [
                {
                    "level": "BLOCKED",
                    "code": "HOOK_STATE_CHECK_FAILED",
                    "path": str(repo),
                    "message": str(error),
                }
            ]
        if blocking:
            all_blocking.extend(blocking)
        else:
            remove_states(path for path, _ in group)
    if not all_blocking:
        return None
    return _event_payload("Stop", _blocking_context(all_blocking), stop=True)


def main() -> int:
    """按生命周期事件执行轻量快照、完整检查或回合兜底。"""
    _configure_output()
    event: object = None
    payload: dict[str, object] | None = None
    try:
        value = _read_input()
        event = value.get("hook_event_name")
        if not isinstance(event, str):
            raise RuntimeError("Hook 输入缺少 hook_event_name")
        if event in {"PreToolUse", "PostToolUse"} and _confirmed_read_only(value):
            return 0
        directory = _state_directory(value)
        if event == "Stop" and not state_paths(directory):
            return 0
        if event == "PreToolUse":
            repo = _repo_from_input(value)
            if repo is None:
                return 0
            path = _tool_state_path(value, directory, repo)
            _handle_pre(repo, path)
        elif event == "PostToolUse":
            repo = _repo_from_input(value)
            if repo is None:
                return 0
            path = _tool_state_path(value, directory, repo)
            payload = _handle_post(repo, path)
        elif event == "Stop":
            payload = _handle_stop(value, directory)
        else:
            raise RuntimeError(f"不支持的 Hook 事件：{event}")
    except (OSError, RuntimeError, ValueError) as error:
        message = f"jojo-code-guard: 生命周期检查失败：{error}"
        if event == "Stop":
            payload = _event_payload("Stop", message, stop=True)
        elif event == "PostToolUse":
            payload = _event_payload("PostToolUse", message)
        else:
            payload = {"systemMessage": message}
    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

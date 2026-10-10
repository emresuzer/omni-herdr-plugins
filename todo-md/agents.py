"""
Sub-agents of the Claude Code session running in a herdr pane.

Claude Code keeps, per session, `<projects>/<slug>/<session>/subagents/`
with `agent-<id>.meta.json` (agentType, description) and `agent-<id>.jsonl`
(the sub-agent's transcript). The transcript's last entry tells the state:
an assistant turn that ended (`end_turn`) is finished; a tool call or a tool
result means it is still working. Only the file tails are read.
"""

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
TAIL = 64 * 1024
QUIET = 10 * 60          # a working agent silent this long is shown as quiet
KEEP_FINISHED = 6 * 3600  # finished agents older than this are not listed


@dataclass
class SubAgent:
    id: str
    kind: str            # agentType
    job: str             # description given at spawn
    state: str           # working | finished | quiet
    started: float
    updated: float
    activity: str        # what it is doing now / its last words


def _claude_pids(proc_info):
    pids = [p.get("pid") for p in proc_info.get("foreground_processes", [])
            if p.get("pid") and (p.get("name") == "claude" or "claude" in (p.get("cmdline") or ""))]
    shell = proc_info.get("shell_pid")
    if not pids and shell:
        for p in os.listdir("/proc"):
            if not p.isdigit():
                continue
            try:
                with open(f"/proc/{p}/stat") as f:
                    ppid = int(f.read().rsplit(")", 1)[1].split()[1])
                if ppid != shell:
                    continue
                with open(f"/proc/{p}/cmdline", "rb") as f:
                    if b"claude" in f.read():
                        pids.append(int(p))
            except (OSError, ValueError, IndexError):
                pass
    return pids


def session_dir(proc_info):
    """`<projects>/<slug>/<session>` of the Claude Code running in the pane, or None.
    ~/.claude/sessions/<pid>.json names the CURRENT session (rewritten on /clear)."""
    if not proc_info:
        return None
    for pid in _claude_pids(proc_info):
        try:
            with open(os.path.join(CLAUDE_DIR, "sessions", f"{pid}.json")) as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        sid, cwd = info.get("sessionId"), info.get("cwd")
        if sid and cwd:
            slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
            return os.path.join(CLAUDE_DIR, "projects", slug, sid)
    return None


def _last_entry(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL))
            lines = f.read().split(b"\n")
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            return json.loads(raw)
        except ValueError:
            continue
    return None


def _first_time(path):
    try:
        with open(path, "rb") as f:
            first = json.loads(f.readline())
        return _ts(first.get("timestamp"))
    except (OSError, ValueError):
        return None


def _ts(s):
    """Transcript timestamps are ISO 8601 in UTC, e.g. 2026-10-10T08:33:12.345Z."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None
    except ValueError:
        return None


def _describe_tool(block):
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    detail = (inp.get("description") or inp.get("command") or inp.get("file_path")
              or inp.get("pattern") or inp.get("url") or inp.get("query") or inp.get("prompt") or "")
    detail = re.sub(r"\s+", " ", str(detail)).strip()
    if detail.startswith(HOME):
        detail = "~" + detail[len(HOME):]
    return f"{name}: {detail}" if detail else name


def _state(entry):
    """(state, activity) from the transcript's last entry."""
    if not entry:
        return "working", "starting"
    msg = entry.get("message") or {}
    content = msg.get("content")
    if entry.get("type") == "assistant" and isinstance(content, list):
        tools = [b for b in content if b.get("type") == "tool_use"]
        if tools:
            return "working", _describe_tool(tools[-1])
        text = " ".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if msg.get("stop_reason") == "end_turn":
            return "finished", re.sub(r"\s+", " ", text)
        return "working", re.sub(r"\s+", " ", text) or "thinking"
    return "working", "thinking"


def list_agents(session, now=None):
    """Sub-agents of a session: working ones first (newest first), then finished."""
    now = now or time.time()
    sub = os.path.join(session, "subagents") if session else None
    if not sub or not os.path.isdir(sub):
        return []
    out = []
    for name in os.listdir(sub):
        if not name.endswith(".meta.json"):
            continue
        aid = name[:-len(".meta.json")]
        log = os.path.join(sub, aid + ".jsonl")
        try:
            updated = os.path.getmtime(log)
        except OSError:
            continue
        if now - updated > KEEP_FINISHED:
            continue
        try:
            with open(os.path.join(sub, name)) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        state, activity = _state(_last_entry(log))
        if state == "working" and now - updated > QUIET:
            state = "quiet"
        started = _first_time(log) or os.path.getmtime(os.path.join(sub, name))
        out.append(SubAgent(aid.removeprefix("agent-"), meta.get("agentType") or "agent",
                            meta.get("description") or "(no description)", state,
                            started, updated, activity))
    order = {"working": 0, "quiet": 1, "finished": 2}
    out.sort(key=lambda a: (order[a.state], -(a.started if a.state != "finished" else a.updated)))
    return out

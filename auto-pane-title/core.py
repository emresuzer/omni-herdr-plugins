#!/usr/bin/env python3
"""
Core title resolution and Herdr API communication logic for auto-pane-title plugin.
Automatically synchronizes Herdr pane and tab titles to agent session names (/rename)
or the current folder path.
"""

import os
import re
import sys
import glob
import json
import time
import socket
import subprocess

PID_FILE = "/tmp/herdr-auto-pane-title.pid"
KNOWN_AGENTS = {
    "claude", "pi", "codex", "gemini", "agy", "cursor", "devin",
    "cline", "opencode", "copilot", "kimi", "droid", "amp", "hermes"
}
SPINNER_CHARS = {
    "✳", "◐", "◓", "◑", "✓", "▲", "▼", "⠋", "⠙", "⠹", "⠸", "⠼", "⠴",
    "⠦", "⠧", "⠇", "⠏", "●", "◆", "■", "⚙", "★", "☆"
}
GENERIC_TITLES = {
    "bash", "zsh", "sh", "fish", "tmux", "screen",
    "claude", "claude code", "agy", "gemini", "node", "python", "python3"
}


def find_herdr_socket():
    """Locate the Herdr API socket."""
    sock = os.environ.get("HERDR_SOCKET_PATH")
    if sock and os.path.exists(sock):
        return sock

    # Check session directory
    candidates = glob.glob(os.path.expanduser("~/.config/herdr/sessions/*/herdr.sock"))
    if candidates:
        return candidates[0]

    return None


def api_call(method, params=None, sock_path=None, timeout=3.0):
    """Execute a single JSON-RPC call to Herdr."""
    if not sock_path:
        sock_path = find_herdr_socket()
    if not sock_path or not os.path.exists(sock_path):
        return None

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock_path)
        req = {
            "id": f"apt-{int(time.time()*1000)}",
            "method": method,
            "params": params or {}
        }
        s.sendall(json.dumps(req).encode() + b"\n")
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in chunk:
                break
        s.close()
        return json.loads(data.decode())
    except Exception:
        return None


def get_folder_name(cwd):
    """Return the folder name or ~ for home directory."""
    if not cwd:
        return "~"
    norm = os.path.normpath(cwd)
    home = os.path.expanduser("~")
    if norm == home:
        return "~"
    base = os.path.basename(norm)
    return base if base else "/"


def clean_terminal_title(title):
    """Strip status spinners/glyphs and whitespace from terminal titles."""
    if not title:
        return None
    t = title.strip()
    changed = True
    while changed and t:
        changed = False
        for ch in SPINNER_CHARS:
            if t.startswith(ch):
                t = t[len(ch):].strip()
                changed = True
    return t if t else None


def get_claude_pids(proc_info):
    """Find all Claude PIDs related to this pane."""
    if not proc_info:
        return []
    pids = set()

    # 1. From foreground processes
    for proc in proc_info.get("foreground_processes", []):
        name = proc.get("name", "")
        cmdline = proc.get("cmdline", "")
        if name == "claude" or "claude" in cmdline:
            pid = proc.get("pid")
            if pid:
                pids.add(pid)

    # 2. From shell_pid child tree if foreground list didn't identify it
    shell_pid = proc_info.get("shell_pid")
    if shell_pid and not pids:
        try:
            for p in os.listdir("/proc"):
                if p.isdigit():
                    try:
                        with open(f"/proc/{p}/cmdline", "rb") as f:
                            cmd = f.read().replace(b"\x00", b" ").decode(errors="ignore")
                        if "claude" in cmd:
                            with open(f"/proc/{p}/stat", "r") as f:
                                parts = f.read().split()
                                ppid = int(parts[3])
                            if ppid == shell_pid:
                                pids.add(int(p))
                    except Exception:
                        pass
        except Exception:
            pass

    return list(pids)


def _title_from_session_files(proj_slug, sess_id):
    """Read the /rename title (or AI title) for a session from its project files."""
    # 1. custom-title.json (explicitly set via /rename)
    ct_file = os.path.expanduser(f"~/.claude/projects/{proj_slug}/{sess_id}/custom-title.json")
    if os.path.isfile(ct_file):
        try:
            with open(ct_file, "r", encoding="utf-8") as f:
                custom_title = json.load(f).get("customTitle")
            if custom_title and custom_title.strip():
                return custom_title.strip()
        except Exception:
            pass

    # 2. Scan tail of jsonl file
    jsonl_file = os.path.expanduser(f"~/.claude/projects/{proj_slug}/{sess_id}.jsonl")
    if not os.path.isfile(jsonl_file):
        return None
    try:
        with open(jsonl_file, "r", encoding="utf-8", errors="ignore") as jf:
            # Read last ~32KB of file
            jf.seek(0, os.SEEK_END)
            fsize = jf.tell()
            jf.seek(max(0, fsize - 32768))
            lines = jf.readlines()
    except Exception:
        return None

    # First scan for customTitle (set by /rename), then fall back to aiTitle
    for kind, key in (("custom-title", "customTitle"), ("ai-title", "aiTitle")):
        for line in reversed(lines):
            if key not in line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == kind and data.get(key):
                    return data[key].strip()
            except Exception:
                pass
    return None


def _project_slug(cwd):
    """Claude Code's project directory name for a cwd."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def get_claude_session_name_from_proc(proc_info):
    """Resolve the current session name of the Claude Code process in this pane."""
    pids = get_claude_pids(proc_info)
    if not pids:
        return None

    for pid in pids:
        # 1. ~/.claude/sessions/<pid>.json is rewritten by Claude Code on /clear,
        #    /resume and /rename, so it always names the CURRENT session.
        sess_file = os.path.expanduser(f"~/.claude/sessions/{pid}.json")
        if os.path.isfile(sess_file):
            try:
                with open(sess_file, "r", encoding="utf-8") as f:
                    info = json.load(f)
                # "derived" names are placeholder slugs (folder + hash); prefer the AI title.
                name = (info.get("name") or "").strip()
                if name and info.get("nameSource") != "derived":
                    return name
                sess_id, cwd = info.get("sessionId"), info.get("cwd")
                if sess_id and cwd:
                    title = _title_from_session_files(_project_slug(cwd), sess_id)
                    if title:
                        return title
                    continue  # current session has no title yet; don't use stale fds
            except Exception:
                pass

        # 2. Fallback: the open /tmp/claude-<uid>/<project-slug>/<session-id>/tasks fd.
        #    Stale after /clear (the old session's handle stays open), hence last resort.
        fd_dir = f"/proc/{pid}/fd"
        if not os.path.isdir(fd_dir):
            continue
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(os.path.join(fd_dir, fd))
                except OSError:
                    continue
                if "/tmp/claude-" in target and "/tasks" in target:
                    parts = target.split("/")
                    if len(parts) >= 4:
                        title = _title_from_session_files(parts[-3], parts[-2])
                        if title:
                            return title
        except Exception:
            pass

    return None


def resolve_pane_title(pane, proc_info=None):
    """
    Resolve the title for a pane:
    1. Agent session name (from /rename or session title).
    2. Otherwise, the folder it is in.
    """
    agent = pane.get("agent")
    agent_status = pane.get("agent_status", "unknown")
    cwd = pane.get("foreground_cwd") or pane.get("cwd") or ""
    folder = get_folder_name(cwd)

    # 1. Try finding Claude session name from process files (/rename custom-title.json)
    if proc_info:
        claude_title = get_claude_session_name_from_proc(proc_info)
        if claude_title:
            return claude_title

    # 2. Check terminal_title if an agent is active or reported
    is_agent = (agent in KNOWN_AGENTS) or (agent_status not in ("unknown", None))
    if not is_agent and proc_info:
        for p in proc_info.get("foreground_processes", []):
            if p.get("name") in KNOWN_AGENTS or any(a in p.get("cmdline", "") for a in KNOWN_AGENTS):
                is_agent = True
                break

    if is_agent:
        raw_tt = pane.get("terminal_title_stripped") or pane.get("terminal_title") or ""
        clean_tt = clean_terminal_title(raw_tt)
        if clean_tt and clean_tt.lower() not in GENERIC_TITLES:
            return clean_tt

    # 3. Otherwise: folder it is in
    return folder


def sync_all_panes(sock_path=None):
    """Fetch all panes and tabs, and update their titles if they differ."""
    if not sock_path:
        sock_path = find_herdr_socket()
    if not sock_path or not os.path.exists(sock_path):
        return

    resp = api_call("pane.list", sock_path=sock_path)
    if not resp or "result" not in resp or "panes" not in resp["result"]:
        return

    panes = resp["result"]["panes"]

    # Fetch tab list to synchronize tab titles (tab bar and sidebar)
    t_resp = api_call("tab.list", sock_path=sock_path)
    tabs = t_resp.get("result", {}).get("tabs", []) if t_resp else []
    tabs_by_id = {t["tab_id"]: t for t in tabs}

    panes_by_tab = {}

    for pane in panes:
        pane_id = pane.get("pane_id")
        if not pane_id:
            continue

        # Get process info for the pane to detect Claude session
        proc_info = None
        try:
            p_resp = api_call("pane.process_info", {"pane_id": pane_id}, sock_path=sock_path)
            if p_resp and "result" in p_resp and "process_info" in p_resp["result"]:
                proc_info = p_resp["result"]["process_info"]
        except Exception:
            pass

        target_title = resolve_pane_title(pane, proc_info)
        current_label = pane.get("label")
        current_title = pane.get("title")

        # Update pane label if different
        if current_label != target_title:
            api_call("pane.rename", {"pane_id": pane_id, "label": target_title}, sock_path=sock_path)

        # Update pane metadata title if different
        if current_title != target_title:
            api_call("pane.report_metadata", {
                "pane_id": pane_id,
                "source": "auto-pane-title",
                "title": target_title
            }, sock_path=sock_path)

        tab_id = pane.get("tab_id")
        if tab_id:
            panes_by_tab.setdefault(tab_id, []).append((pane, target_title))

    # Synchronize tab titles (visible on the tab bar and sidebar)
    for tab_id, tab_panes in panes_by_tab.items():
        tab = tabs_by_id.get(tab_id)
        if not tab:
            continue

        # If a pane in this tab is focused, use its title; otherwise use the first pane's title
        chosen_title = None
        for p, t_title in tab_panes:
            if p.get("focused"):
                chosen_title = t_title
                break
        if not chosen_title and tab_panes:
            chosen_title = tab_panes[0][1]

        if chosen_title and tab.get("label") != chosen_title:
            api_call("tab.rename", {"tab_id": tab_id, "label": chosen_title}, sock_path=sock_path)


def is_daemon_running():
    """Check if the background daemon is currently active."""
    if not os.path.isfile(PID_FILE):
        return False
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        # Check process existence
        os.kill(pid, 0)
        # Verify it's actually daemon.py
        cmdline_path = f"/proc/{pid}/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path, "rb") as cf:
                cmd = cf.read().replace(b"\x00", b" ").decode(errors="ignore")
                if "daemon.py" in cmd:
                    return True
        return False
    except (OSError, ValueError):
        return False


def ensure_daemon_running():
    """Spawn background daemon if not already running."""
    if is_daemon_running():
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    daemon_script = os.path.join(script_dir, "daemon.py")
    if not os.path.isfile(daemon_script):
        return

    try:
        subprocess.Popen(
            [sys.executable, daemon_script],
            cwd=script_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True
        )
    except Exception:
        pass

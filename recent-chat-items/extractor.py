#!/usr/bin/env python3
"""
Item extractor for recent-chat-items Herdr plugin.
Extracts the last 10 files/directories and URLs from the active AI chat.
Resolves relative paths provided by agents (e.g. docs/game-overview-for-friends.md)
to exact, verified absolute canonical locations on disk with high performance.
"""

import os
import re
import json
import glob
import time
import socket
from collections import defaultdict

KNOWN_EXTENSIONS = {
    "py", "rs", "go", "js", "ts", "jsx", "tsx", "html", "css", "scss",
    "json", "toml", "yaml", "yml", "md", "txt", "sh", "bash", "zsh",
    "c", "cpp", "h", "hpp", "lua", "java", "kt", "rb", "php", "swift",
    "sql", "csv", "tsv", "xml", "svg", "png", "jpg", "jpeg", "gif", "webp",
    "pdf", "docx", "xlsx", "pptx", "env", "conf", "ini", "dockerfile"
}

IGNORED_SUBSTRINGS = [
    "/tasks/", "/proc/", "/dev/", "/sys/", "/run/",
    "node_modules/", ".git/", "__pycache__", ".venv/", "target/debug/",
    "target/release/", ".cache/"
]

SYSTEM_ROOTS = {
    "/", "/tmp", "/home", os.path.expanduser("~"), "/etc", "/usr", "/var", "/opt", "/root", "/media", "/mnt"
}

# Cache for project file indices to avoid repeated os.walk: root_dir -> { filename: [matching_paths] }
_PROJECT_INDEX_CACHE = {}


def find_herdr_socket():
    """Locate the Herdr API socket."""
    sock = os.environ.get("HERDR_SOCKET_PATH")
    if sock and os.path.exists(sock):
        return sock

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
            "id": f"rci-{int(time.time()*1000)}",
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


def find_git_root(start_dir):
    """Find the root of the git repository containing start_dir, if any."""
    if not start_dir or not os.path.exists(start_dir):
        return None
    cur = os.path.abspath(start_dir)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur or parent in SYSTEM_ROOTS:
            break
        cur = parent
    return None


def get_project_index(root_dir, max_depth=4):
    """Build or retrieve a fast filename lookup index for a project root directory."""
    if not root_dir or not os.path.isdir(root_dir) or root_dir in SYSTEM_ROOTS:
        return {}

    real_root = os.path.realpath(root_dir)
    if real_root in _PROJECT_INDEX_CACHE:
        return _PROJECT_INDEX_CACHE[real_root]

    index = defaultdict(list)
    root_depth = real_root.rstrip(os.sep).count(os.sep)
    try:
        for dirpath, dirnames, filenames in os.walk(real_root):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in (
                    "node_modules", "target", "dist", "build", "__pycache__", ".venv", ".git"
                )
            ]
            cur_depth = dirpath.count(os.sep) - root_depth
            if cur_depth > max_depth:
                dirnames.clear()
                continue
            for f in filenames:
                index[f].append(os.path.join(dirpath, f))
    except Exception:
        pass

    _PROJECT_INDEX_CACHE[real_root] = index
    return index


def find_in_project(candidate, root_dirs, max_depth=4):
    """
    Search for a candidate file path inside project directories using cached indices.
    Handles relative paths like 'docs/game-overview-for-friends.md' or 'camera.lua'.
    Returns the canonical absolute path if found, or None.
    """
    if not candidate or not root_dirs:
        return None

    cand_clean = candidate.strip(" /")
    fname = os.path.basename(cand_clean)
    if not fname:
        return None

    for root_dir in root_dirs:
        if not root_dir or not os.path.isdir(root_dir) or root_dir in SYSTEM_ROOTS:
            continue

        # 1. Direct join check (O(1))
        direct = os.path.normpath(os.path.join(root_dir, cand_clean))
        if os.path.exists(direct) and (os.path.isfile(direct) or os.path.isdir(direct)):
            return os.path.realpath(direct)

        # 2. Check cached project index
        idx = get_project_index(root_dir, max_depth=max_depth)
        if fname in idx:
            matches = idx[fname]
            for m in matches:
                if cand_clean in m or m.endswith(cand_clean):
                    return os.path.realpath(m)
            return os.path.realpath(matches[0])

    return None


def get_active_pane_info(sock_path=None):
    """
    Determine the active/focused pane ID, cwd, workspace ID, and pane label.
    Prioritizes the exact pane and workspace where the user triggered the popup.
    """
    # 1. Check environment variables
    ctx_raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if ctx_raw:
        try:
            ctx = json.loads(ctx_raw)
            pane_id = ctx.get("focused_pane_id")
            cwd = ctx.get("focused_pane_cwd") or ctx.get("workspace_cwd")
            label = ctx.get("tab_label") or ctx.get("workspace_label")
            if pane_id:
                if not cwd or cwd == os.path.expanduser("~"):
                    p_info = api_call("pane.get", {"pane_id": pane_id}, sock_path=sock_path)
                    if p_info and "result" in p_info and "pane" in p_info["result"]:
                        p = p_info["result"]["pane"]
                        cwd = p.get("foreground_cwd") or p.get("cwd") or cwd
                        label = label or p.get("label") or p.get("title")
                return pane_id, cwd or os.path.expanduser("~")
        except Exception:
            pass

    pane_id = os.environ.get("HERDR_ACTIVE_PANE_ID") or os.environ.get("HERDR_PANE_ID")
    cwd = os.environ.get("HERDR_ACTIVE_PANE_CWD")
    if pane_id:
        if not cwd or cwd == os.path.expanduser("~"):
            p_info = api_call("pane.get", {"pane_id": pane_id}, sock_path=sock_path)
            if p_info and "result" in p_info and "pane" in p_info["result"]:
                p = p_info["result"]["pane"]
                cwd = p.get("foreground_cwd") or p.get("cwd") or cwd
        return pane_id, cwd or os.path.expanduser("~")

    # 2. Query workspace.list for the currently focused workspace and active tab
    w_resp = api_call("workspace.list", sock_path=sock_path)
    focused_ws = None
    active_tab = None
    if w_resp and "result" in w_resp and "workspaces" in w_resp["result"]:
        for w in w_resp["result"]["workspaces"]:
            if w.get("focused"):
                focused_ws = w.get("workspace_id")
                active_tab = w.get("active_tab_id")
                break

    # 3. Query pane.list to find the active pane in the focused workspace
    p_resp = api_call("pane.list", sock_path=sock_path)
    if p_resp and "result" in p_resp and "panes" in p_resp["result"]:
        panes = p_resp["result"]["panes"]
        if focused_ws:
            for p in panes:
                if p.get("workspace_id") == focused_ws:
                    if active_tab and p.get("tab_id") == active_tab:
                        return p.get("pane_id"), (p.get("foreground_cwd") or p.get("cwd") or os.path.expanduser("~"))
                    if p.get("focused"):
                        return p.get("pane_id"), (p.get("foreground_cwd") or p.get("cwd") or os.path.expanduser("~"))
            for p in panes:
                if p.get("workspace_id") == focused_ws:
                    return p.get("pane_id"), (p.get("foreground_cwd") or p.get("cwd") or os.path.expanduser("~"))

        cur_resp = api_call("pane.current", sock_path=sock_path)
        if cur_resp and "result" in cur_resp and "pane" in cur_resp["result"]:
            p = cur_resp["result"]["pane"]
            return p.get("pane_id"), (p.get("foreground_cwd") or p.get("cwd") or os.path.expanduser("~"))

        for p in panes:
            if p.get("focused"):
                return p.get("pane_id"), (p.get("foreground_cwd") or p.get("cwd") or os.path.expanduser("~"))

    return None, os.path.expanduser("~")


def get_claude_jsonl(proc_info, cwd=None):
    """Find the Claude Code session jsonl file if running in this pane or directory."""
    if proc_info:
        pids = []
        for proc in proc_info.get("foreground_processes", []):
            name = proc.get("name", "")
            cmdline = proc.get("cmdline", "")
            if name == "claude" or "claude" in cmdline:
                pid = proc.get("pid")
                if pid:
                    pids.append(pid)

        shell_pid = proc_info.get("shell_pid")
        if shell_pid and not pids:
            try:
                for p in os.listdir("/proc"):
                    if p.isdigit():
                        try:
                            with open(f"/proc/{p}/stat", "r") as f:
                                ppid = int(f.read().split()[3])
                            if ppid == shell_pid:
                                with open(f"/proc/{p}/cmdline", "rb") as cf:
                                    cmd = cf.read().replace(b"\x00", b" ").decode(errors="ignore")
                                if "claude" in cmd:
                                    pids.append(int(p))
                        except Exception:
                            pass
            except Exception:
                pass

        # ~/.claude/sessions/<pid>.json is rewritten by Claude Code on /clear and
        # /resume, so it always names the CURRENT session.
        for pid in pids:
            try:
                with open(os.path.expanduser(f"~/.claude/sessions/{pid}.json"), "r", encoding="utf-8") as f:
                    info = json.load(f)
                sess_id, sess_cwd = info.get("sessionId"), info.get("cwd")
                if sess_id and sess_cwd:
                    slug = re.sub(r"[^A-Za-z0-9]", "-", sess_cwd)
                    jsonl = os.path.expanduser(f"~/.claude/projects/{slug}/{sess_id}.jsonl")
                    if os.path.isfile(jsonl):
                        return jsonl
            except (OSError, ValueError):
                pass

        # Fallback: the open /tmp/claude-<uid>/<project-slug>/<session-id>/tasks fd.
        # Stale after /clear (the old session's handle stays open), hence last resort.
        for pid in pids:
            fd_dir = f"/proc/{pid}/fd"
            if not os.path.isdir(fd_dir):
                continue
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(os.path.join(fd_dir, fd))
                        if "/tmp/claude-" in target and "/tasks" in target:
                            parts = target.split("/")
                            if len(parts) >= 4:
                                sess_id = parts[-2]
                                proj_slug = parts[-3]
                                jsonl = os.path.expanduser(f"~/.claude/projects/{proj_slug}/{sess_id}.jsonl")
                                if os.path.isfile(jsonl):
                                    return jsonl
                    except Exception:
                        continue
            except Exception:
                pass

    if cwd and cwd not in SYSTEM_ROOTS:
        slug = cwd.replace("/", "-")
        proj_dir = os.path.expanduser(f"~/.claude/projects/{slug}")
        if os.path.isdir(proj_dir):
            matches = glob.glob(os.path.join(proj_dir, "*.jsonl"))
            if matches:
                matches.sort(key=os.path.getmtime, reverse=True)
                return matches[0]

    return None


def get_recent_chat_items(pane_id=None, cwd=None, max_files=10, max_urls=10, scan_lines=None):
    """
    Extract the most recent files/directories and URLs from the specified pane.
    Only checks the last X lines (scan_lines, default 150) and recent transcript tail.
    Returns (files_list, urls_list) where every item in files_list is a verified
    canonical absolute path on disk.
    """
    if scan_lines is None:
        try:
            scan_lines = int(os.environ.get("HERDR_RECENT_SCAN_LINES", "150"))
        except Exception:
            scan_lines = 150

    sock_path = find_herdr_socket()
    if not pane_id or not cwd:
        det_id, det_cwd = get_active_pane_info(sock_path=sock_path)
        pane_id = pane_id or det_id
        cwd = cwd or det_cwd

    if not pane_id:
        return [], []

    # Query pane details and process info
    pane_info = None
    proc_info = None
    try:
        p_resp = api_call("pane.get", {"pane_id": pane_id}, sock_path=sock_path)
        if p_resp and "result" in p_resp and "pane" in p_resp["result"]:
            pane_info = p_resp["result"]["pane"]
    except Exception:
        pass

    try:
        pr_resp = api_call("pane.process_info", {"pane_id": pane_id}, sock_path=sock_path)
        if pr_resp and "result" in pr_resp:
            proc_info = pr_resp["result"].get("process_info")
    except Exception:
        pass

    # Gather candidate root directories
    candidate_roots = []

    def add_root(r):
        if r and os.path.isdir(r) and r not in SYSTEM_ROOTS:
            real_r = os.path.realpath(r)
            if real_r not in candidate_roots:
                candidate_roots.append(real_r)
            gr = find_git_root(real_r)
            if gr and gr not in candidate_roots and gr not in SYSTEM_ROOTS:
                candidate_roots.append(gr)

    if proc_info and proc_info.get("cwd"):
        add_root(proc_info.get("cwd"))
    if pane_info:
        add_root(pane_info.get("foreground_cwd"))
        add_root(pane_info.get("cwd"))
    if cwd:
        add_root(cwd)

    primary_cwd = candidate_roots[0] if candidate_roots else (cwd or os.path.expanduser("~"))

    # 1. Read terminal buffer (last X lines instead of 1000)
    resp = api_call("pane.read", {
        "pane_id": pane_id,
        "lines": scan_lines,
        "source": "recent_unwrapped",
        "format": "text"
    }, sock_path=sock_path)

    text = ""
    if resp and "result" in resp and "read" in resp["result"]:
        text = resp["result"]["read"].get("text", "")

    # 2. Check for Claude Code jsonl transcript (tail 256KB; one turn with tool output can exceed 64KB)
    jsonl_lines = []
    jsonl_path = get_claude_jsonl(proc_info, cwd=primary_cwd)
    if jsonl_path:
        try:
            with open(jsonl_path, "rb") as f:
                f.seek(0, 2)
                fsize = f.tell()
                f.seek(max(0, fsize - 262144))
                raw = f.read().decode("utf-8", errors="ignore")
                jsonl_lines = [l for l in raw.splitlines() if l.strip()]
        except Exception:
            pass

    files = []
    urls = []
    seen_files = set()
    seen_urls = set()
    scratchpad_dirs = []
    resolved_cache = {}

    strip_chars = " \t\r\n()[]{}<>.,:;'\"`\\"

    def add_file(candidate, item_cwd=None):
        if not candidate or not isinstance(candidate, str):
            return

        candidate = candidate.strip(strip_chars)
        if candidate.startswith("file://"):
            candidate = candidate[7:]

        # Strip function/tool wrappers like Write(path), Read(path)
        m_tool = re.match(r"^[A-Za-z0-9_]+\((.+)\)$", candidate)
        if m_tool:
            candidate = m_tool.group(1).strip(strip_chars)

        # Remove line numbers or column info like foo.py:123:4
        candidate = re.sub(r":\d+(?::\d+)?$", "", candidate)
        candidate = candidate.strip(strip_chars)

        # Ignore noise and tasks pipes
        if any(ign in candidate for ign in IGNORED_SUBSTRINGS):
            return
        if not candidate or len(candidate) > 300:
            return

        # Check resolution cache
        cache_key = (candidate, item_cwd)
        if cache_key in resolved_cache:
            resolved_match = resolved_cache[cache_key]
        else:
            roots_to_check = []
            if item_cwd and os.path.isdir(item_cwd) and item_cwd not in SYSTEM_ROOTS:
                r_item = os.path.realpath(item_cwd)
                if r_item not in roots_to_check:
                    roots_to_check.append(r_item)
                gr_item = find_git_root(r_item)
                if gr_item and gr_item not in roots_to_check:
                    roots_to_check.append(gr_item)

            for cr in candidate_roots:
                if cr not in roots_to_check:
                    roots_to_check.append(cr)

            for sp in scratchpad_dirs:
                if sp not in roots_to_check:
                    roots_to_check.append(sp)

            paths_to_try = []
            if os.path.isabs(candidate):
                paths_to_try.append(os.path.realpath(os.path.normpath(candidate)))
            else:
                for b in roots_to_check:
                    paths_to_try.append(os.path.realpath(os.path.normpath(os.path.join(b, candidate))))

            resolved_match = None
            for resolved in paths_to_try:
                if resolved in SYSTEM_ROOTS or resolved == primary_cwd:
                    continue
                if any(ign in resolved for ign in IGNORED_SUBSTRINGS):
                    continue
                if os.path.exists(resolved) and (os.path.isfile(resolved) or os.path.isdir(resolved)):
                    resolved_match = resolved
                    break

            # Fast indexed project lookup if not direct match
            if not resolved_match and not os.path.isabs(candidate):
                cand_clean = candidate.strip(" /")
                fname = os.path.basename(cand_clean)
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                if ext in KNOWN_EXTENSIONS:
                    resolved_match = find_in_project(candidate, roots_to_check)

            resolved_cache[cache_key] = resolved_match

        if resolved_match and resolved_match not in SYSTEM_ROOTS and resolved_match != primary_cwd:
            if resolved_match not in seen_files:
                seen_files.add(resolved_match)
                files.append(resolved_match)
                if "scratchpad" in resolved_match and os.path.isdir(resolved_match) and resolved_match not in scratchpad_dirs:
                    scratchpad_dirs.append(resolved_match)

    def add_url(raw):
        if not raw or not isinstance(raw, str):
            return
        sub_urls = re.split(r"(?=https?://)", raw)
        for u in sub_urls:
            # Strip escaped newlines from raw JSON or shell strings
            u = u.split("\\n")[0].split("\\r")[0]
            u = u.strip(strip_chars)
            u = re.sub(r"(\.(?:json|html|htm|org|com|net|io|dev|git|png|jpg|pdf|svg|txt|md))([A-Z_].*)$", r"\1", u)
            u = u.strip(strip_chars)
            u = re.sub(r"['\"\\`]+$", "", u)
            if (u.startswith("http://") or u.startswith("https://")) and u not in seen_urls:
                try:
                    proto, rest = u.split("://", 1)
                    host = rest.split("/")[0].split(":")[0]
                    if (host and "." in host) or host in ("localhost", "127.0.0.1", "0.0.0.0"):
                        if not any(u.endswith(bad) for bad in ("usage", "failed", "error", "...")):
                            seen_urls.add(u)
                            urls.append(u)
                except Exception:
                    pass

    url_regex = re.compile(r"https?://[^\s<>'\"\]\)]+")
    abs_path_regex = re.compile(r"(?:^|[\s\"'(=])(/[\w\.\-]+(?:/[\w\.\-]+)+/?)")
    tool_call_regex = re.compile(r"(?:Write|Read|Edit|View|NotebookEditCell|Save|Open|Update|Delete|Create)\(([^)]+)\)")

    # 1. Scan JSONL in reverse chronological order with structured tool parsing
    for line in reversed(jsonl_lines):
        if len(files) >= max_files and len(urls) >= max_urls:
            break

        for m in url_regex.finditer(line):
            add_url(m.group())

        for m in abs_path_regex.finditer(line):
            add_file(m.group(1))

        try:
            d = json.loads(line)
            line_cwd = d.get("cwd")
            msg = d.get("message", {})
            if isinstance(msg, dict):
                content = msg.get("content", [])
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict):
                            btype = blk.get("type")
                            if btype == "tool_use":
                                inp = blk.get("input", {})
                                if isinstance(inp, dict):
                                    for k in ("file_path", "filePath", "path", "target_file", "targetFile", "AbsolutePath", "TargetFile"):
                                        v = inp.get(k)
                                        if isinstance(v, str):
                                            add_file(v, item_cwd=line_cwd)
                            elif btype == "text":
                                txt = blk.get("text", "")
                                for m in url_regex.finditer(txt):
                                    add_url(m.group())
                                for m in re.finditer(r"[`'\"]([^\s`'\"]+)[`'\"]", txt):
                                    add_file(m.group(1), item_cwd=line_cwd)
        except Exception:
            pass

    # 2. Scan terminal text lines in reverse chronological order
    t_lines = text.split("\n")
    for line in reversed(t_lines):
        if len(files) >= max_files and len(urls) >= max_urls:
            break

        for m in url_regex.finditer(line):
            add_url(m.group())

        for m in tool_call_regex.finditer(line):
            add_file(m.group(1))

        for m in abs_path_regex.finditer(line):
            add_file(m.group(1))

        for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", line):
            target = m.group(2).strip()
            if target.startswith("http://") or target.startswith("https://"):
                add_url(target)
            else:
                add_file(target)

        for m in re.finditer(r"file://([^\s<>'\"\]\)]+)", line):
            add_file(m.group(1))

        for m in re.finditer(r"[`'\"]([^\s`'\"]+)[`'\"]", line):
            add_file(m.group(1))

        for token in line.split():
            clean = token.strip(strip_chars)
            if "=" in clean:
                clean = clean.split("=", 1)[1].strip(strip_chars)
            if "/" in clean and (clean.startswith("/") or "." in clean or clean.endswith("/")):
                add_file(clean)
            elif "." in clean:
                ext = clean.rsplit(".", 1)[-1].lower()
                if ext in KNOWN_EXTENSIONS:
                    add_file(clean)

    return files[:max_files], urls[:max_urls]


if __name__ == "__main__":
    pane_id, cwd = get_active_pane_info()
    print(f"Active pane: {pane_id}, cwd: {cwd}")
    t0 = time.time()
    files, urls = get_recent_chat_items(pane_id, cwd)
    t1 = time.time()
    print(f"\n--- Recent Files / Folders ({len(files)} found in {(t1-t0)*1000:.2f}ms) ---")
    for i, f in enumerate(files, 1):
        kind = "[DIR ]" if os.path.isdir(f) else "[FILE]"
        print(f"[{i}] {kind} {f}")
    print(f"\n--- Recent URLs ({len(urls)} found) ---")
    for i, u in enumerate(urls, 1):
        print(f"[{i}] {u}")

#!/usr/bin/env python3
"""
Read-only popup for the active project's TODO.md.

Finds TODO.md in the focused pane's cwd or any parent up to the git root,
groups the items by status (running, pending, next phase, done, skipped, each
tagged with its phase), and reloads when the file changes so you can watch the
agent move through it. Keys: j/k ↑/↓ scroll, g/G top/bottom,
PgUp/PgDn page, q/Esc close.
"""

import glob
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import termios
import textwrap
import tty

HOME = os.path.expanduser("~")
TODO_NAME = "TODO.md"

# marker -> (label, ANSI style)
MARKERS = {
    " ": ("pending", ""),
    "~": ("running", "\033[1;33m"),
    "x": ("done", "\033[32m"),
    "X": ("done", "\033[32m"),
    ">": ("next phase", "\033[36m"),
    "-": ("skipped", "\033[2;9m"),
}
STATUS_ORDER = ["running", "pending", "next phase", "done", "skipped"]
ITEM_RE =re.compile(r"^(\s*)(?:[-*+]\s+)?\[([ ~xX>\-])\](.*)$")
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


# --- herdr: find the cwd of the pane the popup was opened from -------------

def api_call(method, params=None):
    sock_path = os.environ.get("HERDR_SOCKET_PATH")
    if not sock_path or not os.path.exists(sock_path):
        found = glob.glob(os.path.join(HOME, ".config/herdr/sessions/*/herdr.sock"))
        sock_path = found[0] if found else None
    if not sock_path:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(sock_path)
            s.sendall(json.dumps({"id": "todo-md", "method": method, "params": params or {}}).encode() + b"\n")
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode()).get("result")
    except (OSError, ValueError):
        return None


def pane_cwd(pane_id):
    res = api_call("pane.get", {"pane_id": pane_id}) or {}
    pane = res.get("pane") or {}
    return pane.get("foreground_cwd") or pane.get("cwd")


def active_cwd():
    """Same precedence as recent-chat-items: plugin context, env, focused workspace."""
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    pane_id = ctx.get("focused_pane_id") or os.environ.get("HERDR_ACTIVE_PANE_ID") or os.environ.get("HERDR_PANE_ID")
    cwd = ctx.get("focused_pane_cwd") or os.environ.get("HERDR_ACTIVE_PANE_CWD")
    if pane_id and (not cwd or cwd == HOME):
        cwd = pane_cwd(pane_id) or cwd
    if cwd:
        return cwd

    ws = api_call("workspace.list") or {}
    focused = next((w for w in ws.get("workspaces", []) if w.get("focused")), None)
    panes = (api_call("pane.list") or {}).get("panes", [])
    if focused:
        mine = [p for p in panes if p.get("workspace_id") == focused.get("workspace_id")]
        pick = (next((p for p in mine if p.get("tab_id") == focused.get("active_tab_id")), None)
                or next((p for p in mine if p.get("focused")), None))
        if pick:
            return pick.get("foreground_cwd") or pick.get("cwd") or HOME
    return os.getcwd()


def find_todo(start):
    """Nearest TODO.md from start upwards, stopping at the git root (or $HOME)."""
    cur = os.path.abspath(start)
    while True:
        path = os.path.join(cur, TODO_NAME)
        if os.path.isfile(path):
            return path
        if os.path.exists(os.path.join(cur, ".git")) or cur in (HOME, "/"):
            return None
        cur = os.path.dirname(cur)


# --- rendering --------------------------------------------------------------

def render(path, start, width):
    """Return (header, body lines) with ANSI styling, wrapped to width."""
    if not path:
        return (f"{BOLD}No {TODO_NAME}{RESET} {DIM}in {shorten(start)} or its parents{RESET}", [])
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        return (f"{BOLD}{shorten(path)}{RESET}  cannot read: {e}", [])

    # Collect items by status, remembering the phase heading each sits under.
    groups = {label: [] for label in STATUS_ORDER}
    phase = ""
    for raw in text.splitlines():
        m = ITEM_RE.match(raw)
        if m:
            _, mark, rest = m.groups()
            groups[MARKERS[mark][0]].append((mark, rest.strip(), phase))
        elif raw.startswith("## "):
            phase = raw[3:].strip()

    # No markers at all: show the file as-is rather than an empty view.
    if not any(groups.values()):
        body = [ln for raw in text.splitlines() for ln in (textwrap.wrap(raw, width) or [""])]
        return (f"{BOLD}{shorten(path)}{RESET}", body)

    body = []
    for label in STATUS_ORDER:
        items = groups[label]
        if not items:
            continue
        if body:
            body.append("")
        body.append(f"{BOLD}{label.upper()} ({len(items)}){RESET}")
        for mark, rest, item_phase in items:
            style = MARKERS[mark][1]
            prefix = f"[{mark}] "
            tag = f"  · {item_phase}" if item_phase else ""
            wrapped = textwrap.wrap(rest + tag, max(10, width - len(prefix))) or [""]
            lines = [prefix + wrapped[0]] + [" " * len(prefix) + w for w in wrapped[1:]]
            if tag:  # dim the phase tag, which wrap left at the end of the last line
                last = lines[-1]
                cut = last.rfind("  · ")
                lines[-1] = last[:cut] + RESET + DIM + last[cut:] if cut >= 0 else last
            body += [f"{style}{ln}{RESET}" for ln in lines]

    summary = "  ".join(f"{len(groups[k])} {k}" for k in STATUS_ORDER if groups[k])
    return (f"{BOLD}{shorten(path)}{RESET}  {DIM}{summary}{RESET}", body)


def shorten(p):
    return "~" + p[len(HOME):] if p.startswith(HOME + "/") or p == HOME else p


# --- input loop ---------------------------------------------------------------

def read_key(fd, timeout):
    if not select.select([fd], [], [], timeout)[0]:
        return None
    ch = os.read(fd, 1)
    if ch != b"\x1b":
        return ch.decode(errors="ignore")
    seq = b""
    while select.select([fd], [], [], 0.02)[0]:
        seq += os.read(fd, 1)
    return {b"[A": "up", b"[B": "down", b"[5~": "pgup", b"[6~": "pgdn",
            b"OA": "up", b"OB": "down"}.get(seq, "esc" if not seq else None)


def run(start):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    out = sys.stdout
    top = 0
    stamp = None
    try:
        tty.setcbreak(fd)
        out.write("\033[?1049h\033[?25l")
        dirty = True
        while True:
            path = find_todo(start)
            try:
                new_stamp = (path, os.stat(path).st_mtime_ns) if path else None
            except OSError:
                new_stamp = None
            cols, rows = shutil.get_terminal_size((80, 24))
            if new_stamp != stamp:
                stamp, dirty = new_stamp, True
            header, body = render(path, start, cols - 2)
            page = max(1, rows - 3)
            top = max(0, min(top, len(body) - page))
            if dirty:
                out.write("\033[H\033[2J " + header + "\r\n\r\n")
                for line in body[top:top + page]:
                    out.write(" " + line + "\r\n")
                more = f"{top + 1}-{min(top + page, len(body))}/{len(body)}  " if len(body) > page else ""
                out.write(f"\033[{rows};1H {DIM}{more}j/k scroll · g/G top/bottom · q close{RESET}")
                out.flush()
                dirty = False

            key = read_key(fd, 1.0)
            if key is None:
                continue
            if key in ("q", "esc", "\x03"):
                return
            before = top
            if key in ("j", "down"):
                top += 1
            elif key in ("k", "up"):
                top -= 1
            elif key in ("pgdn", " ", "\x06"):
                top += page
            elif key in ("pgup", "\x02"):
                top -= page
            elif key == "g":
                top = 0
            elif key == "G":
                top = len(body)
            top = max(0, min(top, len(body) - page))
            dirty = dirty or top != before
    finally:
        out.write("\033[?25h\033[?1049l")
        out.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    if "--open-popup" in sys.argv:
        herdr = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"
        subprocess.run([herdr, "plugin", "pane", "open", "--plugin", "todo-md", "--entrypoint", "viewer"])
        return
    run(active_cwd())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Interactive popup for the active project's TODO.md, driven by the mouse.

Finds TODO.md in the focused pane's cwd or any parent up to the git root and
reloads when it changes. Click an item to get its actions: add a note, move
it up/down/top or delete it; on a done item, ask the agent to re-check or
redo it (sent to the agent pane with `herdr agent prompt`). Phases get `+`
(new item) and `✕` (remove; items move to "General"); projects and the
header get `+ Phase`. Done items are hidden unless "Show done" is on.

Keys only scroll and close: wheel, ↑/↓, PgUp/PgDn, Esc/q (Esc first cancels
what is open). While a text field is open, typing goes into it.
"""

import codecs
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
import time
import tty
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import todo_model as tm  # noqa: E402
import agents as ag  # noqa: E402

HOME = os.path.expanduser("~")
TODO_NAME = "TODO.md"
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "todo-md")


# --- style ------------------------------------------------------------------

def fg(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return f"\033[38;2;{r};{g};{b}m"


def bg(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return f"\033[48;2;{r};{g};{b}m"


C = {  # Tokyo Night–style; only foregrounds and chips, the popup keeps the terminal's background
    "text": "#c0caf5", "sub": "#a9b1d6", "dim": "#565f89", "line": "#3b4261",
    "blue": "#7aa2f7", "purple": "#bb9af7", "cyan": "#7dcfff", "green": "#9ece6a",
    "yellow": "#e0af68", "red": "#f7768e", "ink": "#16161e",
}
RESET, BOLD, ITAL, STRIKE = "\033[0m", "\033[1m", "\033[3m", "\033[9m"
GLYPH = {" ": "○", "~": "◉", "x": "✔", "X": "✔", ">": "»", "-": "✕"}
TINT = {" ": C["sub"], "~": C["yellow"], "x": C["green"], "X": C["green"], ">": C["cyan"], "-": C["dim"]}


def cw(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def width(s):
    return sum(cw(ch) for ch in s)


def wrap(s, w):
    """Word-wrap s to display width w."""
    w = max(4, w)
    out, cur = [], ""
    for word in s.split(" "):
        cand = word if not cur else cur + " " + word
        if width(cand) <= w:
            cur = cand
            continue
        if cur:
            out.append(cur)
        while width(word) > w:  # hard-break a word longer than the line
            cut, n = "", 0
            for ch in word:
                if n + cw(ch) > w:
                    break
                cut += ch
                n += cw(ch)
            out.append(cut)
            word = word[len(cut):]
        cur = word
    out.append(cur)
    return out


def clip(s, w):
    out, n = "", 0
    for ch in s:
        if n + cw(ch) > w:
            return out[:-1] + "…" if out else ""
        out += ch
        n += cw(ch)
    return out


class Line:
    """One screen row: styled segments plus clickable column ranges."""

    def __init__(self):
        self.segs, self.regions, self.col = [], [], 0

    def add(self, s, style=""):
        self.segs.append((s, style))
        self.col += width(s)
        return self

    def chip(self, label, action, tone="plain"):
        back = {"plain": C["line"], "accent": C["blue"], "danger": C["red"]}[tone]
        front = C["text"] if tone == "plain" else C["ink"]
        start = self.col
        self.add(f" {label} ", bg(back) + fg(front) + ("" if tone == "plain" else BOLD))
        self.regions.append((start, self.col, action))
        self.add(" ")
        return self

    def click_all(self, action):
        self.regions.append((0, 10_000, action))
        return self

    def render(self):
        return "".join(f"{st}{s}{RESET}" if st else s for s, st in self.segs)


# --- herdr ------------------------------------------------------------------

def herdr_bin():
    return os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"


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
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode()).get("result")
    except (OSError, ValueError):
        return None


def context():
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    pane_id = ctx.get("focused_pane_id") or os.environ.get("HERDR_ACTIVE_PANE_ID") or os.environ.get("HERDR_PANE_ID")
    cwd = ctx.get("focused_pane_cwd") or os.environ.get("HERDR_ACTIVE_PANE_CWD")
    return pane_id, cwd


def active_cwd():
    pane_id, cwd = context()
    if pane_id and (not cwd or cwd == HOME):
        pane = (api_call("pane.get", {"pane_id": pane_id}) or {}).get("pane") or {}
        cwd = pane.get("foreground_cwd") or pane.get("cwd") or cwd
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


def agent_pane(todo_dir):
    """The agent pane to prompt: the pane alt+t was pressed in when it runs an
    agent, else an agent working inside the TODO's directory."""
    pane_id, _ = context()
    try:
        out = subprocess.run([herdr_bin(), "agent", "list"], capture_output=True, text=True, timeout=5).stdout
        agents = json.loads(out)["result"]["agents"]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None
    if pane_id and any(a.get("pane_id") == pane_id for a in agents):
        return pane_id
    inside = [a for a in agents
              if (a.get("foreground_cwd") or a.get("cwd") or "").startswith(todo_dir)]
    inside.sort(key=lambda a: (not a.get("focused"), a.get("agent_status") == "working"))
    return inside[0]["pane_id"] if inside else None


def find_todo(start):
    cur = os.path.abspath(start)
    while True:
        path = os.path.join(cur, TODO_NAME)
        if os.path.isfile(path):
            return path
        if os.path.exists(os.path.join(cur, ".git")) or cur in (HOME, "/"):
            return None
        cur = os.path.dirname(cur)


def shorten(p):
    return "~" + p[len(HOME):] if p.startswith(HOME + "/") or p == HOME else p


def elapsed(sec):
    m = int(sec // 60)
    if m < 1:
        return "just started"
    if m < 60:
        return f"{m}m"
    h = m // 60
    return f"{h}h {m % 60}m" if h < 24 else f"{h // 24}d {h % 24}h"


# --- the app ----------------------------------------------------------------

class App:
    def __init__(self, start):
        self.start = start
        self.path = find_todo(start)
        self.text = ""
        self.stamp = None
        self.show_done = False
        self.sel = None          # selected item's line
        self.mode = "idle"       # idle | note | redo | confirm-delete | add-item | add-phase | rm-phase
        self.target = None       # phase id / project for add-item, add-phase, rm-phase
        self.buf = ""
        self.top = 0
        self.msg = ""
        self.msg_at = 0.0
        self.quit = False
        self.since = self.load_since()
        self.show_finished = False
        self.session = None      # Claude Code session dir of the pane alt+t came from
        self.agents = []
        self.agents_at = 0.0
        self.session_at = 0.0
        self.reload(force=True)

    # file ---------------------------------------------------------------
    def reload(self, force=False):
        if not self.path:
            self.path = find_todo(self.start)
        try:
            stamp = os.stat(self.path).st_mtime_ns if self.path else None
        except OSError:
            stamp = None
        if not force and stamp == self.stamp:
            return False
        self.stamp = stamp
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                self.text = f.read()
        except (OSError, TypeError):
            self.text = ""
        self.track_running()
        return True

    def write(self, text):
        text = tm.number_all(text)
        tm.write_atomic(self.path, text)
        self.text = text
        self.stamp = os.stat(self.path).st_mtime_ns
        self.track_running()

    def edit_item(self, fn):
        """Re-read, check the selected item is unchanged, apply fn(text, item)."""
        self.reload(force=True)
        it = tm.find_item(self.text, self.sel) if self.sel is not None else None
        if it is None:
            self.say("TODO.md changed on disk — pick the item again")
            self.sel, self.mode = None, "idle"
            return None
        out = fn(self.text, it)
        if out is None:
            return it
        new_text, new_line = out if isinstance(out, tuple) else (out, self.sel)
        self.write(new_text)
        self.sel = new_line
        self.mode = "idle"
        return it

    def say(self, msg):
        self.msg, self.msg_at = msg, time.time()

    # running timer --------------------------------------------------------
    def load_since(self):
        try:
            with open(os.path.join(CACHE, "since.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def track_running(self):
        if not self.path:
            return
        run = next((it for it in tm.items(self.text) if it.marker == "~"), None)
        cur = self.since.get(self.path)
        if run is None:
            if cur is None:
                return
            self.since.pop(self.path, None)
        elif cur is None or cur.get("key") != run.text:
            # First sight of this running item: the file's last write is the
            # latest moment it can have been marked, so count from there.
            try:
                at = os.stat(self.path).st_mtime
            except OSError:
                at = time.time()
            self.since[self.path] = {"key": run.text, "at": at}
        else:
            return
        try:
            os.makedirs(CACHE, exist_ok=True)
            tmp = os.path.join(CACHE, "since.json.tmp")
            with open(tmp, "w") as f:
                json.dump(self.since, f)
            os.replace(tmp, os.path.join(CACHE, "since.json"))
        except OSError:
            pass

    # agent ----------------------------------------------------------------
    def ask_agent(self, it, kind, extra=""):
        where = shorten(self.path) + (f', phase "{it.phase}"' if it.phase else "")
        if kind == "verify":
            prompt = (f'Re-check TODO item {it.label} ({where}). Verify it is really done and '
                      "still works — look at the code/system, run what checks apply, and tell me "
                      "what you checked. If it is not done, set it back to [ ] with a short note.")
        else:
            prompt = (f'Redo TODO item {it.label} ({where}).' + (f" What to change: {extra}." if extra else "")
                      + " I set it back to [ ]; follow the TODO rule while you work on it.")
        pane = agent_pane(os.path.dirname(self.path))
        if not pane:
            self.say("No agent pane found for this project — nothing sent")
            return
        r = subprocess.run([herdr_bin(), "agent", "prompt", pane, prompt],
                           capture_output=True, text=True, timeout=10)
        self.say(f"Sent to agent {pane}" if r.returncode == 0 else f"herdr agent prompt failed: {r.stderr.strip()[:80]}")

    # actions --------------------------------------------------------------
    def act(self, a):
        kind = a[0]
        if kind == "select":
            line = a[1]
            self.sel = None if self.sel == line else line
            self.mode, self.buf, self.target = "idle", "", None
        elif kind == "close":
            self.sel, self.mode, self.buf, self.target = None, "idle", "", None
        elif kind == "mode":
            self.mode, self.buf = a[1], ""
        elif kind == "move":
            self.edit_item(lambda t, it: tm.move_item(t, it, a[1]) or None)
        elif kind == "delete":
            self.edit_item(lambda t, it: (tm.remove_item(t, it), None))
        elif kind == "verify":
            it = tm.find_item(self.text, self.sel)
            if it:
                self.ask_agent(it, "verify")
        elif kind == "toggle-done":
            self.show_done = not self.show_done
            self.say("Showing done items" if self.show_done else "Done items hidden")
        elif kind in ("add-item", "add-phase", "rm-phase"):
            same = self.mode == kind and self.target == a[1]
            self.mode, self.target, self.buf = ("idle", None, "") if same else (kind, a[1], "")
            self.sel = None
        elif kind in ("rm-phase-yes", "rm-phase-all"):
            self.reload(force=True)
            p = tm.find_phase(self.text, a[1])
            wipe = kind == "rm-phase-all"
            out = None if p is None else (tm.delete_phase(self.text, p) if wipe else tm.remove_phase(self.text, p))
            if out is None:
                self.say("Phase changed on disk or can't be removed")
            else:
                self.write(out)
                n = len(p.items)
                self.say(f'Deleted phase "{p.title}" and its {n} item(s)' if wipe and n
                         else f'Removed phase "{p.title}"' + (f" — items moved to {tm.GENERAL}" if n else ""))
            self.mode, self.target = "idle", None
        elif kind == "save":
            self.submit()
        elif kind == "cancel":
            self.mode, self.buf, self.target = "idle", "", None
        elif kind == "toggle-finished":
            self.show_finished = not self.show_finished
        elif kind == "quit":
            self.quit = True

    def submit(self):
        v = self.buf.strip()
        mode, target = self.mode, self.target
        self.buf = ""
        if mode == "note":
            if v:
                self.edit_item(lambda t, it: tm.add_note(t, it, v))
            self.mode = "idle"
        elif mode == "redo":
            today = time.strftime("%Y-%m-%d")

            def reopen(t, it):
                t = tm.set_marker(t, it, " ")
                return tm.add_note(t, tm.find_item(t, it.line), f"reopened {today}" + (f": {v}" if v else ""))
            it = self.edit_item(reopen)
            if it:
                self.ask_agent(it, "redo", v)
        elif mode == "add-item":
            p = tm.find_phase(self.text, target)
            if v and p:
                self.reload(force=True)
                p = tm.find_phase(self.text, target)
                if p:
                    self.write(tm.add_item(self.text, p, v))
                    self.say(f"Added to {p.title or 'the list'}")
            self.mode, self.target = "idle", None
        elif mode == "add-phase":
            if v:
                self.reload(force=True)
                self.write(tm.add_phase(self.text, v, target))
                self.say(f'Added phase "{v}"')
            self.mode, self.target = "idle", None

    # drawing --------------------------------------------------------------
    def bar(self, ln, done, total, cells):
        fill = round(done / total * cells) if total else 0
        pct = round(done / total * 100) if total else 0
        ln.add("━" * fill, fg(C["green"])).add("━" * max(0, cells - fill), fg(C["line"]))
        ln.add(f" {pct:3d}%", fg(C["green"] if total and done == total else C["sub"]))

    def input_line(self, label, w):
        ln = Line().add("   ")
        ln.add(label + " ", fg(C["cyan"]) + BOLD)
        room = max(8, w - width(label) - 24)
        shown = self.buf if width(self.buf) < room else "…" + self.buf[-(room - 2):]
        ln.add(shown, fg(C["text"])).add("▏", fg(C["blue"]))
        ln.add(" " * max(1, room - width(shown)))
        ln.chip("Save", ("save",), "accent").chip("Cancel", ("cancel",))
        return ln

    def item_lines(self, it, w):
        out = []
        is_sel = it.line == self.sel
        lead = "▌ " if is_sel else "  "
        num = f"#{it.num} " if it.num is not None else ""
        textc = C["yellow"] if it.marker == "~" else (C["sub"] if it.marker == "-" else C["text"])
        style = fg(textc) + (BOLD if is_sel or it.marker == "~" else "") + (STRIKE if it.marker == "-" else "")
        body = wrap(it.text, w - 6 - width(num))
        for i, part in enumerate(body):
            ln = Line()
            ln.add(lead if i == 0 else "  ", fg(C["blue"]))
            if i == 0:
                ln.add(GLYPH.get(it.marker, "?") + " ", fg(TINT.get(it.marker, C["sub"])) + BOLD)
                ln.add(num, fg(C["dim"]))
            else:
                ln.add("  " + " " * width(num))
            ln.add(part, style)
            out.append(ln.click_all(("select", it.line)))
        for n in it.notes:
            for j, part in enumerate(wrap(n, w - 10)):
                ln = Line().add("      └ " if j == 0 else "        ", fg(C["dim"]))
                out.append(ln.add(part, fg(C["sub"]) + ITAL).click_all(("select", it.line)))
        if is_sel:
            out.append(Line())
            out += self.actions(it, w)
            out.append(Line())
        return out

    def actions(self, it, w):
        if self.mode == "note":
            return [self.input_line("Note:", w)]
        if self.mode == "redo":
            return [self.input_line("What to redo (optional):", w)]
        if self.mode == "confirm-delete":
            ln = Line().add("    ").add("Delete this item? ", fg(C["red"]) + BOLD)
            return [ln.chip("Delete", ("delete",), "danger").chip("Cancel", ("mode", "idle"))]
        ln = Line().add("    ")
        if it.done:
            ln.chip("Re-check", ("verify",), "accent").chip("Redo", ("mode", "redo"))
        else:
            ln.chip("+ Note", ("mode", "note"), "accent")
            ln.chip("↑", ("move", "up")).chip("↓", ("move", "down")).chip("⤒ Top", ("move", "top"))
        ln.chip("Delete", ("mode", "confirm-delete")).chip("✕", ("close",))
        return [ln]

    def refresh_agents(self):
        now = time.time()
        if now - self.session_at > 15:          # follow /clear and /resume
            self.session_at = now
            override = os.environ.get("TODO_MD_SESSION_DIR")   # tests
            if override:
                self.session = override
            else:
                pane_id, _ = context()
                info = (api_call("pane.process_info", {"pane_id": pane_id}) or {}).get("process_info") if pane_id else None
                self.session = ag.session_dir(info)
        if now - self.agents_at > 2:
            self.agents_at = now
            self.agents = ag.list_agents(self.session) if self.session else []

    def agent_lines(self, w):
        if not self.agents:
            return []
        live = [a for a in self.agents if a.state != "finished"]
        done = [a for a in self.agents if a.state == "finished"]
        now = time.time()
        out = [Line()]
        hd = Line().add(" Agents ", fg(C["purple"]) + BOLD)
        hd.add(f" {len(live)} working" if live else " none working", fg(C["yellow"] if live else C["dim"]))
        if done:
            hd.add(" · ", fg(C["dim"]))
            start = hd.col
            hd.add(("▾ " if self.show_finished else "▸ ") + f"{len(done)} finished", fg(C["dim"]))
            hd.regions.append((start, hd.col, ("toggle-finished",)))
        out.append(hd)
        out.append(Line().add(" " + "─" * (w - 2), fg(C["line"])))
        for a in live + (done if self.show_finished else []):
            dot, col = {"working": ("●", C["yellow"]), "quiet": ("◌", C["sub"]),
                        "finished": ("✔", C["green"])}[a.state]
            age = elapsed((a.updated if a.state == "finished" else now) - a.started)
            meta = f"{a.kind} · {age}" + (f" · quiet {elapsed(now - a.updated)}" if a.state == "quiet" else "")
            ln = Line().add(f"  {dot} ", fg(col) + BOLD)
            ln.add(clip(a.job, max(8, w - width(meta) - 8)), fg(C["text"] if a.state != "finished" else C["sub"]) + BOLD)
            ln.add(" " * max(1, w - ln.col - width(meta) - 1)).add(meta, fg(C["dim"]))
            out.append(ln)
            if a.activity:
                out.append(Line().add("      " + clip(a.activity, w - 8), fg(C["dim"]) + (ITAL if a.state == "finished" else "")))
        return out

    def build(self, w):
        """Return (header lines, body lines)."""
        head = []
        title = Line().add(" ◆ TODO ", fg(C["blue"]) + BOLD)
        title.add(clip(shorten(self.path) if self.path else "", max(0, w - 46)), fg(C["dim"]))
        title.add(" " * max(1, w - title.col - 36))
        title.chip("+ Phase", ("add-phase", None)).chip("Hide done" if self.show_done else "Show done", ("toggle-done",))
        title.chip("✕ Close", ("quit",))
        head.append(title)

        if not self.path:
            head.append(Line().add(f"   No {TODO_NAME} in {shorten(self.start)} or its parents.", fg(C["dim"])))
            return head, self.agent_lines(w)

        phases = tm.parse(self.text)
        all_items = [it for p in phases for it in p.items]
        done = sum(1 for it in all_items if it.done)
        prog = Line().add("   ").add(str(len(all_items) - done), fg(C["text"]) + BOLD).add(" open  ", fg(C["dim"]))
        prog.add(str(done), fg(C["green"]) + BOLD).add(" done  ", fg(C["dim"]))
        self.bar(prog, done, len(all_items), max(6, w - prog.col - 8))
        head.append(prog)

        run = next((it for it in all_items if it.marker == "~"), None)
        now = Line().add("   ")
        if run:
            since = self.since.get(self.path)
            now.add("◉ now ", fg(C["yellow"]) + BOLD)
            tail = f" · {elapsed(time.time() - since['at'])}" if since and since.get("key") == run.text else ""
            now.add(clip(run.label, max(4, w - 12 - width(tail))), fg(C["text"]) + BOLD).add(tail, fg(C["dim"]))
            now.click_all(("select", run.line))
        else:
            now.add("nothing running", fg(C["dim"]))
        head.append(now)
        if self.mode == "add-phase" and self.target is None:
            head.append(self.input_line("New phase:", w))
        head.append(Line())

        body = self.agent_lines(w)
        last_project = None
        for p in phases:
            shown = p.items if self.show_done else [it for it in p.items if not it.done]
            # Done work is hidden entirely: a phase with only done items goes too.
            if not self.show_done and p.items and not shown:
                continue
            if p.project != last_project:
                last_project = p.project
                if p.project and p.project.upper() != "TODO":
                    if body:
                        body.append(Line())
                    ph = Line().add(" " + clip(p.project, w - 16), fg(C["purple"]) + BOLD)
                    ph.add(" " * max(1, w - ph.col - 12)).chip("+ Phase", ("add-phase", p.project))
                    body.append(ph)
                    body.append(Line().add(" " + "─" * (w - 2), fg(C["line"])))
                    if self.mode == "add-phase" and self.target == p.project:
                        body.append(self.input_line("New phase:", w))
            body.append(Line())
            hd = Line().add("  ")
            hd.add(clip(p.title or "(no phase)", w - 24), fg(C["cyan"]) + BOLD)
            hd.add(f"  {p.done_count}/{len(p.items)}", fg(C["dim"]))
            hd.add(" " * max(1, w - hd.col - 11))
            hd.chip("+", ("add-item", p.id))
            if p.line >= 0:
                hd.chip("✕", ("rm-phase", p.id))
            body.append(hd)
            if p.items:
                b = Line().add("  ")
                self.bar(b, p.done_count, len(p.items), max(6, w - 12))
                body.append(b)
            if self.mode == "rm-phase" and self.target == p.id:
                ask = Line().add("    ")
                n = len(p.items)
                if not n:
                    ask.add("Remove this empty phase? ", fg(C["red"]) + BOLD)
                    ask.chip("Remove", ("rm-phase-yes", p.id), "danger")
                else:
                    ask.add(f"Phase has {n} item(s): ", fg(C["red"]) + BOLD)
                    if p.title != tm.GENERAL:
                        ask.chip(f"Move items to {tm.GENERAL}", ("rm-phase-yes", p.id), "accent")
                    ask.chip("Delete with items", ("rm-phase-all", p.id), "danger")
                body.append(ask.chip("Cancel", ("cancel",)))
            if self.mode == "add-item" and self.target == p.id:
                body.append(self.input_line("New item:", w))
            for it in shown:
                body += self.item_lines(it, w)
        return head, body


# --- terminal ---------------------------------------------------------------

MOUSE_RE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
KEYS = {"\x1b[A": "up", "\x1b[B": "down", "\x1b[5~": "pgup", "\x1b[6~": "pgdn",
        "\x1bOA": "up", "\x1bOB": "down", "\x1b[H": "home", "\x1b[F": "end"}


def events(data):
    """Split decoded input into ('mouse', b, x, y, press) / ('key', name) / ('char', c)."""
    i, out = 0, []
    while i < len(data):
        m = MOUSE_RE.match(data, i)
        if m:
            b, x, y, kind = m.groups()
            out.append(("mouse", int(b), int(x), int(y), kind == "M"))
            i = m.end()
            continue
        if data[i] == "\x1b":
            for seq, name in KEYS.items():
                if data.startswith(seq, i):
                    out.append(("key", name))
                    i += len(seq)
                    break
            else:
                m2 = re.match(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1bO.", data[i:])
                if m2 and len(m2.group(0)) > 1:
                    i += len(m2.group(0))  # an escape sequence we don't use
                else:
                    out.append(("key", "esc"))
                    i += 1
            continue
        out.append(("char", data[i]))
        i += 1
    return out


def run(start):
    app = App(start)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    out = sys.stdout
    dec = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    rows_map = []          # screen row -> Line
    last_frame = None
    last_tick = 0
    try:
        tty.setcbreak(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ISIG      # ctrl+c reaches us as a key
        termios.tcsetattr(fd, termios.TCSANOW, new)
        out.write("\033[?1049h\033[?25l\033[?1000h\033[?1006h")
        out.flush()
        while not app.quit:
            if app.mode == "idle" or app.mode.startswith("confirm") or app.mode == "rm-phase":
                app.reload()
            app.refresh_agents()
            cols, rows = shutil.get_terminal_size((80, 24))
            w = max(30, cols - 1)
            head, body = app.build(w)
            page = max(1, rows - len(head) - 1)
            app.top = max(0, min(app.top, len(body) - page))
            view = body[app.top:app.top + page]
            footer = Line()
            if app.msg and time.time() - app.msg_at < 6:
                footer.add(" " + clip(app.msg, w - 2), fg(C["cyan"]))
            elif len(body) > page:
                footer.add(f" {app.top + 1}–{min(app.top + page, len(body))} of {len(body)} · wheel or ↑/↓ to scroll",
                           fg(C["dim"]))
            rows_map = head + view + [Line()] * (page - len(view)) + [footer]
            frame = "".join(f"\033[{i + 1};1H\033[2K" + ln.render() for i, ln in enumerate(rows_map[:rows]))
            if frame != last_frame or time.time() - last_tick > 30:
                out.write(frame)
                out.flush()
                last_frame, last_tick = frame, time.time()

            if not select.select([fd], [], [], 0.5)[0]:
                continue
            data = dec.decode(os.read(fd, 4096))
            for ev in events(data):
                typing = app.mode in ("note", "redo", "add-item", "add-phase")
                if ev[0] == "mouse":
                    _, b, x, y, press = ev
                    if b == 64:
                        app.top -= 3
                    elif b == 65:
                        app.top += 3
                    elif b == 0 and press and 1 <= y <= len(rows_map):
                        ln = rows_map[y - 1]
                        hit = next((a for c0, c1, a in ln.regions if c0 <= x - 1 < c1), None)
                        if hit:
                            app.act(hit)
                    continue
                if ev[0] == "key":
                    k = ev[1]
                    if k == "esc":
                        if app.mode != "idle":
                            app.act(("cancel",)) if typing or app.mode == "rm-phase" else app.act(("mode", "idle"))
                        elif app.sel is not None:
                            app.act(("close",))
                        else:
                            app.quit = True
                    elif k in ("up", "down"):
                        app.top += -1 if k == "up" else 1
                    elif k in ("pgup", "pgdn"):
                        app.top += (-page if k == "pgup" else page)
                    elif k == "home":
                        app.top = 0
                    elif k == "end":
                        app.top = len(body)
                    continue
                c = ev[1]
                if typing:
                    if c in ("\r", "\n"):
                        app.submit()
                    elif c in ("\x7f", "\x08"):
                        app.buf = app.buf[:-1]
                    elif c == "\x15":
                        app.buf = ""
                    elif c == "\x03":
                        app.act(("cancel",))
                    elif c >= " ":
                        app.buf += c
                elif c in ("q", "\x03"):
                    app.quit = True
    finally:
        out.write("\033[?1000l\033[?1006l\033[?25h\033[?1049l")
        out.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    if "--open-popup" in sys.argv:
        subprocess.run([herdr_bin(), "plugin", "pane", "open", "--plugin", "todo-md", "--entrypoint", "viewer"])
        return
    run(active_cwd())


if __name__ == "__main__":
    main()

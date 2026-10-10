"""
TODO.md model: parsing and line edits.

Format (the agent's TODO rule): `# ` project titles, `## ` phase headings,
one top-level `- [m] #N text` line per item, markers ` ` pending, `~` running,
`x` done, `>` moved to next phase, `-` skipped. `#N` is a stable item number.
Indented lines under an item belong to it; `  - note: text` lines are notes.

Every edit works on the file's text and returns the new text; the viewer
writes it back atomically.
"""

import os
import re
import tempfile
from dataclasses import dataclass, field

ITEM_RE = re.compile(r"^[-*] \[([ ~xX>\-])\] (?:#(\d+)\s+)?(.*)$")
NOTE_RE = re.compile(r"^\s+[-*] (?:note: )?(.*)$")
GENERAL = "General"


@dataclass
class Item:
    line: int          # index of the item's own line
    end: int           # one past its last indented child line
    raw: str           # the line as read, to detect concurrent edits
    marker: str
    num: int | None
    text: str
    phase: str
    project: str
    notes: list = field(default_factory=list)

    @property
    def done(self):
        return self.marker in "xX-"

    @property
    def label(self):
        return f"#{self.num} {self.text}" if self.num is not None else self.text


@dataclass
class Phase:
    title: str
    project: str
    line: int          # heading line, -1 when items precede any heading
    end: int = 0       # one past its last line (next heading or EOF)
    items: list = field(default_factory=list)

    @property
    def id(self):
        return f"{self.project}\x00{self.title}"

    @property
    def done_count(self):
        return sum(1 for it in self.items if it.done)


def parse(text):
    lines = text.split("\n")
    phases = []
    project = ""
    phase = Phase("", project, -1)
    last = None

    def close(i):
        phase.end = i
        if phase.items or phase.title or phase.line >= 0:
            phases.append(phase)

    for i, line in enumerate(lines):
        if line.startswith("# "):
            close(i)
            project = line[2:].strip()
            # Items straight under a project title, before any `## `.
            phase = Phase("", project, -1)
            last = None
            continue
        if line.startswith("## "):
            close(i)
            phase = Phase(line[3:].strip(), project, i)
            last = None
            continue
        m = ITEM_RE.match(line)
        if m:
            mark, num, rest = m.groups()
            last = Item(i, i + 1, line, mark, int(num) if num else None,
                        rest.strip(), phase.title, project)
            phase.items.append(last)
            continue
        if last is not None and last.end == i and re.match(r"^\s+\S", line):
            n = NOTE_RE.match(line)
            last.notes.append(n.group(1) if n else line.strip())
            last.end = i + 1
            continue
        last = None
    close(len(lines))
    # A heading-less leading block with nothing in it is noise.
    return [p for p in phases if p.items or p.line >= 0]


def items(text):
    return [it for p in parse(text) for it in p.items]


def find_item(text, line):
    return next((it for it in items(text) if it.line == line), None)


def find_phase(text, pid):
    return next((p for p in parse(text) if p.id == pid), None)


def _clean(s):
    return re.sub(r"\s+", " ", s).strip()


def _splice(text, at, delete, insert):
    lines = text.split("\n")
    lines[at:at + delete] = insert
    return "\n".join(lines)


def next_number(text):
    nums = [it.num for it in items(text) if it.num is not None]
    return max(nums, default=0) + 1


def number_all(text):
    """Give unnumbered items the next free numbers, in file order."""
    lines = text.split("\n")
    n = next_number(text)
    for it in items(text):
        if it.num is None:
            lines[it.line] = re.sub(r"^([-*] \[.\] )", lambda m: f"{m.group(1)}#{n} ", it.raw, count=1)
            n += 1
    return "\n".join(lines)


def add_note(text, it, note):
    return _splice(text, it.end, 0, [f"  - note: {_clean(note)}"])


def remove_item(text, it):
    return _splice(text, it.line, it.end - it.line, [])


def set_marker(text, it, marker):
    return _splice(text, it.line, 1, [re.sub(r"\[.\]", f"[{marker}]", it.raw, count=1)])


def move_item(text, it, where):
    """Move the item's block within its phase: 'up', 'down' or 'top'.
    Returns (new text, new line) or None when it cannot move."""
    phase = next((p for p in parse(text) if any(x.line == it.line for x in p.items)), None)
    if not phase:
        return None
    idx = next(i for i, x in enumerate(phase.items) if x.line == it.line)
    lines = text.split("\n")
    block = lines[it.line:it.end]
    if where == "down":
        if idx + 1 >= len(phase.items):
            return None
        nxt = phase.items[idx + 1]
        lines[it.line:nxt.end] = lines[nxt.line:nxt.end] + lines[it.end:nxt.line] + block
        return "\n".join(lines), nxt.end - len(block)
    target = phase.items[0] if where == "top" else (phase.items[idx - 1] if idx > 0 else None)
    if target is None or target.line == it.line:
        return None
    del lines[it.line:it.end]
    lines[target.line:target.line] = block
    return "\n".join(lines), target.line


def add_item(text, phase, item_text):
    """Add a numbered pending item after the phase's last item, or under its heading."""
    at = phase.items[-1].end if phase.items else phase.line + 1
    return _splice(text, at, 0, [f"- [ ] #{next_number(text)} {_clean(item_text)}"])


def add_phase(text, title, project=None):
    """Add a `## ` phase at the end of a project (or of the file)."""
    lines = text.rstrip("\n").split("\n")
    end = len(lines)
    if project:
        starts = [i for i, l in enumerate(lines) if l.startswith("# ")]
        mine = [i for i in starts if lines[i][2:].strip() == project]
        if mine:
            later = [i for i in starts if i > mine[0]]
            end = later[0] if later else len(lines)
            while end > 0 and lines[end - 1].strip() == "":
                end -= 1
    # Trailing blank lines before the next project stay after the new heading.
    lines[end:end] = ["", f"## {_clean(title)}"]
    return "\n".join(lines) + "\n"


def remove_phase(text, phase):
    """Remove a phase; its content moves to the project's General phase, or
    the heading is renamed to General when there is none. General itself can
    only be removed once it is empty. Returns None when not allowed."""
    if phase.line < 0:
        return None
    lines = text.split("\n")
    body = [l for l in lines[phase.line + 1:phase.end] if l.strip()]
    if phase.title == GENERAL and body:
        return None
    if not body:
        del lines[phase.line:phase.end]
        return "\n".join(lines)
    general = next((p for p in parse(text)
                    if p.title == GENERAL and p.project == phase.project), None)
    if general is None:
        lines[phase.line] = f"## {GENERAL}"
        return "\n".join(lines)
    at = general.items[-1].end if general.items else general.line + 1
    # Edit the later position first so the earlier index stays valid.
    if at > phase.line:
        lines[at:at] = body
        del lines[phase.line:phase.end]
    else:
        del lines[phase.line:phase.end]
        lines[at:at] = body
    return "\n".join(lines)


def write_atomic(path, text):
    """Write via a temp file + rename so a reader never sees half a file."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".TODO.md.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

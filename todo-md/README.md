# TODO.md Viewer

A herdr popup for the active project's `TODO.md`, driven by the mouse. It reloads as the
file changes, so you can watch the agent move through its work, and lets you steer it:
add notes, reorder, delete, add items and phases, or send an item back to the agent.

Your agent's instruction file (`CLAUDE.md` / `AGENTS.md`) has to tell it to maintain the
file. For example:

```markdown
## TODO.md
- For multi-step work (3+ steps, or anything that may span sessions), keep a `TODO.md`
  at the project root (git root, else the cwd). If one exists, read it first and
  continue from it.
- Update it as status changes — when you start, finish or skip an item — not only at
  the end. One short line per item, grouped under `## ` phase headings.
- Track only what matters: milestones, decisions (mark the ones waiting on me) and
  key deliverables. Never list obvious steps (read code, edit a file, run tests, fix
  lint, commit) or split one change into several lines. Aim for ≤ ~6 items per phase.
- Number every item: `- [ ] #7 text`. Numbers are stable IDs: new item = highest + 1,
  never reused or renumbered when items move or are deleted.
- Indented `  - note: …` lines under an item are my notes: read and follow them, keep
  them when editing the file.
- Markers: `[ ]` pending · `[~]` running · `[x]` done · `[>]` moved to next phase ·
  `[-]` skipped (with a short reason). Only one `[~]` at a time.
```

## Behaviour

- Looks for `TODO.md` in the focused pane's cwd, then in each parent directory up to
  the git root (or `$HOME`).
- Header: overall progress bar and percentage, and the running (`[~]`) item with how long
  it has been running. The time counts from the file's last write when the popup first
  sees the item running, so it is a lower bound.
- `# ` titles group phases by project; each `## ` phase shows done/total and a progress bar.
- Done and skipped items are hidden, and so is a phase with nothing left open. **Show done**
  adds them back into the list.
- Click an item for its actions:
  - pending: **+ Note**, **↑** / **↓** / **⤒ Top** (order within the phase is its
    priority), **Delete**
  - done: **Re-check** (asks the agent to verify it) and **Redo** (sets it back to `[ ]`
    with a note, then asks the agent to redo it), **Delete**
  - Re-check and Redo are sent with `herdr agent prompt` to the pane the popup was opened
    from, or else to an agent working in the project's directory.
- Phase headings: **+** adds an item, **✕** removes the phase; its items move to the
  project's `General` phase (the phase is renamed to `General` when there is none).
- **+ Phase** in the header adds a phase at the end of the file; the one on a project
  title adds it at the end of that project.
- Items the popup adds get the next `#` number; when it writes the file it also numbers
  any item that has none. Writes are atomic, and an edit is refused if the item changed
  on disk since it was drawn.
- Keys only scroll and close: wheel, `↑`/`↓`, `PgUp`/`PgDn`, `Home`/`End`, `Esc` (first
  cancels what is open), `q`. While a text field is open, typing goes into it; `Enter`
  saves, `Esc` cancels.

## Shortcut

```toml
[[keys.command]]
key = "alt+t"
type = "popup"
command = "python3 /path/to/omni-herdr-plugins/todo-md/viewer.py"
width = "70%"
height = "75%"
```

You can also run it as the plugin action `todo-md.open-viewer`.

## Tests

```bash
python3 tests/test_model.py        # parsing and file edits
python3 tests/test_viewer_pty.py   # the popup in a pseudo-terminal, driven by mouse clicks
```

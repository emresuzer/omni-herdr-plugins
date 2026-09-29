# TODO.md Viewer

A read-only herdr popup that shows the active project's `TODO.md`. It reloads as the file
changes, so you can see the agent move through its work.

The plugin only displays the file. Your agent's instruction file (`CLAUDE.md` /
`AGENTS.md`) has to tell it to maintain one. For example:

```markdown
## TODO.md
- For multi-step work (3+ steps, or anything that may span sessions), keep a `TODO.md`
  at the project root (git root, else the cwd). If one exists, read it first and
  continue from it.
- Update it as status changes — when you start, finish or skip an item — not only at
  the end. One short line per item, grouped under `## ` phase headings.
- Markers: `[ ]` pending · `[~]` running · `[x]` done · `[>]` moved to next phase ·
  `[-]` skipped (with a short reason). Only one `[~]` at a time.
```

## Behaviour

- Looks for `TODO.md` in the focused pane's cwd, then in each parent directory up to
  the git root (or `$HOME`).
- Groups the items by status: running first, then pending, next phase, done and skipped.
  Each item is tagged with the `## ` phase it sits under. A file with no markers is shown
  as-is.
- Colours the markers: running in yellow, done in green, moved to next phase in cyan,
  skipped struck through. The header counts each status.
- Keys: `j`/`k` or `↑`/`↓` scroll, `PgUp`/`PgDn` page, `g`/`G` top/bottom, `q`/`Esc` close.

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

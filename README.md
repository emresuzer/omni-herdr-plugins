# omni-herdr-plugins

Plugins for [herdr](https://herdr.dev), the terminal workspace manager for AI coding agents.

| Plugin | What it does |
| :--- | :--- |
| [`auto-pane-title`](auto-pane-title/) | Sets each pane's title to the agent session name (`/rename` in Claude Code), or the folder name when no agent is running |
| [`recent-chat-items`](recent-chat-items/) | Popup picker for the last files and URLs mentioned in the active AI chat; opens them in their default app |
| [`todo-md`](todo-md/) | Popup showing the active project's `TODO.md`, live-reloading as the agent updates it |

Requirements: Linux, herdr ≥ 0.7.0, Python ≥ 3.11 (standard library only).

## Install

From GitHub:

```bash
herdr plugin install emresuzer/omni-herdr-plugins/auto-pane-title
herdr plugin install emresuzer/omni-herdr-plugins/recent-chat-items
herdr plugin install emresuzer/omni-herdr-plugins/todo-md
```

Or from a local clone, so edits take effect directly:

```bash
git clone https://github.com/emresuzer/omni-herdr-plugins
herdr plugin link omni-herdr-plugins/auto-pane-title
herdr plugin link omni-herdr-plugins/recent-chat-items
herdr plugin link omni-herdr-plugins/todo-md
```

## License

MIT

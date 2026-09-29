# Recent Chat Items Plugin for Herdr

A Herdr plugin that automatically extracts the **last 10 files and URLs** from your active AI chat (Claude Code, Agy, Gemini, Cursor, Cline, etc.) and presents an interactive picker popup.

Clicking any item with your mouse or pressing Enter/Quick-Key immediately opens the item using its related application (`xdg-open` for files, default browser for URLs).

---

## Features

- **Automatic Extraction**:
  - Scans both live terminal chat output and agent session logs (e.g. Claude Code `.jsonl` transcript and tool calls).
  - Automatically resolves relative paths against the pane's active working directory (`cwd`).
  - Filters out internal noise (temporary files, build artifacts, sockets).
- **Interactive Mouse & Keyboard Picker**:
  - **Mouse Clicks**: Click any row in the list to immediately open that file or URL.
  - **Quick Hotkeys**: Press `1`–`9` or `a`–`z` to instantly open without navigation.
  - **Arrow Navigation**: Use `↑` / `↓` (or `k` / `j`) and press `Enter` or `Space` to open.
  - **Instant Filter**: Type letters to search and filter items dynamically.
  - **Dismiss**: Press `Esc` or `q` to close.
- **Related App Launching**:
  - Uses `xdg-open` (with fallback to `gio open` and standard URL handlers) to launch images, documents, PDFs, code files, and web links in the system's configured default programs.

---

## Shortcuts

Bind the picker to a key in `~/.config/herdr/config.toml` (adjust the path to where the plugin lives):

```toml
[[keys.command]]
key = "alt+u"
type = "popup"
command = "python3 /path/to/omni-herdr-plugins/recent-chat-items/picker.py"
width = "80%"
height = "75%"
```

`prefix+u` works as a key too.

---

## Configuration

Optional. Create `config.toml` in the plugin's config dir (`herdr plugin config-dir recent-chat-items`):

```toml
# Open Markdown/Mermaid files with this program instead of xdg-open
markdown_viewer = "my-markdown-viewer"
```

---

## Manual & Action Commands

- Open picker via Herdr CLI:
  ```bash
  herdr plugin pane open --plugin recent-chat-items --entrypoint picker
  ```
- Invoke through Herdr plugin action:
  ```bash
  herdr plugin action invoke open-picker
  ```
- Run directly from shell:
  ```bash
  python3 /path/to/omni-herdr-plugins/recent-chat-items/picker.py
  ```

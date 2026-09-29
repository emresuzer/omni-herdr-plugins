# Auto Pane Title Plugin for Herdr

A Herdr plugin that automatically sets the title of each pane to:
1. The **agent session name** (the name set with `/rename` in Claude Code / AI agent sessions).
2. Otherwise, the **folder name** where the pane is currently running.

## Features
- **No changes to Herdr core code**: Implemented entirely using Herdr's native plugin architecture (`herdr-plugin.toml`).
- **Live Automatic Synchronization**: A background listener connects to Herdr's Unix domain socket and reacts to events (`pane.updated`, `pane.created`, `pane.focused`, `pane.agent_detected`, `workspace.focused`, etc.) with zero polling lag.
- **Accurate `/rename` Detection**: Directly tracks `/rename` changes from agent session metadata (`custom-title.json`, session records, and terminal title escape sequences).
- **Graceful Fallback**: Panes without active AI agent sessions automatically display the folder basename (or `~` for the home directory).
- **Debounced and Idempotent**: Prevents redundant API calls and eliminates event loops.

## Plugin Structure
- `herdr-plugin.toml`: Plugin manifest declaring startup hooks, event hooks, and actions.
- `core.py`: Core logic for title resolution (session extraction & folder determination) and socket communication.
- `daemon.py`: Background event listener subscribed to Herdr socket events.
- `sync_titles.py`: Hook entry point executed by Herdr on events or manual action.

## Management Commands
- Check installed plugins:
  ```bash
  herdr plugin list
  ```
- Force a title sync manually:
  ```bash
  herdr plugin action invoke sync
  ```
- Disable or enable the plugin:
  ```bash
  herdr plugin disable auto-pane-title
  herdr plugin enable auto-pane-title
  ```

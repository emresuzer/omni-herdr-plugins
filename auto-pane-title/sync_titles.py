#!/usr/bin/env python3
"""
CLI / Hook entrypoint for auto-pane-title plugin.
Executes an immediate sync of pane titles and ensures the background daemon is running.
"""

import sys
from core import sync_all_panes, ensure_daemon_running


def main():
    # Sync all pane titles immediately
    sync_all_panes()

    # Ensure real-time background listener daemon is alive
    ensure_daemon_running()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Launcher script for recent-chat-items picker popup pane.
Can be invoked as a Herdr plugin action or CLI command.
"""

import os
import sys
import subprocess
import shutil

HERDR_BIN = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"


def main():
    # Open the picker entrypoint as a popup pane in Herdr
    cmd = [
        HERDR_BIN,
        "plugin", "pane", "open",
        "--plugin", "recent-chat-items",
        "--entrypoint", "picker"
    ]
    try:
        subprocess.run(cmd, check=True)
    except Exception as e:
        sys.stderr.write(f"Failed to open picker pane: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

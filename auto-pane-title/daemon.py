#!/usr/bin/env python3
"""
Background event listener daemon for auto-pane-title plugin.
Listens to real-time Herdr events (pane.updated, pane.created, pane.focused, tab.focused, etc.)
and keeps all pane and tab titles synchronized with zero delay.
"""

import os
import sys
import json
import time
import socket
import select
import signal

from core import (
    PID_FILE,
    find_herdr_socket,
    sync_all_panes,
    is_daemon_running,
)

RUNNING = True


def handle_signal(sig, frame):
    global RUNNING
    RUNNING = False


def cleanup():
    try:
        if os.path.isfile(PID_FILE):
            with open(PID_FILE, "r") as f:
                stored_pid = int(f.read().strip())
            if stored_pid == os.getpid():
                os.remove(PID_FILE)
    except Exception:
        pass


def run_daemon():
    global RUNNING

    if is_daemon_running():
        sys.exit(0)

    # Write PID
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    reconnect_attempts = 0
    max_reconnect_attempts = 30

    while RUNNING:
        sock_path = find_herdr_socket()
        if not sock_path or not os.path.exists(sock_path):
            time.sleep(2.0)
            reconnect_attempts += 1
            if reconnect_attempts > max_reconnect_attempts:
                break
            continue

        reconnect_attempts = 0

        # Initial sync
        try:
            sync_all_panes(sock_path=sock_path)
        except Exception:
            pass

        # Connect event subscription socket
        try:
            sub_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sub_sock.connect(sock_path)

            sub_req = {
                "id": f"sub-{int(time.time())}",
                "method": "events.subscribe",
                "params": {
                    "subscriptions": [
                        {"type": "workspace.created"},
                        {"type": "workspace.focused"},
                        {"type": "workspace.updated"},
                        {"type": "workspace.renamed"},
                        {"type": "tab.created"},
                        {"type": "tab.closed"},
                        {"type": "tab.focused"},
                        {"type": "tab.renamed"},
                        {"type": "tab.moved"},
                        {"type": "pane.created"},
                        {"type": "pane.closed"},
                        {"type": "pane.updated"},
                        {"type": "pane.focused"},
                        {"type": "pane.moved"},
                        {"type": "pane.exited"},
                        {"type": "pane.agent_detected"},
                        {"type": "layout.updated"},
                    ]
                }
            }
            sub_sock.sendall(json.dumps(sub_req).encode() + b"\n")

            # Read initial response
            sub_sock.settimeout(5.0)
            initial = sub_sock.recv(4096)
            if not initial:
                sub_sock.close()
                time.sleep(2.0)
                continue

            resp_obj = json.loads(initial.decode(errors="ignore").strip().split("\n")[0])
            if "error" in resp_obj:
                # Subscription failed
                sub_sock.close()
                time.sleep(3.0)
                continue

            sub_sock.settimeout(None)
            buffer = b""
            last_sync_time = time.time()

            while RUNNING:
                # Wait for event or periodic 2-second heartbeat check
                r, _, _ = select.select([sub_sock], [], [], 2.0)
                if not r:
                    # Timeout heartbeat: periodic check
                    sync_all_panes(sock_path=sock_path)
                    last_sync_time = time.time()
                    continue

                chunk = sub_sock.recv(4096)
                if not chunk:
                    # Socket closed by server
                    break

                buffer += chunk
                if b"\n" in buffer:
                    lines = buffer.split(b"\n")
                    buffer = lines[-1]
                    # Debounce: avoid running faster than once per 0.25s
                    now = time.time()
                    if now - last_sync_time >= 0.25:
                        sync_all_panes(sock_path=sock_path)
                        last_sync_time = time.time()

            sub_sock.close()
        except Exception:
            time.sleep(2.0)

    cleanup()


if __name__ == "__main__":
    try:
        run_daemon()
    finally:
        cleanup()

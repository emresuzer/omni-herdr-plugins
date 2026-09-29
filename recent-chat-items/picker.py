#!/usr/bin/env python3
"""
Interactive mouse-enabled picker for recent-chat-items Herdr plugin.
Displays the last 10 files and URLs from the active AI chat.
Clicking any item or pressing Enter/Hotkeys immediately opens it with its related app.
"""

import os
import sys
import tty
import termios
import select
import shutil
import subprocess
import time
import json
import tomllib

from extractor import (
    get_recent_chat_items,
    get_active_pane_info,
    find_git_root,
    find_in_project,
    api_call,
    find_herdr_socket,
)


def load_config():
    """Read config.toml from the plugin's herdr config dir, if present."""
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or os.path.expanduser(
        "~/.config/herdr/plugins/config/recent-chat-items"
    )
    try:
        with open(os.path.join(config_dir, "config.toml"), "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def focus_window_in_niri(target):
    """Ensure the opened window is brought into view and focused under Niri compositor."""
    if not shutil.which("niri") or not os.environ.get("WAYLAND_DISPLAY"):
        return False
    fname = os.path.basename(target).lower() if target else ""
    for _ in range(4):
        time.sleep(0.15)
        try:
            out = subprocess.check_output(["niri", "msg", "--json", "windows"], stderr=subprocess.DEVNULL)
            windows = json.loads(out)
            # 1. Match by file name in window title
            for w in windows:
                title = (w.get("title") or "").lower()
                if fname and fname in title:
                    subprocess.run(["niri", "msg", "action", "focus-window", "--id", str(w["id"])],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
            # 2. Match by app_id for markdown viewer
            if target.endswith((".md", ".markdown", ".mmd", ".mermaid", ".ow")):
                for w in windows:
                    app_id = (w.get("app_id") or "").lower()
                    if "markdown" in app_id:
                        subprocess.run(["niri", "msg", "action", "focus-window", "--id", str(w["id"])],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return True
        except Exception:
            pass
    return False


def open_with_related_app(target, cwd=None):
    """Launch the file or URL with the system's default / related application."""
    if not target:
        return False

    is_url = target.startswith("http://") or target.startswith("https://")
    if not is_url:
        # If target is relative, resolve against cwd
        if not os.path.isabs(target) and cwd:
            target = os.path.join(cwd, target)
        target = os.path.realpath(os.path.abspath(target))

        # If file still doesn't exist, search within project
        if not os.path.exists(target):
            alt = find_in_project(os.path.basename(target), [cwd] if cwd else [])
            if alt:
                target = alt
            else:
                return False

    # Determine optimal working directory for the spawned app
    if not is_url:
        work_dir = target if os.path.isdir(target) else os.path.dirname(target)
    else:
        work_dir = cwd or os.path.expanduser("~")

    launched = False

    # 1. Optional dedicated viewer for markdown files (markdown_viewer in config.toml)
    viewer = load_config().get("markdown_viewer")
    if viewer and not is_url and target.endswith((".md", ".markdown", ".mmd", ".mermaid", ".ow")):
        viewer_bin = shutil.which(os.path.expanduser(viewer))
        if viewer_bin:
            try:
                subprocess.Popen(
                    [viewer_bin, target],
                    cwd=work_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                launched = True
            except Exception:
                pass

    # 2. Try gio open
    if not launched and not is_url:
        try:
            subprocess.Popen(
                ["gio", "open", target],
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            launched = True
        except Exception:
            pass

    # 3. Try xdg-open
    if not launched:
        try:
            subprocess.Popen(
                ["xdg-open", target],
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            launched = True
        except Exception:
            pass

    # 4. Fallback for URL
    if not launched and is_url:
        try:
            import webbrowser
            webbrowser.open(target)
            launched = True
        except Exception:
            pass

    # 5. Bring the opened application window into focus in Niri
    if launched and not is_url:
        focus_window_in_niri(target)

    return launched


def notify_herdr(title, body=""):
    """Show a brief Herdr toast notification if available."""
    try:
        api_call("notification.show", {
            "title": title,
            "body": body,
            "position": "bottom-right",
            "sound": "done"
        })
    except Exception:
        pass


def run_zenity_picker(items):
    """Fallback GUI picker using zenity if no TTY is available."""
    if not shutil.which("zenity") or not os.environ.get("DISPLAY"):
        return None

    cmd = [
        "zenity", "--list",
        "--title=Recent Chat Files & URLs",
        "--text=Click an item to open with its related app:",
        "--width=750", "--height=450",
        "--column=Key", "--column=Type", "--column=Target",
        "--print-column=3"
    ]
    for item in items:
        cmd.extend([item["key"], item["type"], item["target"]])

    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def run_tui_picker(items, pane_id=None, cwd=None):
    """Interactive TUI picker with SGR mouse click and keyboard support."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    # Hotkey mapping
    hotkey_map = {}
    for it in items:
        if it.get("key"):
            hotkey_map[it["key"].lower()] = it

    selected_idx = 0
    filter_text = ""

    def get_filtered_items():
        if not filter_text:
            return items
        q = filter_text.lower()
        return [it for it in items if q in it["display"].lower() or q in it["target"].lower()]

    try:
        tty.setraw(fd)
        # Enter alternate screen buffer, hide cursor, enable 1000 + 1002 mouse tracking and SGR 1006 mode
        sys.stdout.write("\033[?1049h\033[?25l\033[?1000h\033[?1002h\033[?1006h")
        sys.stdout.flush()

        # Track row -> item mapping for mouse click detection
        row_item_map = {}

        def draw():
            nonlocal selected_idx, row_item_map
            row_item_map = {}
            cols, rows = shutil.get_terminal_size((80, 24))
            buf = []

            # Clear screen
            buf.append("\033[H\033[2J")

            # Colors
            c_reset = "\033[0m"
            c_bold = "\033[1m"
            c_dim = "\033[2m"
            c_title = "\033[1;38;5;39m"         # Cyan
            c_border = "\033[38;5;242m"        # Grey border
            c_file_badge = "\033[1;38;5;75m"   # Blue file
            c_dir_badge = "\033[1;38;5;214m"   # Orange dir
            c_img_badge = "\033[1;38;5;207m"   # Magenta image
            c_url_badge = "\033[1;38;5;78m"    # Green URL
            c_hl = "\033[1;48;5;238;38;5;255m" # Highlighted row
            c_key = "\033[1;38;5;220m"         # Yellow key
            c_info = "\033[38;5;246m"          # Info text

            # Header
            title_text = " 📂 Recent Chat Files & URLs "
            header_str = f"┌─{title_text}" + "─" * max(0, cols - len(title_text) - 4) + "┐"
            buf.append(f"\033[1;1H{c_border}{header_str}{c_reset}")

            sub_text = f" Active Pane: {pane_id or 'current'} | Directory: {os.path.basename(cwd) or '~'} "
            if len(sub_text) > cols - 4:
                sub_text = sub_text[:cols - 7] + "..."
            buf.append(f"\033[2;1H{c_border}│{c_info}{sub_text}{c_reset}" + " " * max(0, cols - len(sub_text) - 2) + f"{c_border}│{c_reset}")

            div_str = "├" + "─" * (cols - 2) + "┤"
            buf.append(f"\033[3;1H{c_border}{div_str}{c_reset}")

            filtered = get_filtered_items()
            if selected_idx >= len(filtered):
                selected_idx = max(0, len(filtered) - 1)

            curr_row = 4
            max_list_rows = rows - 6

            if not filtered:
                msg = "  No items found" if not filter_text else f"  No items matching '{filter_text}'"
                buf.append(f"\033[{curr_row};1H{c_border}│{c_dim}{msg}{c_reset}" + " " * max(0, cols - len(msg) - 2) + f"{c_border}│{c_reset}")
                curr_row += 1
            else:
                start_idx = 0
                if selected_idx >= max_list_rows:
                    start_idx = selected_idx - max_list_rows + 1

                for idx in range(start_idx, min(len(filtered), start_idx + max_list_rows)):
                    it = filtered[idx]
                    is_selected = (idx == selected_idx)
                    row_item_map[curr_row] = it

                    # Formatting badge and display
                    itype = it["type"]
                    if itype == "DIR":
                        badge = f"{c_dir_badge}[DIR ]{c_reset}"
                        icon = "📁"
                    elif itype == "IMG":
                        badge = f"{c_img_badge}[IMG ]{c_reset}"
                        icon = "🖼️"
                    elif itype == "URL":
                        badge = f"{c_url_badge}[URL ]{c_reset}"
                        icon = "🌐"
                    else:
                        badge = f"{c_file_badge}[FILE]{c_reset}"
                        icon = "📄"

                    key_label = f"{c_key}[{it['key']}]{c_reset}"

                    target_str = it["display"]
                    avail_len = cols - 18
                    if len(target_str) > avail_len:
                        half = (avail_len - 3) // 2
                        target_str = target_str[:half] + "..." + target_str[-half:]

                    line_content = f" {key_label} {badge} {icon} {target_str}"
                    plain_len = 1 + 3 + 1 + 6 + 1 + 2 + 1 + len(target_str)
                    pad_spaces = " " * max(0, cols - plain_len - 2)

                    if is_selected:
                        line_styled = f"{c_hl}{line_content}{pad_spaces}{c_reset}"
                        buf.append(f"\033[{curr_row};1H{c_border}│{line_styled}{c_border}│{c_reset}")
                    else:
                        buf.append(f"\033[{curr_row};1H{c_border}│{line_content}{pad_spaces}{c_border}│{c_reset}")

                    curr_row += 1

            # Fill remaining rows
            while curr_row < rows - 2:
                buf.append(f"\033[{curr_row};1H{c_border}│" + " " * (cols - 2) + f"│{c_reset}")
                curr_row += 1

            # Footer
            buf.append(f"\033[{rows-2};1H{c_border}{div_str}{c_reset}")
            help_str = " [Click / Enter] Open in app   [1-9, a-z] Quick Key   [↑/↓] Move   [q/Esc] Close "
            if filter_text:
                help_str = f" Filter: '{filter_text}' (Backspace to delete) | [Enter] Open "
            if len(help_str) > cols - 4:
                help_str = help_str[:cols - 4]
            buf.append(f"\033[{rows-1};1H{c_border}│{c_dim}{help_str}{c_reset}" + " " * max(0, cols - len(help_str) - 2) + f"{c_border}│{c_reset}")
            bottom_str = "└" + "─" * (cols - 2) + "┘"
            buf.append(f"\033[{rows};1H{c_border}{bottom_str}{c_reset}")

            sys.stdout.write("".join(buf))
            sys.stdout.flush()

        draw()

        input_buf = b""
        while True:
            # Poll stdin
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                continue

            chunk = os.read(fd, 1024)
            if not chunk:
                break
            input_buf += chunk

            while input_buf:
                # 1. Parse SGR mouse sequence: \x1b[<btn;col;rowM or \x1b[<btn;col;rowm
                if input_buf.startswith(b"\x1b[<"):
                    end_idx = -1
                    for idx, byte_val in enumerate(input_buf[3:], 3):
                        if byte_val in (ord(b'M'), ord(b'm')):
                            end_idx = idx
                            break
                    if end_idx != -1:
                        seq = input_buf[:end_idx + 1].decode("ascii", errors="ignore")
                        input_buf = input_buf[end_idx + 1:]

                        # Extract btn, col, row, kind
                        try:
                            payload = seq[3:-1]
                            kind = seq[-1]
                            parts = payload.split(";")
                            btn = int(parts[0])
                            col = int(parts[1])
                            row = int(parts[2])

                            # Left click press (btn 0 and kind 'M')
                            if btn == 0 and kind == 'M':
                                if row in row_item_map:
                                    return row_item_map[row]["target"]
                                elif row == rows or row == rows - 1:
                                    # Clicked footer / close
                                    pass
                            elif btn == 64:  # Wheel up
                                selected_idx = max(0, selected_idx - 1)
                                draw()
                            elif btn == 65:  # Wheel down
                                filtered = get_filtered_items()
                                selected_idx = min(len(filtered) - 1, selected_idx + 1)
                                draw()
                        except Exception:
                            pass
                        continue
                    else:
                        # Partial mouse sequence, wait for rest
                        break

                # 2. Parse ANSI Arrow keys
                if input_buf.startswith(b"\x1b[A"):  # Up
                    input_buf = input_buf[3:]
                    selected_idx = max(0, selected_idx - 1)
                    draw()
                    continue
                elif input_buf.startswith(b"\x1b[B"):  # Down
                    input_buf = input_buf[3:]
                    filtered = get_filtered_items()
                    selected_idx = min(len(filtered) - 1, selected_idx + 1)
                    draw()
                    continue
                elif input_buf.startswith(b"\x1b[C") or input_buf.startswith(b"\x1b[D"):
                    input_buf = input_buf[3:]
                    continue
                elif input_buf.startswith(b"\x1b"):  # Esc or other escape
                    if len(input_buf) == 1:
                        return None
                    # Drain unknown escape sequence
                    if len(input_buf) >= 2 and input_buf[1:2] in (b"[", b"O"):
                        drained = 2
                        while drained < len(input_buf) and not (0x40 <= input_buf[drained] <= 0x7E):
                            drained += 1
                        if drained < len(input_buf):
                            input_buf = input_buf[drained + 1:]
                            continue
                        else:
                            break
                    else:
                        input_buf = input_buf[1:]
                        return None

                # 3. Single character keys
                ch = input_buf[:1]
                input_buf = input_buf[1:]

                # Enter / Space
                if ch in (b"\r", b"\n", b" "):
                    filtered = get_filtered_items()
                    if filtered and 0 <= selected_idx < len(filtered):
                        return filtered[selected_idx]["target"]
                    return None

                # Ctrl+C or q (if not filtering)
                if ch in (b"\x03",):
                    return None
                if ch == b"q" and not filter_text:
                    return None

                # Backspace
                if ch in (b"\x7f", b"\x08"):
                    if filter_text:
                        filter_text = filter_text[:-1]
                        selected_idx = 0
                        draw()
                    continue

                char_str = ch.decode("utf-8", errors="ignore")
                char_lower = char_str.lower()

                # Hotkey direct selection (1-9, a-z)
                if char_lower in hotkey_map and not filter_text:
                    return hotkey_map[char_lower]["target"]

                # Add to filter text
                if char_str.isprintable():
                    filter_text += char_str
                    selected_idx = 0
                    draw()

    finally:
        # Restore terminal mouse modes, cursor, and alternate screen
        sys.stdout.write("\033[?1006l\033[?1002l\033[?1000l\033[?25h\033[?1049l")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return None


import argparse


def main():
    parser = argparse.ArgumentParser(description="Recent Chat Items Picker")
    parser.add_argument("--lines", "-n", type=int, default=None, help="Number of recent terminal lines to scan (default: 150)")
    parser.add_argument("--pane", type=str, default=None, help="Target pane ID")
    parser.add_argument("--cwd", type=str, default=None, help="Working directory")
    args, _ = parser.parse_known_args()

    det_pane, det_cwd = get_active_pane_info()
    pane_id = args.pane or det_pane
    cwd = args.cwd or det_cwd
    files, urls = get_recent_chat_items(pane_id, cwd, max_files=10, max_urls=10, scan_lines=args.lines)

    # Build indexed items list
    items = []
    keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
            "a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    key_idx = 0

    # Add files and directories
    image_exts = {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"}
    git_root = find_git_root(cwd)

    for f in files:
        k = keys[key_idx] if key_idx < len(keys) else str(key_idx + 1)
        key_idx += 1

        is_dir = os.path.isdir(f)
        ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
        if is_dir:
            itype = "DIR"
        elif ext in image_exts:
            itype = "IMG"
        else:
            itype = "FILE"

        # Short display path: relative to cwd, git_root, or home if shorter
        disp = f
        if "scratchpad" in f:
            parts = f.split("scratchpad")
            if len(parts) >= 2:
                disp = "scratchpad" + parts[1]
                if is_dir and not disp.endswith("/"):
                    disp += "/"
        elif cwd and f.startswith(cwd + "/"):
            disp = f[len(cwd) + 1:]
            if is_dir and not disp.endswith("/"):
                disp += "/"
        elif git_root and f.startswith(git_root + "/"):
            disp = f[len(git_root) + 1:]
            if is_dir and not disp.endswith("/"):
                disp += "/"
        elif f.startswith(os.path.expanduser("~") + "/"):
            disp = "~/" + f[len(os.path.expanduser("~")) + 1:]
            if is_dir and not disp.endswith("/"):
                disp += "/"

        # target is GUARANTEED to be the verified canonical absolute path
        items.append({
            "key": k,
            "type": itype,
            "target": os.path.realpath(f),
            "display": disp
        })

    # Add URLs
    for u in urls:
        k = keys[key_idx] if key_idx < len(keys) else str(key_idx + 1)
        key_idx += 1
        items.append({
            "key": k,
            "type": "URL",
            "target": u,
            "display": u
        })

    if not items:
        notify_herdr("Recent Chat Items", "No files or URLs found in active chat.")
        print("No recent files or URLs found in active chat.")
        return

    # Choose picker: TUI or Zenity
    selected = None
    if sys.stdin.isatty():
        selected = run_tui_picker(items, pane_id=pane_id, cwd=cwd)
    else:
        selected = run_zenity_picker(items)

    if selected:
        notify_herdr("Opening Item", os.path.basename(selected) if not selected.startswith("http") else selected)
        open_with_related_app(selected, cwd=cwd)

    try:
        import time as _t
        with open("/tmp/recent_chat_items.log", "a") as _lf:
            _lf.write(f"[{_t.ctime()}] pane_id={pane_id} cwd={cwd} items_count={len(items)} selected={selected}\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()

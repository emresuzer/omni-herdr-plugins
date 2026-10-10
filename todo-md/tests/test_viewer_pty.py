"""Drive viewer.py in a pseudo-terminal with SGR mouse clicks and check TODO.md."""
import fcntl
import json
import os
import pty
import select
import shutil
import struct
import sys
import tempfile
import termios
import time
import unittest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
COLS, ROWS = 100, 40

TEXT = """# Proj

## Build
- [x] #1 Done thing
- [~] #2 Running thing
- [ ] #3 Pending thing
  - note: keep this

## Ship
- [x] #4 Shipped
"""


class Viewer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "TODO.md")
        with open(self.path, "w") as f:
            f.write(TEXT)
        self.log = os.path.join(self.dir, "herdr.log")
        fake = os.path.join(self.dir, "herdr")
        agents = {"result": {"agents": [{"pane_id": "w1:p1", "cwd": self.dir, "focused": True}]}}
        with open(fake, "w") as f:
            f.write("#!/bin/sh\n"
                    f'printf "%s\\n" "$*" >> {self.log}\n'
                    f"[ \"$1 $2\" = \"agent list\" ] && echo '{json.dumps(agents)}'\n"
                    "exit 0\n")
        os.chmod(fake, 0o755)
        # A minimal, explicit environment: no herdr session, a fake herdr binary.
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "xterm-256color",
               "HERDR_ACTIVE_PANE_CWD": self.dir, "HERDR_BIN_PATH": fake,
               "HERDR_SOCKET_PATH": "/nonexistent", "HOME": self.dir,
               "XDG_CACHE_HOME": os.path.join(self.dir, "cache")}
        for k in ("HERDR_PANE_ID", "HERDR_ACTIVE_PANE_ID", "HERDR_PLUGIN_CONTEXT_JSON"):
            os.environ.pop(k, None)
        for k in ("HERDR_ACTIVE_PANE_CWD", "HERDR_BIN_PATH", "XDG_CACHE_HOME"):
            os.environ[k] = env[k]
        sys.path.insert(0, HERE)
        import viewer
        self.viewer = viewer
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, "-I", os.path.join(HERE, "viewer.py")], env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.pid, self.fd = pid, fd
        self.screen = self.drain(1.0)

    def tearDown(self):
        try:
            os.write(self.fd, b"q")
            time.sleep(0.3)
            os.waitpid(self.pid, 0)
        except OSError:
            pass
        os.close(self.fd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def drain(self, wait=0.6):
        out, end = b"", time.time() + wait
        while time.time() < end:
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    out += os.read(self.fd, 65536)
                except OSError:
                    break
        return out.decode(errors="replace")

    def file(self):
        with open(self.path) as f:
            return f.read()

    def click(self, action, **state):
        """Click the region bound to `action`, laid out as the viewer does for `state`."""
        app = self.viewer.App(self.dir)
        for k, v in state.items():
            setattr(app, k, v)
        head, body = app.build(COLS - 1)
        page = ROWS - len(head) - 1
        rows = head + body[:page]
        for y, ln in enumerate(rows):
            for c0, _c1, a in ln.regions:
                if a == action:
                    os.write(self.fd, f"\x1b[<0;{c0 + 2};{y + 1}M\x1b[<0;{c0 + 2};{y + 1}m".encode())
                    return self.drain()
        self.fail(f"no region for {action} in state {state}")

    def type(self, text):
        os.write(self.fd, text.encode())
        return self.drain()

    def test_default_view_hides_done(self):
        self.assertIn("Running thing", self.screen)
        self.assertIn("Pending thing", self.screen)
        self.assertNotIn("Done thing", self.screen)
        self.assertNotIn("Ship", self.screen)          # all-done phase hidden entirely
        self.assertIn("now", self.screen)

    def test_show_done_adds_items(self):
        s = self.click(("toggle-done",))
        self.assertIn("Done thing", s)
        self.assertIn("Shipped", s)

    def test_note_and_move(self):
        self.click(("select", 5))
        self.click(("mode", "note"), sel=5)
        self.type("check the Turkish ğüşİ text\r")
        self.assertIn("- [ ] #3 Pending thing\n  - note: keep this\n  - note: check the Turkish ğüşİ text\n", self.file())
        self.click(("move", "top"), sel=5)
        self.assertIn("## Build\n- [ ] #3 Pending thing\n  - note: keep this\n  - note: check", self.file())

    def test_delete_with_confirm(self):
        self.click(("select", 5))
        self.click(("mode", "confirm-delete"), sel=5)
        self.assertIn("#3", self.file())
        self.click(("delete",), sel=5, mode="confirm-delete")
        self.assertNotIn("Pending thing", self.file())
        self.assertNotIn("keep this", self.file())

    def test_recheck_and_redo_prompt_agent(self):
        self.click(("toggle-done",))
        self.click(("select", 3), show_done=True)
        self.click(("verify",), sel=3, show_done=True)
        with open(self.log) as f:
            self.assertIn("agent prompt w1:p1 Re-check TODO item #1 Done thing", f.read())
        self.click(("mode", "redo"), sel=3, show_done=True)
        self.type("use the new API\r")
        self.assertIn("- [ ] #1 Done thing\n  - note: reopened ", self.file())
        self.assertIn(": use the new API", self.file())
        with open(self.log) as f:
            self.assertIn("Redo TODO item #1 Done thing", f.read())

    def test_add_item_phase_and_remove_phase(self):
        pid = "Proj\x00Build"
        self.click(("add-item", pid))
        self.type("New milestone\r")
        self.assertIn("- [ ] #3 Pending thing\n  - note: keep this\n- [ ] #5 New milestone\n", self.file())
        self.click(("add-phase", "Proj"))
        self.type("Later\r")
        self.assertTrue(self.file().endswith("## Later\n"))
        self.click(("rm-phase", pid))
        self.click(("rm-phase-yes", pid), mode="rm-phase", target=pid)
        self.assertNotIn("## Build", self.file())
        self.assertIn("## General\n- [x] #1 Done thing", self.file())

    def test_esc_cancels_then_closes(self):
        self.click(("select", 5))
        self.click(("mode", "note"), sel=5)
        self.type("draft")
        self.type("\x1b")
        self.assertNotIn("draft", self.file())
        self.type("\x1b")   # closes the selection
        os.write(self.fd, b"\x1b")   # closes the popup
        time.sleep(0.8)
        pid, _ = os.waitpid(self.pid, os.WNOHANG)
        self.assertEqual(pid, self.pid)


if __name__ == "__main__":
    unittest.main()

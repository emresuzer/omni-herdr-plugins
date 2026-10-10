import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import todo_model as tm  # noqa: E402

SRC = "\n".join([
    "# Proj",              # 0
    "",                    # 1
    "## A",                # 2
    "- [x] #1 one",        # 3
    "- [ ] #2 two",        # 4
    "  - note: old note",  # 5
    "- [~] three",         # 6
    "",                    # 7
    "## B",                # 8
    "- [-] #4 four (no longer needed)",  # 9
    "",
])


class Model(unittest.TestCase):
    def test_parse(self):
        p = tm.parse(SRC)
        self.assertEqual([x.title for x in p], ["A", "B"])
        a = p[0]
        self.assertEqual([i.marker for i in a.items], ["x", " ", "~"])
        self.assertEqual([i.num for i in a.items], [1, 2, None])
        self.assertEqual(a.items[1].notes, ["old note"])
        self.assertEqual((a.line, a.end), (2, 8))
        self.assertEqual(a.items[2].label, "three")

    def test_numbering(self):
        self.assertEqual(tm.next_number(SRC), 5)
        out = tm.number_all(SRC)
        self.assertIn("- [~] #5 three", out)
        self.assertEqual(tm.number_all(out), out)

    def test_note_marker_delete(self):
        two = tm.find_item(SRC, 4)
        self.assertEqual(tm.add_note(SRC, two, "new  note").split("\n")[6], "  - note: new note")
        self.assertEqual(tm.set_marker(SRC, two, "x").split("\n")[4], "- [x] #2 two")
        self.assertEqual(tm.remove_item(SRC, two).split("\n")[3:5], ["- [x] #1 one", "- [~] three"])

    def test_move(self):
        two = tm.find_item(SRC, 4)
        text, line = tm.move_item(SRC, two, "down")
        self.assertEqual(text.split("\n")[3:7], ["- [x] #1 one", "- [~] three", "- [ ] #2 two", "  - note: old note"])
        self.assertEqual(line, 5)
        text, line = tm.move_item(SRC, two, "top")
        self.assertEqual(text.split("\n")[3:6], ["- [ ] #2 two", "  - note: old note", "- [x] #1 one"])
        self.assertEqual(line, 3)
        self.assertIsNone(tm.move_item(SRC, tm.find_item(SRC, 6), "down"))

    def test_add_item_and_phase(self):
        a = tm.parse(SRC)[0]
        self.assertEqual(tm.add_item(SRC, a, "five").split("\n")[7], "- [ ] #5 five")
        out = tm.add_phase(SRC, "C")
        self.assertTrue(out.endswith("## B\n- [-] #4 four (no longer needed)\n\n## C\n"))
        c = tm.find_phase(out, "Proj\x00C")
        self.assertTrue(tm.add_item(out, c, "first").endswith("## C\n- [ ] #5 first\n"))

    def test_add_phase_in_project(self):
        two = "# P1\n\n## A\n- [ ] #1 a\n\n# P2\n\n## B\n- [ ] #2 b\n"
        out = tm.add_phase(two, "New", "P1")
        self.assertIn("- [ ] #1 a\n\n## New\n\n# P2", out)

    def test_remove_phase(self):
        b = tm.find_phase(SRC, "Proj\x00B")
        out = tm.remove_phase(SRC, b)            # no General yet: renamed in place
        self.assertIn("## General\n- [-] #4", out)
        a = tm.find_phase(out, "Proj\x00A")
        out = tm.remove_phase(out, a)            # joins the existing General
        self.assertNotIn("## A", out)
        self.assertIn("## General\n- [-] #4 four (no longer needed)\n- [x] #1 one\n- [ ] #2 two\n  - note: old note\n- [~] three", out)
        g = tm.find_phase(out, "Proj\x00General")
        self.assertIsNone(tm.remove_phase(out, g))   # General with items stays
        empty = "## X\n\n## Y\n- [ ] #1 y\n"
        self.assertEqual(tm.remove_phase(empty, tm.find_phase(empty, "\x00X")), "## Y\n- [ ] #1 y\n")

    def test_write_atomic(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "TODO.md")
        with open(p, "w") as f:
            f.write("x")
        os.chmod(p, 0o640)
        tm.write_atomic(p, "hello\n")
        with open(p) as f:
            self.assertEqual(f.read(), "hello\n")
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o640)
        self.assertEqual(os.listdir(d), ["TODO.md"])


if __name__ == "__main__":
    unittest.main()

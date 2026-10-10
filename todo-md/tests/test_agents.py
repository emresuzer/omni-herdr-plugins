import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agents as ag  # noqa: E402


def make(sub, aid, kind, job, entries, age=0):
    with open(os.path.join(sub, f"agent-{aid}.meta.json"), "w") as f:
        json.dump({"agentType": kind, "description": job}, f)
    log = os.path.join(sub, f"agent-{aid}.jsonl")
    with open(log, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    t = time.time() - age
    os.utime(log, (t, t))


START = {"type": "user", "timestamp": "2026-10-10T08:00:00.000Z", "message": {"role": "user", "content": "go"}}


def tool(name, **inp):
    return {"type": "assistant", "message": {"stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": name, "input": inp}]}}


def reply(text):
    return {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


class Agents(unittest.TestCase):
    def test_states(self):
        session = tempfile.mkdtemp()
        sub = os.path.join(session, "subagents")
        os.makedirs(sub)
        make(sub, "a1", "builder", "Build the inn", [START, tool("Bash", command="make  test")])
        make(sub, "a2", "Explore", "Find callers", [START, reply("Found 3 callers.")])
        make(sub, "a3", "builder", "Long wait", [START, tool("Bash", command="sleep 900")], age=15 * 60)
        make(sub, "a4", "builder", "Ancient", [START, reply("old")], age=7 * 3600)
        make(sub, "a5", "builder", "Mid-turn", [START, {"type": "user", "message": {"content": [{"type": "tool_result"}]}}])
        got = {a.id: a for a in ag.list_agents(session)}
        self.assertEqual(set(got), {"a1", "a2", "a3", "a5"})            # a4 too old
        self.assertEqual((got["a1"].state, got["a1"].activity), ("working", "Bash: make test"))
        self.assertEqual((got["a2"].state, got["a2"].activity), ("finished", "Found 3 callers."))
        self.assertEqual(got["a3"].state, "quiet")
        self.assertEqual((got["a5"].state, got["a5"].activity), ("working", "thinking"))
        self.assertEqual(got["a1"].started, ag._ts("2026-10-10T08:00:00.000Z"))
        order = [a.state for a in ag.list_agents(session)]
        self.assertEqual(order[-1], "finished")

    def test_timestamp_is_utc(self):
        self.assertEqual(ag._ts("1970-01-01T00:01:00.000Z"), 60.0)

    def test_no_session(self):
        self.assertEqual(ag.list_agents(None), [])
        self.assertEqual(ag.list_agents("/nonexistent"), [])


if __name__ == "__main__":
    unittest.main()

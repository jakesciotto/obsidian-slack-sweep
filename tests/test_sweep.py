import tempfile
import unittest
from pathlib import Path
from unittest import mock

from slack_sweep import agent, schedule, sweep, vault
from slack_sweep.config import Config, Schedule, Vault

TODAY = "2026-10-09"
NOW = 1_791_500_000.0
ROOT = "https://acme.slack.com/archives/C1/p1700000000000001"
REPLY = "https://acme.slack.com/archives/C1/p1700000000000500?thread_ts=1700000000.000001&cid=C1"
KEY = "C1/1700000000.000001"

INBOX = """# Inbox

## Untriaged
- [ ] buy milk

## Later
- [ ] someday
"""


class SweepTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.cfg = Vault(path=self.dir.name, inbox="inbox.md", section="## Untriaged", exclude=("archives",))

    def tearDown(self):
        self.dir.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def sweep(self, records, new=(), completed=()):
        tasks = sweep.collect(self.cfg)
        sweep.reconcile(records, tasks, NOW)
        rows = sweep.open_tasks(records, tasks, self.cfg.complete)
        checkable = {k for row in rows if row["check"] for k in row["keys"]}
        result = {"new_tasks": list(new), "completed": list(completed), "notes": ""}
        plan = sweep.plan(result, tasks, records, checkable, self.cfg, TODAY, NOW)
        return plan, sweep.apply(plan, self.cfg, tasks, records, TODAY, NOW)

    def test_new_task_lands_at_end_of_section_with_unsafe_text_neutralized(self):
        inbox = self.write("inbox.md", INBOX)
        records = {}
        item = {"permalink": REPLY, "tag": "acme", "text": "answer [[Secret]] #urgent [tier:: now] https://evil.example", "reason": "r"}

        self.sweep(records, new=[item])

        lines = inbox.read_text().splitlines()
        added = lines[lines.index("- [ ] buy milk") + 1]
        self.assertEqual(added, f"- [ ] (acme) answer Secret urgent tier: now ([thread]({REPLY}))")
        self.assertEqual(lines[lines.index(added) + 2], "## Later")
        self.assertEqual(records[KEY]["status"], "open")

    def test_open_task_on_the_same_thread_blocks_a_new_one(self):
        inbox = self.write("inbox.md", INBOX.replace("- [ ] buy milk", f"- [ ] reply to ana ([thread]({ROOT}))"))
        before = inbox.read_text()

        plan, applied = self.sweep({}, new=[{"permalink": REPLY, "tag": "t", "text": "reply", "reason": "r"}])

        self.assertEqual(applied["new"], [])
        self.assertIn(f"already open: {KEY}", plan.skipped)
        self.assertEqual(inbox.read_text(), before)

    def test_completion_marks_the_moved_sweep_task_and_leaves_a_manual_task_alone(self):
        other = "https://acme.slack.com/archives/C2/p1700000000000002"
        moved = self.write("areas/acme.md", f"## Tasks\n- [ ] (acme) answer ana ([thread]({REPLY})) [tier:: next]\n")
        inbox = self.write("inbox.md", INBOX.replace("- [ ] buy milk", f"- [ ] my own task ([thread]({other}))"))
        records = {KEY: {"status": "open", "trigger_ts": "1700000000.000500", "created_at": NOW - 60}}

        self.sweep(records, completed=[{"key": KEY, "evidence": "replied"}, {"key": "C2/1700000000.000002", "evidence": "x"}])

        self.assertIn(f"- [x] (acme) answer ana ([thread]({REPLY})) [tier:: next] [completion:: {TODAY}]", moved.read_text())
        self.assertIn("- [ ] my own task", inbox.read_text())
        self.assertEqual(records[KEY]["status"], "done")

    def test_an_obsidian_save_during_the_write_is_kept(self):
        inbox = self.write("inbox.md", INBOX)
        calls = []

        def transform(text):
            calls.append(text)
            if len(calls) == 1:
                inbox.write_text(text.replace("buy milk", "buy oat milk"))
            return vault.append_under(text, "## Untriaged", ["- [ ] new"])

        with mock.patch.object(vault.time, "sleep"):
            vault.update(inbox, transform)

        self.assertEqual(len(calls), 2)
        self.assertIn("- [ ] buy oat milk\n- [ ] new\n", inbox.read_text())

    def test_a_tool_without_a_deny_rule_is_reported(self):
        cfg = Config(vault=self.cfg)
        read, write = cfg.slack.read_tools[0], cfg.slack.write_tools[0]
        init = {
            "tools": [read, write, "mcp__notes__write_note", "StructuredOutput"],
            "mcp_servers": [{"name": "claude.ai Slack"}, {"name": "notes"}, {"name": "claude.ai Gmail"}],
        }
        rules = agent.deny_rules(cfg, init)
        self.assertEqual(agent.exposed(cfg, init, rules), [])

        late = {"tools": init["tools"] + ["mcp__late__delete_all"], "mcp_servers": init["mcp_servers"] + [{"name": "late"}]}
        self.assertEqual(agent.exposed(cfg, late, rules), ["mcp__late__delete_all", "late"])

    def test_a_check_that_lands_just_before_the_interval_runs(self):
        every = Schedule(every_hours=0.25, check_minutes=15)
        last = {"last_attempt_at": NOW}
        self.assertTrue(schedule.due(last, NOW + 900 - 2, every)[0])
        self.assertFalse(schedule.due(last, NOW + 600, every)[0])

if __name__ == "__main__":
    unittest.main()

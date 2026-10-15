import io
import json
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


class FakeClaude:
    def __init__(self, *events):
        self.stdin = io.StringIO()
        self.stdout = iter(json.dumps(e) for e in events)
        self.returncode = 0

    def poll(self):
        return 0

    def kill(self):
        pass

    def wait(self):
        return 0


def init(status, tools):
    return {"type": "system", "subtype": "init", "tools": tools,
            "mcp_servers": [{"name": "claude.ai Slack", "status": status}]}


def late_init(read, *servers):
    event = init("connected", [read] + [f"mcp__{s}__delete_all" for s in servers])
    event["mcp_servers"] += [{"name": s} for s in servers]
    return event


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

    def test_a_slack_tool_the_preflight_missed_is_still_denied(self):
        cfg = Config(vault=self.cfg)
        preflight = {"tools": list(cfg.slack.read_tools), "mcp_servers": [{"name": "claude.ai Slack"}]}
        rules = agent.deny_rules(cfg, preflight)
        real = {**preflight, "tools": preflight["tools"] + ["mcp__claude_ai_Slack__slack_read_canvas"]}
        self.assertEqual(agent.exposed(cfg, real, rules), [])

    def test_a_run_retries_while_the_slack_connector_is_pending(self):
        cfg = Config(vault=self.cfg)
        read = cfg.slack.read_tools[0]
        done = {"type": "result", "subtype": "success", "structured_output": {"notes": "swept"}}
        starts = [FakeClaude(init("pending", [])), FakeClaude(init("connected", [read]), done)]
        with mock.patch.object(agent, "preflight", return_value=init("connected", [read])), \
                mock.patch.object(agent.subprocess, "Popen", side_effect=starts) as popen, \
                mock.patch.object(agent.time, "sleep"):
            output, meta = agent.run(cfg, "sweep")
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(output["notes"], "swept")
        self.assertEqual(meta["slack_status"], "connected")

    def test_a_server_the_preflight_missed_is_denied_on_a_restart(self):
        cfg = Config(vault=self.cfg)
        read = cfg.slack.read_tools[0]
        done = {"type": "result", "subtype": "success", "structured_output": {"notes": "swept"}}
        late = late_init(read, "late")
        starts = [FakeClaude(late), FakeClaude(late, done)]
        with mock.patch.object(agent, "preflight", return_value=init("connected", [read])), \
                mock.patch.object(agent.subprocess, "Popen", side_effect=starts) as popen:
            output, meta = agent.run(cfg, "sweep")
        cmd = popen.call_args_list[1].args[0]
        denied = json.loads(cmd[cmd.index("--settings") + 1])["permissions"]["deny"]
        self.assertIn("mcp__late", denied)
        self.assertEqual(meta["added_rules"], ["mcp__late"])
        self.assertEqual(output["notes"], "swept")

    def test_a_server_still_exposed_after_the_restart_stops_the_run(self):
        cfg = Config(vault=self.cfg)
        read = cfg.slack.read_tools[0]
        starts = [FakeClaude(late_init(read, "late")), FakeClaude(late_init(read, "late", "later"))]
        with mock.patch.object(agent, "preflight", return_value=init("connected", [read])), \
                mock.patch.object(agent.subprocess, "Popen", side_effect=starts) as popen:
            with self.assertRaisesRegex(agent.AgentError, "tools without a deny rule: .*later"):
                agent.run(cfg, "sweep")
        self.assertEqual(popen.call_count, 2)

    def test_a_check_that_lands_just_before_the_interval_runs(self):
        every = Schedule(every_hours=0.25, check_minutes=15)
        last = {"last_attempt_at": NOW}
        self.assertTrue(schedule.due(last, NOW + 900 - 2, every)[0])
        self.assertFalse(schedule.due(last, NOW + 600, every)[0])

if __name__ == "__main__":
    unittest.main()

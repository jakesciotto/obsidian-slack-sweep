from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from string import Template

from .config import Config, claude_home, state_dir

PROMPT = Path(__file__).with_name("prompt.md")
CHANNELS_PER_SEARCH = 15

SCHEMA = {
    "type": "object",
    "properties": {
        "new_tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "permalink": {"type": "string"},
                    "tag": {"type": "string"},
                    "text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["permalink", "tag", "text", "reason"],
                "additionalProperties": False,
            },
        },
        "completed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "evidence": {"type": "string"}},
                "required": ["key", "evidence"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["new_tasks", "completed", "notes"],
    "additionalProperties": False,
}


class AgentError(Exception):
    def __init__(self, message: str, meta: dict | None = None):
        super().__init__(message)
        self.meta = meta or {}


def _local(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(ts))


def _sources(cfg: Config, channels: list[str], keywords: list[str]) -> str:
    uid = cfg.slack.user_id or "USER_ID"
    lines = []
    if "mentions" in cfg.slack.sources:
        lines.append(f'- Mentions: `keywords: ["<@{uid}>"]`.')
    if "dms" in cfg.slack.sources:
        lines.append(
            '- Direct messages: `filters: "is:dm"`, `channel_types: "im,mpim"`. A DM conversation needs action '
            "when its newest message is not from the person and asks for something."
        )
    if "threads" in cfg.slack.sources:
        lines.append(f'- Threads the person is in: `filters: "with:<@{uid}> is:thread"`. Ignore the person\'s own messages.')
    if "channels" in cfg.slack.sources and channels:
        lines.append(
            "- Watched channels: a message needs action when someone outside the team posted it and nobody from the "
            "team replied after it. Run each filter below as its own search. A repeated `in:` means OR."
        )
        for i in range(0, len(channels), CHANNELS_PER_SEARCH):
            chunk = " ".join(f"in:#{name}" for name in channels[i : i + CHANNELS_PER_SEARCH])
            lines.append(f'  - `filters: "{chunk}"`')
    if "keywords" in cfg.slack.sources and keywords:
        lines.append(
            "- Account keywords: run one search per keyword below, and pass `include_bots: true`. A hit needs action "
            "when the message is about that account and asks something of the person, reports a problem, an escalation, "
            "a billing or contract change, or a risk to the account. A keyword can also be a common word: skip hits that "
            "are not about the account. Skip hits in the watched channels, because their own search covers them."
        )
        lines += [f"  - `keywords: {json.dumps([word])}`" for word in keywords]
    return "\n".join(lines) if lines else "- No sources are enabled. Skip job 1."


def _team_rule(cfg: Config) -> str:
    domains = cfg.slack.team_email_domains
    if domains:
        listed = " or ".join(f"@{d.lstrip('@')}" for d in domains)
        return f"The team: everyone whose email ends in {listed}. The detailed search result shows each sender's email."
    return "The team: members of the person's own Slack workspace. External Slack Connect users are outside the team."


def _open_tasks(tasks: list[dict]) -> str:
    if not tasks:
        return "There are no open tasks."
    rows = ["Open tasks. Each row: keys | check | task line."]
    for task in tasks:
        keys = ", ".join(f"`{k}`" for k in task["keys"])
        rows.append(f"- {keys} | check: {'yes' if task['check'] else 'no'} | {task['task'][:300]}")
    return "\n".join(rows)


def prompt(cfg: Config, since: int, until: int, channels: list[str], keywords: list[str], tasks: list[dict]) -> str:
    source = Path(cfg.agent.prompt_file).expanduser() if cfg.agent.prompt_file else PROMPT
    user_id = cfg.slack.user_id or (
        "not configured. Read it from the description of the Slack search tool, which states the logged-in user id. "
        "USER_ID below means this id"
    )
    extra = cfg.agent.instructions.strip()
    return Template(source.read_text(encoding="utf-8")).safe_substitute(
        user_id=user_id,
        since=since,
        until=until,
        since_text=_local(since),
        until_text=_local(until),
        team_rule=_team_rule(cfg),
        sources=_sources(cfg, channels, keywords),
        style=cfg.agent.style,
        open_tasks=_open_tasks(tasks),
        instructions=f"\n## Guidance from the person\n\n{extra}\n" if extra else "",
    )


ISOLATION_ENV = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "MCP_TIMEOUT": "15000",
}
EXPECTED_BUILTINS = frozenset({"StructuredOutput"})
PREFLIGHT_SECONDS = 120


def auth(cfg: Config) -> dict:
    try:
        done = subprocess.run([cfg.agent.claude, "auth", "status", "--json"], capture_output=True, text=True, timeout=60)
        return json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def _server_prefix(name: str) -> str:
    return "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _tool_prefix(tool: str) -> str:
    return tool.rsplit("__", 1)[0]


def _user_plugins() -> list[str]:
    try:
        data = json.loads((claude_home() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(name for name, on in (data.get("enabledPlugins") or {}).items() if on)


def _base_settings() -> dict:
    settings: dict = {"disableAllHooks": True}
    plugins = _user_plugins()
    if plugins:
        settings["enabledPlugins"] = {name: False for name in plugins}
    return settings


def deny_rules(cfg: Config, init: dict) -> list[str]:
    allowed = set(cfg.slack.read_tools)
    kept = {_tool_prefix(t) for t in allowed}
    tools = [t for t in init.get("tools") or [] if t.startswith("mcp__")]
    servers = {_server_prefix(s.get("name", "")) for s in init.get("mcp_servers") or []}
    servers |= {_tool_prefix(t) for t in tools}
    extra = {t for t in tools if _tool_prefix(t) in kept and t not in allowed}
    return sorted(servers - kept) + sorted(extra | set(cfg.slack.write_tools))


def exposed(cfg: Config, init: dict, rules: list[str]) -> list[str]:
    denied, allowed = set(rules), set(cfg.slack.read_tools)
    kept = {_tool_prefix(t) for t in allowed}
    loose = [
        t for t in init.get("tools") or []
        if t not in allowed and t not in EXPECTED_BUILTINS and t not in denied and _tool_prefix(t) not in denied
    ]
    loose += [
        s.get("name", "") for s in init.get("mcp_servers") or []
        if _server_prefix(s.get("name", "")) not in denied | kept
    ]
    return loose


def _settings(cfg: Config, rules: list[str]) -> dict:
    return {**_base_settings(), "permissions": {"deny": rules}}


def command(cfg: Config, settings: dict | None) -> list[str]:
    a, s = cfg.agent, cfg.slack
    cmd = [
        a.claude,
        "-p",
        "--model", a.model,
        "--output-format", "stream-json",
        "--verbose",
        "--json-schema", json.dumps(SCHEMA),
        "--no-session-persistence",
        "--max-budget-usd", str(a.max_budget_usd),
        "--permission-mode", "dontAsk",
        "--tools", "",
    ]
    if settings is not None:
        cmd += ["--setting-sources", "user", "--settings", json.dumps(settings)]
    cmd += ["--allowedTools", *s.read_tools, "--disallowedTools", *s.write_tools]
    if a.effort:
        cmd += ["--effort", a.effort]
    return cmd + list(a.extra_args)


def _environment(cfg: Config) -> dict[str, str]:
    return {**os.environ, **(ISOLATION_ENV if cfg.agent.isolate else {})}


def preflight(cfg: Config) -> dict:
    cmd = [cfg.agent.claude, "-p", "--model", cfg.agent.model, "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--permission-mode", "dontAsk", "--tools", "",
           "--setting-sources", "user", "--settings", json.dumps(_base_settings())]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, cwd=state_dir(), env=_environment(cfg))
    except OSError as exc:
        raise AgentError(f"cannot start {cfg.agent.claude}: {exc}") from None
    timer = threading.Timer(PREFLIGHT_SECONDS, proc.kill)
    timer.start()
    try:
        try:
            proc.stdin.write("Reply with ok.")
            proc.stdin.close()
        except BrokenPipeError:
            pass
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                return event
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    raise AgentError("the preflight session ended before its init event")


def run(cfg: Config, text: str) -> tuple[dict, dict]:
    meta: dict = {}
    rules: list[str] = []
    settings = None
    if cfg.agent.isolate:
        rules = deny_rules(cfg, preflight(cfg))
        settings = _settings(cfg, rules)
        meta["deny_rules"] = len(rules)
    with tempfile.TemporaryFile(mode="w+") as stderr:
        try:
            proc = subprocess.Popen(
                command(cfg, settings), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True,
                cwd=state_dir(), env=_environment(cfg),
            )
        except OSError as exc:
            raise AgentError(f"cannot start {cfg.agent.claude}: {exc}") from None
        timer = threading.Timer(cfg.agent.timeout_minutes * 60, proc.kill)
        timer.start()
        try:
            try:
                proc.stdin.write(text)
                proc.stdin.close()
            except BrokenPipeError:
                pass
            result = _read_events(proc, cfg, meta, rules)
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            stderr.seek(0)
            meta["stderr"] = stderr.read()[-2000:]
    if result is None:
        raise AgentError(f"claude exited {proc.returncode} without a result: {meta['stderr'][-300:]}", meta)
    for field in ("total_cost_usd", "duration_ms", "num_turns", "subtype", "permission_denials"):
        meta[field] = result.get(field)
    if result.get("is_error") or result.get("subtype") != "success":
        raise AgentError(f"agent run failed ({result.get('subtype')}): {str(result.get('result'))[:300]}", meta)
    output = result.get("structured_output")
    if output is None:
        output = _salvage(result.get("result") or "")
    return _validate(output), meta


def _read_events(proc: subprocess.Popen, cfg: Config, meta: dict, rules: list[str]) -> dict | None:
    result = None
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            tools = event.get("tools") or []
            meta["mcp_servers"] = len(event.get("mcp_servers") or [])
            meta["slack_tools"] = sorted(t for t in tools if t in cfg.slack.read_tools)
            if not meta["slack_tools"]:
                proc.kill()
                raise AgentError("no Slack read tool is loaded; check that the claude.ai Slack connector is connected", meta)
            loose = exposed(cfg, event, rules) if cfg.agent.isolate else []
            if loose:
                proc.kill()
                meta["exposed"] = loose
                raise AgentError(f"tools without a deny rule: {', '.join(loose[:10])}", meta)
        elif event.get("type") == "result":
            result = event
    return result


def _salvage(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AgentError(f"agent returned no JSON: {text[:300]}")
    try:
        return json.loads(text[start : end + 1])
    except ValueError as exc:
        raise AgentError(f"agent returned invalid JSON: {exc}") from None


def _validate(output: dict) -> dict:
    if not isinstance(output, dict):
        raise AgentError("agent output is not an object")
    clean = {"new_tasks": [], "completed": [], "notes": str(output.get("notes") or "")}
    for item in output.get("new_tasks") or []:
        if isinstance(item, dict) and all(isinstance(item.get(k), str) for k in ("permalink", "tag", "text")):
            clean["new_tasks"].append({k: item.get(k) or "" for k in ("permalink", "tag", "text", "reason")})
    for item in output.get("completed") or []:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            clean["completed"].append({"key": item["key"], "evidence": str(item.get("evidence") or "")})
    return clean

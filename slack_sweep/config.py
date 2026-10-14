from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

APP = "obsidian-slack-sweep"
SOURCES = ("mentions", "dms", "threads", "channels", "keywords")
COMPLETE_SCOPES = ("created", "linked")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

_SLACK = "mcp__claude_ai_Slack__"
READ_TOOLS = tuple(
    _SLACK + name
    for name in (
        "slack_search_public_and_private",
        "slack_search_public",
        "slack_search_channels",
        "slack_search_users",
        "slack_read_channel",
        "slack_read_thread",
        "slack_read_user_profile",
    )
)
WRITE_TOOLS = tuple(
    _SLACK + name
    for name in (
        "slack_send_message",
        "slack_send_message_draft",
        "slack_schedule_message",
        "slack_create_canvas",
        "slack_update_canvas",
        "slack_read_canvas",
    )
)


class ConfigError(Exception):
    pass


def _xdg(variable: str, fallback: str) -> Path:
    base = os.environ.get(variable)
    return (Path(base) if base else Path.home() / fallback) / APP


def default_config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "config.toml"


def config_path() -> Path:
    override = os.environ.get("OBSIDIAN_SLACK_SWEEP_CONFIG")
    return Path(override).expanduser() if override else default_config_path()


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def claude_home() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


@dataclass(frozen=True)
class Vault:
    path: str = ""
    inbox: str = "Inbox.md"
    section: str = "## Inbox"
    scan: tuple[str, ...] = (".",)
    exclude: tuple[str, ...] = ()
    task_template: str = "- [ ] ({tag}) {text} ([thread]({link}))"
    completion_template: str = "[completion:: {date}]"
    complete: str = "created"

    @property
    def root(self) -> Path:
        return Path(self.path).expanduser()


@dataclass(frozen=True)
class Slack:
    user_id: str = ""
    sources: tuple[str, ...] = SOURCES
    channels: tuple[str, ...] = ()
    channels_command: str = ""
    keywords: tuple[str, ...] = ()
    keywords_command: str = ""
    team_email_domains: tuple[str, ...] = ()
    read_tools: tuple[str, ...] = READ_TOOLS
    write_tools: tuple[str, ...] = WRITE_TOOLS


@dataclass(frozen=True)
class Agent:
    claude: str = "claude"
    model: str = "claude-sonnet-5-5"
    effort: str = ""
    max_budget_usd: float = 1.0
    timeout_minutes: int = 15
    isolate: bool = True
    extra_args: tuple[str, ...] = ()
    prompt_file: str = ""
    style: str = "Start with a verb. Use at most 20 words. Say what to do and for whom."
    instructions: str = ""


@dataclass(frozen=True)
class Schedule:
    every_hours: float = 2.0
    check_minutes: int = 15
    idle_minutes: int = 10
    retry_minutes: int = 30
    first_lookback_hours: int = 24
    max_lookback_hours: int = 72
    label: str = APP


@dataclass(frozen=True)
class Config:
    vault: Vault
    slack: Slack = field(default_factory=Slack)
    agent: Agent = field(default_factory=Agent)
    schedule: Schedule = field(default_factory=Schedule)
    notify: bool = True
    path: Path | None = None


SECTIONS = {"vault": Vault, "slack": Slack, "agent": Agent, "schedule": Schedule}


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"no config at {path}; copy config.example.toml there and edit it") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    unknown = set(raw) - set(SECTIONS) - {"notify"}
    if unknown:
        raise ConfigError(f"unknown top-level keys: {', '.join(sorted(unknown))}")
    notify = raw.get("notify", True)
    if not isinstance(notify, bool):
        raise ConfigError("notify must be true or false")
    parts = {name: _section(cls, raw.get(name, {}), name) for name, cls in SECTIONS.items()}
    config = Config(notify=notify, path=path, **parts)
    _validate(config)
    return config


def _section(cls, table, name):
    if not isinstance(table, dict):
        raise ConfigError(f"[{name}] must be a table")
    known = {f.name: f.default for f in fields(cls)}
    unknown = set(table) - set(known)
    if unknown:
        raise ConfigError(f"[{name}] unknown keys: {', '.join(sorted(unknown))}")
    values = {}
    for key, value in table.items():
        default, where = known[key], f"[{name}] {key}"
        if isinstance(default, tuple):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigError(f"{where} must be a list of strings")
            value = tuple(value)
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"{where} must be true or false")
        elif isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{where} must be a whole number")
        elif isinstance(default, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{where} must be a number")
            value = float(value)
        elif not isinstance(value, str):
            raise ConfigError(f"{where} must be a string")
        values[key] = value
    return cls(**values)


def _validate(c: Config) -> None:
    v, s, a, sch = c.vault, c.slack, c.agent, c.schedule
    if not v.path:
        raise ConfigError("[vault] path is required")
    if not v.section.startswith("#"):
        raise ConfigError('[vault] section must be a markdown heading, such as "## Inbox"')
    if v.complete not in COMPLETE_SCOPES:
        raise ConfigError(f"[vault] complete must be one of {', '.join(COMPLETE_SCOPES)}")
    if "{link}" not in v.task_template:
        raise ConfigError("[vault] task_template must contain {link}: the sweep finds its tasks by their Slack link")
    try:
        sample = v.task_template.format(tag="t", text="x", link="L", date="2026-01-01")
        v.completion_template.format(date="2026-01-01")
    except (KeyError, IndexError, ValueError) as exc:
        raise ConfigError(f"[vault] a template uses an unknown field: {exc}") from None
    if not re.match(r"^\s*[-*+] \[ \] ", sample):
        raise ConfigError('[vault] task_template must start with an open task, such as "- [ ] "')
    unknown = set(s.sources) - set(SOURCES)
    if unknown:
        raise ConfigError(f"[slack] unknown sources: {', '.join(sorted(unknown))}; use {', '.join(SOURCES)}")
    if not s.read_tools:
        raise ConfigError("[slack] read_tools must name at least one tool")
    if a.effort and a.effort not in EFFORTS:
        raise ConfigError(f"[agent] effort must be one of {', '.join(EFFORTS)}")
    for where, value in (
        ("[agent] max_budget_usd", a.max_budget_usd),
        ("[agent] timeout_minutes", a.timeout_minutes),
        ("[schedule] every_hours", sch.every_hours),
        ("[schedule] check_minutes", sch.check_minutes),
        ("[schedule] idle_minutes", sch.idle_minutes),
        ("[schedule] retry_minutes", sch.retry_minutes),
        ("[schedule] first_lookback_hours", sch.first_lookback_hours),
        ("[schedule] max_lookback_hours", sch.max_lookback_hours),
    ):
        if value <= 0:
            raise ConfigError(f"{where} must be greater than 0")


def _listed(static: tuple[str, ...], command: str, label: str) -> tuple[list[str], list[str]]:
    values, warnings = list(static), []
    if command:
        try:
            done = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120, check=True)
            values += done.stdout.splitlines()
        except subprocess.CalledProcessError as exc:
            warnings.append(f"{label} exited {exc.returncode}: {exc.stderr.strip()[:200]}")
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"{label} failed: {exc}")
    return [v.strip() for v in values if v.strip()], warnings


def channels(s: Slack) -> tuple[list[str], list[str]]:
    names, warnings = _listed(s.channels, s.channels_command, "channels_command")
    return list(dict.fromkeys(n.lstrip("#") for n in names)), warnings


def keywords(s: Slack) -> tuple[list[str], list[str]]:
    words, warnings = _listed(s.keywords, s.keywords_command, "keywords_command")
    quoted = (f'"{w}"' if " " in w and not w.startswith('"') else w for w in words)
    return list(dict.fromkeys(quoted)), warnings

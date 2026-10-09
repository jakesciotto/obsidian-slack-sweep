from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, agent, config, schedule, sweep

ENTRY = Path(__file__).resolve().parent.parent / "bin" / config.APP


def _when(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "never"


def cmd_run(cfg: config.Config, args: argparse.Namespace) -> int:
    return sweep.run(cfg, now_flag=args.now, dry_run=args.dry_run)


def cmd_install(cfg: config.Config, args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print("install supports macOS launchd only; schedule `obsidian-slack-sweep run` with your own scheduler")
        return 2
    try:
        launcher, rebuilt, plist = schedule.install(cfg, args.python or sys.executable, str(ENTRY))
    except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return 1
    print(f"launch agent: {plist}")
    print(f"launcher:     {launcher}")
    if rebuilt:
        print()
        print("Give the launcher Full Disk Access, or it cannot read a vault under ~/Documents or iCloud:")
        print("  1. Open System Settings > Privacy & Security > Full Disk Access.")
        print("  2. Click +, press Cmd+Shift+G, and paste the launcher path above.")
        print("  3. Turn on the new entry.")
        print("A rebuilt launcher has a new signature, so macOS asks again after each rebuild.")
    return 0


def _login_line(cfg: config.Config) -> str:
    status = agent.auth(cfg)
    if not status.get("loggedIn"):
        return "NOT logged in; run `claude auth login`"
    if status.get("authMethod") != "claude.ai":
        return f"{status.get('email')} ({status.get('authMethod')}); claude.ai connectors need a claude.ai login"
    return f"{status.get('email')} (claude.ai)"


def cmd_uninstall(cfg: config.Config, args: argparse.Namespace) -> int:
    for path in schedule.uninstall(cfg.schedule.label):
        print(f"removed {path}")
    print(f"kept config and state: {cfg.path}, {config.state_dir()}")
    return 0


def cmd_status(cfg: config.Config, args: argparse.Namespace) -> int:
    state = sweep.load_state(config.state_dir() / "state.json")
    try:
        tasks = sweep.collect(cfg.vault)
        vault_line = f"{cfg.vault.root} (readable)"
    except PermissionError:
        tasks, vault_line = [], f"{cfg.vault.root} (NOT readable from this process)"
    except OSError as exc:
        tasks, vault_line = [], f"{cfg.vault.root} ({exc.strerror})"
    records = state["tasks"]
    open_count = sum(1 for r in records.values() if r.get("status") == "open")
    due, remaining = schedule.due(state, time.time(), cfg.schedule)
    rows = [
        ("config", str(cfg.path)),
        ("vault", vault_line),
        ("launchd", f"{cfg.schedule.label} ({'loaded' if schedule.loaded(cfg.schedule.label) else 'not loaded'})"),
        ("launcher", str(schedule.launcher_path()) + ("" if schedule.launcher_path().exists() else " (missing)")),
        ("claude login", _login_line(cfg)),
        ("last run", f"{_when(state.get('last_attempt_at'))} ({'ok' if state.get('last_attempt_ok', True) else 'failed'})"),
        ("swept to", _when(state.get("last_success_at"))),
        ("next run", "due when you are active" if due else f"in {int(remaining // 60)} min"),
        ("open tasks", f"{open_count} created by the sweep, {sum(1 for t in tasks if t.open)} open Slack-linked in scan"),
    ]
    if state.get("vault_blocked"):
        rows.append(("blocked", f"the launchd job cannot read the vault since {_when(state['vault_blocked'])}"))
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        print(f"{key.ljust(width)}  {value}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=config.APP, description="Sweep Slack for things that need you and keep Obsidian tasks current.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", type=Path, help=f"config file (default {config.default_config_path()})")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="sweep once if you are active and a sweep is due")
    run.add_argument("--now", action="store_true", help="skip the activity and interval checks")
    run.add_argument("--dry-run", action="store_true", help="print the changes; write nothing to the vault or state")
    run.set_defaults(func=cmd_run)
    install = sub.add_parser("install", help="build the launcher and load the launchd job (macOS)")
    install.add_argument("--python", help="interpreter the launcher starts (default: the one running this command)")
    install.set_defaults(func=cmd_install)
    sub.add_parser("uninstall", help="unload the launchd job and remove the launcher").set_defaults(func=cmd_uninstall)
    sub.add_parser("status", help="show the schedule, the last run and the open tasks").set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        cfg = config.load(args.config.expanduser() if args.config else None)
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return args.func(cfg, args)

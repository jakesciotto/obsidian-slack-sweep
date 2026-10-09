from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import agent, config, schedule, slack, vault
from .config import Config

KEEP_CLOSED_DAYS = 30
KEEP_RUNS = 300
LOG_LIMIT = 1_000_000
STATE_VERSION = 1


@dataclass
class Plan:
    new: list[tuple[str, str, dict]] = field(default_factory=list)
    done: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def collect(v: config.Vault) -> list[vault.Task]:
    return vault.scan(v.root, vault.markdown_files(v.root, v.scan, v.exclude))


def reconcile(records: dict, tasks: list[vault.Task], now: float) -> None:
    by_key: dict[str, list[vault.Task]] = defaultdict(list)
    for task in tasks:
        for key in task.keys:
            by_key[key].append(task)
    for key, record in records.items():
        if record.get("status") != "open":
            continue
        found = by_key.get(key, [])
        if any(t.open for t in found):
            continue
        record.update(status="checked" if found else "removed", closed_at=now)


def open_tasks(records: dict, tasks: list[vault.Task], scope: str) -> list[dict]:
    rows = []
    for task in tasks:
        if not task.open:
            continue
        mine = any(records.get(k, {}).get("status") == "open" for k in task.keys)
        rows.append({"keys": sorted(task.keys), "check": scope == "linked" or mine, "task": task.line.strip()})
    return rows


def plan(result: dict, tasks: list[vault.Task], records: dict, checkable: set[str], v: config.Vault, today: str, now: float) -> Plan:
    out = Plan()
    for item in result["completed"]:
        if item["key"] in checkable:
            out.done[item["key"]] = item["evidence"]
        else:
            out.skipped.append(f"completion ignored, not an open sweep task: {item['key']}")
    open_keys = {k for t in tasks if t.open for k in t.keys}
    for item in result["new_tasks"]:
        link = slack.parse(item["permalink"])
        key = link.key if link and link.ts else None
        if key is None:
            out.skipped.append(f"invalid permalink: {item['permalink'][:120]}")
            continue
        if key in open_keys or key in out.done or any(key == k for k, _, _ in out.new):
            out.skipped.append(f"already open: {key}")
            continue
        seen = records.get(key, {}).get("trigger_ts")
        if seen and float(link.ts) <= float(seen):
            out.skipped.append(f"already handled: {key}")
            continue
        line = vault.render(v.task_template, item["tag"], item["text"], link.url, today)
        out.new.append((key, line, {"status": "open", "created_at": now, "trigger_ts": link.ts, "link": link.url, "reason": item["reason"]}))
    return out


def apply(p: Plan, v: config.Vault, tasks: list[vault.Task], records: dict, today: str, now: float) -> dict:
    applied = {"new": [], "done": []}
    if p.new:
        lines = [line for _, line, _ in p.new]
        vault.update(v.root / v.inbox, lambda text: vault.append_under(text, v.section, lines))
        for key, line, record in p.new:
            records[key] = record
            applied["new"].append(line)
    if p.done:
        suffix = v.completion_template.format(date=today)
        marked: set[str] = set()

        def mark(text: str) -> str:
            updated, hit = vault.complete(text, set(p.done), suffix)
            marked.update(hit)
            return updated

        for name in sorted({t.file for t in tasks if t.open and t.keys & p.done.keys()}):
            vault.update(v.root / name, mark)
        for key in sorted(marked):
            record = records.setdefault(key, {"trigger_ts": None, "created_at": None})
            record.update(status="done", closed_at=now, evidence=p.done[key])
            applied["done"].append(key)
    return applied


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": STATE_VERSION, "tasks": {}}
    if data.get("version") != STATE_VERSION:
        raise ValueError(f"{path}: unsupported state version {data.get('version')}")
    data.setdefault("tasks", {})
    return data


def save_state(path: Path, data: dict, now: float) -> None:
    cutoff = now - KEEP_CLOSED_DAYS * 86400
    data["tasks"] = {
        k: r for k, r in data["tasks"].items() if r.get("status") == "open" or (r.get("closed_at") or now) >= cutoff
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def log(message: str) -> None:
    path = config.state_dir() / "sweep.log"
    if path.exists() and path.stat().st_size > LOG_LIMIT:
        os.replace(path, path.with_suffix(".log.1"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")
    if sys.stdout.isatty():
        print(message)


def record_run(data: dict) -> None:
    runs = config.state_dir() / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"{time.strftime('%Y%m%dT%H%M%S')}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    for old in sorted(runs.glob("*.json"))[:-KEEP_RUNS]:
        old.unlink()


def run(cfg: Config, now_flag: bool = False, dry_run: bool = False) -> int:
    directory = config.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with schedule.lock(directory / "lock"):
            return _run(cfg, now_flag, dry_run)
    except schedule.Busy:
        log("skip: another sweep holds the lock")
        return 0


def _run(cfg: Config, now_flag: bool, dry_run: bool) -> int:
    state_path = config.state_dir() / "state.json"
    state = load_state(state_path)
    now = time.time()
    if not now_flag:
        idle = schedule.idle_seconds()
        if idle is not None and idle > cfg.schedule.idle_minutes * 60:
            return 0
        is_due, _ = schedule.due(state, now, cfg.schedule)
        if not is_due:
            return 0
    try:
        os.listdir(cfg.vault.root)
    except PermissionError:
        return _blocked(cfg, state, state_path, now, dry_run)
    except FileNotFoundError:
        log(f"error: vault not found at {cfg.vault.root}")
        return 2
    state.pop("vault_blocked", None)

    since, until = schedule.window(state, now, cfg.schedule)
    tasks = collect(cfg.vault)
    records = state["tasks"]
    reconcile(records, tasks, now)
    names, warnings = config.channels(cfg.slack)
    rows = open_tasks(records, tasks, cfg.vault.complete)
    checkable = {k for row in rows if row["check"] for k in row["keys"]}
    run_record = {"since": since, "until": until, "channels": len(names), "warnings": warnings}

    try:
        result, meta = agent.run(cfg, agent.prompt(cfg, since, until, names, rows))
    except agent.AgentError as exc:
        run_record.update(error=str(exc), meta=exc.meta)
        log(f"error: {exc}")
        if dry_run:
            print(json.dumps(run_record, indent=1, default=str))
        else:
            failures = state.get("failures", 0) + 1
            state.update(last_attempt_at=now, last_attempt_ok=False, failures=failures)
            save_state(state_path, state, now)
            record_run(run_record)
            if failures == 1:
                notify(cfg, f"Sweep failed: {str(exc)[:120]}")
        return 1

    today = time.strftime("%Y-%m-%d", time.localtime(now))
    p = plan(result, tasks, records, checkable, cfg.vault, today, now)
    run_record.update(meta=meta, result=result, skipped=p.skipped)
    if meta.get("permission_denials"):
        log(f"warning: the agent tried tools it may not use: {meta['permission_denials']}")
    if dry_run:
        print(json.dumps({"window": [since, until], "new": [line for _, line, _ in p.new], "done": p.done,
                          "skipped": p.skipped, "notes": result["notes"], "cost_usd": meta.get("total_cost_usd")}, indent=1))
        return 0

    try:
        applied = apply(p, cfg.vault, tasks, records, today, now)
    except (vault.ConflictError, OSError, UnicodeDecodeError) as exc:
        log(f"error: vault write failed: {exc}")
        state.update(last_attempt_at=now, last_attempt_ok=False, failures=state.get("failures", 0) + 1)
        save_state(state_path, state, now)
        record_run({**run_record, "error": f"vault write failed: {exc}"})
        return 1
    state.update(last_attempt_at=now, last_attempt_ok=True, last_success_at=until, failures=0)
    save_state(state_path, state, now)
    run_record["applied"] = applied
    record_run(run_record)
    cost = meta.get("total_cost_usd")
    log(f"ok: {len(applied['new'])} new, {len(applied['done'])} done, {len(p.skipped)} skipped, cost {cost}")
    if applied["new"] or applied["done"]:
        notify(cfg, f"{len(applied['new'])} new, {len(applied['done'])} done")
    return 0


def _blocked(cfg: Config, state: dict, state_path: Path, now: float, dry_run: bool) -> int:
    launcher = config.data_dir() / config.APP
    message = f"cannot read the vault; give Full Disk Access to {launcher}"
    log(f"error: {message}")
    if not dry_run and not state.get("vault_blocked"):
        state["vault_blocked"] = now
        save_state(state_path, state, now)
        notify(cfg, "Cannot read the vault. Give Full Disk Access to the sweep launcher.")
    return 1


def notify(cfg: Config, message: str) -> None:
    if not cfg.notify or sys.platform != "darwin":
        return
    script = f"display notification {json.dumps(message, ensure_ascii=False)} with title \"Slack sweep\""
    schedule.quiet(["/usr/bin/osascript", "-e", script])

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import plistlib
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from . import config
from .config import Config

LAUNCHER_SOURCE = Path(__file__).with_name("launcher.c")
WINDOW_OVERLAP = 600
DUE_GRACE = 60


class Busy(Exception):
    pass


def quiet(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def idle_seconds() -> float | None:
    try:
        out = quiet(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"]).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(match[1]) / 1e9 if match else None


def due(state: dict, now: float, s: config.Schedule) -> tuple[bool, float]:
    last = state.get("last_attempt_at")
    if last is None:
        return True, 0.0
    wait = s.every_hours * 3600 if state.get("last_attempt_ok", True) else s.retry_minutes * 60
    remaining = last + wait - now
    return remaining <= DUE_GRACE, max(remaining, 0.0)


def window(state: dict, now: float, s: config.Schedule) -> tuple[int, int]:
    last = state.get("last_success_at")
    since = last - WINDOW_OVERLAP if last else now - s.first_lookback_hours * 3600
    return int(max(since, now - s.max_lookback_hours * 3600)), int(now)


@contextmanager
def lock(path: Path):
    with open(path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy() from None
        yield


def launcher_path() -> Path:
    return config.data_dir() / config.APP


def plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def build_launcher(python: str, entry: str) -> tuple[Path, bool]:
    out = launcher_path()
    stamp = out.with_name(out.name + ".sha256")
    digest = hashlib.sha256(LAUNCHER_SOURCE.read_bytes() + f"\0{python}\0{entry}".encode()).hexdigest()
    if out.exists() and stamp.exists() and stamp.read_text() == digest:
        return out, False
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["cc", "-O2", "-Wall", "-Wextra", "-Werror", f"-DSWEEP_PYTHON={json.dumps(python)}",
         f"-DSWEEP_ENTRY={json.dumps(entry)}", "-o", str(out), str(LAUNCHER_SOURCE)],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--identifier", config.APP, str(out)],
        check=True, capture_output=True,
    )
    stamp.write_text(digest)
    return out, True


def domain() -> str:
    return f"gui/{os.getuid()}"


def loaded(label: str) -> bool:
    return quiet(["launchctl", "print", f"{domain()}/{label}"]).returncode == 0


def install(cfg: Config, python: str, entry: str) -> tuple[Path, bool, Path]:
    label = cfg.schedule.label
    launcher, rebuilt = build_launcher(python, entry)
    states = config.state_dir()
    states.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")}
    if cfg.path and cfg.path != config.default_config_path():
        env["OBSIDIAN_SLACK_SWEEP_CONFIG"] = str(cfg.path)
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "CLAUDE_CONFIG_DIR"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    plist = {
        "Label": label,
        "ProgramArguments": [str(launcher), "run"],
        "StartInterval": cfg.schedule.check_minutes * 60,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "WorkingDirectory": str(states),
        "EnvironmentVariables": env,
        "StandardOutPath": str(states / "launchd.out"),
        "StandardErrorPath": str(states / "launchd.err"),
    }
    path = plist_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        plistlib.dump(plist, handle)
    unload(label)
    for attempt in range(10):
        done = quiet(["launchctl", "bootstrap", domain(), str(path)])
        if done.returncode == 0:
            break
        if attempt == 9:
            raise RuntimeError(f"launchctl bootstrap failed: {done.stderr.strip()}")
        time.sleep(0.5)
    return launcher, rebuilt, path


def unload(label: str) -> None:
    quiet(["launchctl", "bootout", f"{domain()}/{label}"])


def uninstall(label: str) -> list[Path]:
    unload(label)
    removed = []
    for path in (plist_path(label), launcher_path(), launcher_path().with_name(config.APP + ".sha256")):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import slack

TASK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[(?P<status>.)\]")
OPEN = frozenset(" /")
HEADING = re.compile(r"^(#{1,6})\s")
URL = re.compile(r"\w+://\S+")
UNSAFE = str.maketrans("", "", "[]<>|#")


class ConflictError(Exception):
    pass


@dataclass(frozen=True)
class Task:
    file: str
    line: str
    status: str
    keys: frozenset[str]

    @property
    def open(self) -> bool:
        return self.status in OPEN


def markdown_files(root: Path, scan: Iterable[str], exclude: Iterable[str]) -> list[Path]:
    base = root.resolve()
    skipped = [(base / entry).resolve() for entry in exclude]
    found: dict[Path, None] = {}
    for entry in scan:
        target = (base / entry).resolve()
        paths = [target] if target.is_file() else sorted(target.rglob("*.md")) if target.is_dir() else []
        for path in paths:
            relative = path.relative_to(base)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if any(path == s or s in path.parents for s in skipped):
                continue
            found[path] = None
    return list(found)


def scan(root: Path, files: Iterable[Path]) -> list[Task]:
    base, tasks = root.resolve(), []
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = TASK.match(line)
            if not match:
                continue
            keys = frozenset(k for link in slack.find(line) if (k := link.key))
            if keys:
                tasks.append(Task(str(path.relative_to(base)), line, match["status"], keys))
    return tasks


def clean(value: str, limit: int) -> str:
    value = URL.sub("", value).translate(UNSAFE).replace("::", ":").replace("%%", "%")
    return " ".join(value.split())[:limit].rstrip()


def render(template: str, tag: str, text: str, link: str, date: str) -> str:
    return template.format(tag=clean(tag, 40), text=clean(text, 240), link=link, date=date)


def append_under(content: str, heading: str, lines: list[str]) -> str:
    if not lines:
        return content
    newline = "\r\n" if "\r\n" in content else "\n"
    block = [line + newline for line in lines]
    title = heading.strip()
    level = len(title) - len(title.lstrip("#"))
    rows = content.splitlines(keepends=True)
    start = next((i for i, row in enumerate(rows) if row.strip() == title), None)
    if start is None:
        body = content.rstrip("\r\n")
        return (body + newline * 2 if body else "") + title + newline + "".join(block)
    end = len(rows)
    for i in range(start + 1, len(rows)):
        match = HEADING.match(rows[i])
        if match and len(match[1]) <= level:
            end = i
            break
    at = end
    while at > start + 1 and not rows[at - 1].strip():
        at -= 1
    if not rows[at - 1].endswith(("\n", "\r")):
        rows[at - 1] += newline
    rows[at:at] = block
    return "".join(rows)


def complete(content: str, keys: set[str], suffix: str) -> tuple[str, set[str]]:
    rows = content.splitlines(keepends=True)
    hit: set[str] = set()
    for i, row in enumerate(rows):
        match = TASK.match(row)
        if not match or match["status"] not in OPEN:
            continue
        matched = {link.key for link in slack.find(row)} & keys
        if not matched:
            continue
        body = row.rstrip("\r\n")
        ending = row[len(body):]
        body = body[: match.start("status")] + "x" + body[match.end("status"):]
        rows[i] = f"{body.rstrip()} {suffix}{ending}"
        hit |= matched
    return "".join(rows), hit


def update(path: Path, transform: Callable[[str], str], attempts: int = 5, pause: float = 0.5) -> bool:
    for attempt in range(attempts):
        before = _read(path)
        text = before.decode("utf-8") if before is not None else ""
        after = transform(text)
        if after == text:
            return False
        if _read(path) == before:
            _write(path, after.encode("utf-8"))
            return True
        if attempt + 1 < attempts:
            time.sleep(pause)
    raise ConflictError(f"{path} changed during every one of {attempts} write attempts")


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write(path: Path, data: bytes) -> None:
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, mode)
    os.replace(temp, path)

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs

LINK = re.compile(
    r"https://(?P<host>[a-z0-9-]+(?:\.[a-z0-9-]+)*\.slack\.com)/archives/(?P<channel>[A-Z0-9]+)"
    r"(?:/p(?P<sec>\d{10})(?P<usec>\d{6}))?(?:\?(?P<query>[\w.=&%-]*))?"
)
TS = re.compile(r"\d{10}\.\d{6}")


@dataclass(frozen=True)
class Permalink:
    host: str
    channel: str
    ts: str | None
    thread_ts: str | None

    @property
    def key(self) -> str | None:
        root = self.thread_ts or self.ts
        return f"{self.channel}/{root}" if root else None

    @property
    def url(self) -> str:
        url = f"https://{self.host}/archives/{self.channel}"
        if self.ts:
            url += "/p" + self.ts.replace(".", "")
            if self.thread_ts and self.thread_ts != self.ts:
                url += f"?thread_ts={self.thread_ts}&cid={self.channel}"
        return url


def _permalink(match: re.Match) -> Permalink:
    ts = f"{match['sec']}.{match['usec']}" if match["sec"] else None
    thread = parse_qs(match["query"] or "").get("thread_ts", [None])[0]
    if thread and not TS.fullmatch(thread):
        thread = None
    return Permalink(match["host"], match["channel"], ts, thread)


def find(text: str) -> list[Permalink]:
    return [_permalink(m) for m in LINK.finditer(text)]


def parse(url: str) -> Permalink | None:
    match = LINK.fullmatch(url.strip())
    return _permalink(match) if match else None

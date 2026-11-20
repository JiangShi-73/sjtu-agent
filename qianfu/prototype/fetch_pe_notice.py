#!/usr/bin/env python3
"""物理实验选课系统 通知抓取 —— 信鸽 SJTU

该站的通知列表页与详情页**无需登录**，可直接抓取。

用法:
    python fetch_pe_notice.py
"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from pathlib import Path

BASE = "http://111.186.40.251/pe"
LIST = f"{BASE}/noticeb.aspx?ctype=1"
STORE = Path(__file__).with_name("pe_seen.json")

RE_ID = re.compile(r"notice\.aspx\?id=(\d+)")
RE_TITLE = re.compile(r"Label6_\d+\">(.*?)</span>", re.S)
RE_DATE = re.compile(r"（\s*(\d{4}-\d{2}-\d{2})\s*）")
RE_SUMMARY = re.compile(r"Label4_\d+\">(.*?)</span>", re.S)


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (WebNotify/1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def parse(page: str) -> list[dict]:
    ids = RE_ID.findall(page)
    titles = RE_TITLE.findall(page)
    dates = RE_DATE.findall(page)
    summaries = RE_SUMMARY.findall(page)

    items = []
    for i, nid in enumerate(ids):
        items.append(
            {
                "id": nid,
                "title": clean(titles[i]) if i < len(titles) else "",
                "date": dates[i] if i < len(dates) else "",
                "summary": clean(summaries[i]) if i < len(summaries) else "",
                "url": f"{BASE}/notice.aspx?id={nid}",
            }
        )
    return items


def render(it: dict) -> str:
    lines = [f"【物理实验】{it['title']}"]
    lines.append(f"🗓 {it['date']}    🔗 {it['url']}")
    if it["summary"]:
        lines.append(it["summary"][:80] + ("…" if len(it["summary"]) > 80 else ""))
    return "\n".join(lines)


def main() -> None:
    items = parse(fetch(LIST))

    seen: list[str] = []
    if STORE.exists():
        seen = json.loads(STORE.read_text(encoding="utf-8"))

    fresh = [it for it in items if it["id"] not in seen]
    print(f"列表 {len(items)} 条，新增 {len(fresh)} 条\n")
    for it in fresh:
        print(render(it))
        print("-" * 48)

    seen.extend(it["id"] for it in items)
    STORE.write_text(json.dumps(sorted(set(seen)), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

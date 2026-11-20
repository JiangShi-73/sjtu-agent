#!/usr/bin/env python3
"""第二课堂活动抓取原型 —— 信鸽 SJTU

用法:
    PowerShell:  $env:SJTU_API_KEY="<你的 key>"; python fetch_second_classroom.py
    cmd:         set SJTU_API_KEY=<你的 key> && python fetch_second_classroom.py

流程: 拉取 -> 归一化 -> 按内容指纹合并 -> 与历史比对 -> 渲染成 QQ 推送卡片
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

API = "https://agentsquare.sjtu.edu.cn/api/open/second-classroom"
STORE = Path(__file__).with_name("seen.json")


def fetch() -> list[dict]:
    key = os.environ.get("SJTU_API_KEY", "").strip()
    if not key:
        sys.exit("缺少 SJTU_API_KEY 环境变量")
    req = urllib.request.Request(API, headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if not payload.get("success"):
        sys.exit(f"接口返回失败: {payload}")
    return payload["data"]


def fingerprint(item: dict) -> str:
    """接口没有 id 字段，用"名称+开始时间+主办方"作为活动身份。

    注意：不能把 signupEndTime 放进指纹——同一活动"报名延期"后，
    除报名时间外所有字段都相同，放进去会被当成两个活动重复推送。
    """
    raw = "|".join(
        [
            (item.get("name") or "").strip(),
            item.get("startTime") or "",
            (item.get("sponsor") or "").strip(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize(item: dict) -> dict:
    return {
        "fp": fingerprint(item),
        "title": (item.get("name") or "").strip(),
        "type": item.get("activityTypeName") or "",
        "status": item.get("status") or "",
        "start": item.get("startTime") or "",
        "end": item.get("endTime") or "",
        "place": item.get("address") or "",
        "sponsor": item.get("sponsor") or "",
        "signup_end": item.get("signupEndTime") or "",
        "signup_method": item.get("signupMethod") or "",
        "intro": (item.get("introduction") or "").replace("\n", " ").strip(),
    }


def render(it: dict) -> str:
    lines = [f"【第二课堂 · {it['type']}】{it['title']}"]
    lines.append(f"🕐 {it['start'][5:16]}    📍 {it['place']}")
    if it["signup_end"]:
        lines.append(f"📝 报名截止 {it['signup_end'][5:16]}（{it['signup_method']}）")
    if it["intro"]:
        lines.append(it["intro"][:60] + ("…" if len(it["intro"]) > 60 else ""))
    return "\n".join(lines)


def main() -> None:
    raw = fetch()
    items = [normalize(x) for x in raw]

    # 批内合并：同一活动若因"报名延期"出现多条，保留报名截止最晚的那条
    merged: dict[str, dict] = {}
    for it in items:
        cur = merged.get(it["fp"])
        if cur is None or (it["signup_end"] or "") > (cur["signup_end"] or ""):
            merged[it["fp"]] = it
    items = list(merged.values())

    seen: dict[str, str] = {}
    if STORE.exists():
        seen = json.loads(STORE.read_text(encoding="utf-8"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fresh = [
        it
        for it in items
        if it["fp"] not in seen and it["status"] != "已结束" and it["start"] >= now
    ]

    print(
        f"接口返回 {len(raw)} 条 -> 合并后 {len(items)} 条 -> 新增未开始 {len(fresh)} 条\n"
    )
    for it in sorted(fresh, key=lambda x: x["start"])[:5]:
        print(render(it))
        print("-" * 48)

    for it in items:
        seen.setdefault(it["fp"], it["title"])
    STORE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已记录 {len(seen)} 个指纹 -> {STORE.name}")


if __name__ == "__main__":
    main()

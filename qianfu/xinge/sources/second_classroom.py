"""第二课堂活动。

走学校开放数据接口，用 X-Api-Key 鉴权，和 JAccount 无关。

注意：接口返回里**没有 id 字段**，只能用内容指纹去重；
同一活动"报名延期"后除报名时间外所有字段都相同，所以要按指纹合并、
保留报名截止最晚的那条。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import httpx

from ..core import FetchReport, Item, register_source

API = "https://agentsquare.sjtu.edu.cn/api/open/second-classroom"
PORTAL = "https://dekt.sjtu.edu.cn/"      # 第二课堂平台首页


def _fingerprint(raw: dict) -> str:
    key = "|".join(
        [
            (raw.get("name") or "").strip(),
            raw.get("startTime") or "",
            (raw.get("sponsor") or "").strip(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@register_source
class SecondClassroomSource:
    key = "second_classroom"
    name = "第二课堂活动"
    auth = None                     # 用 API Key，不走 JAccount

    def __init__(self, session=None, api_key: str | None = None) -> None:
        import os

        from ..auth import SessionStore

        self.session = session or SessionStore()
        self.api_key = api_key or os.environ.get("SJTU_API_KEY", "")

    def fetch(self) -> FetchReport:
        started = datetime.now()
        report = FetchReport(source=self.key)
        if not self.api_key:
            report.ok = False
            report.error = "缺少 SJTU_API_KEY 环境变量"
            return report

        try:
            with httpx.Client(follow_redirects=True, timeout=20) as client:
                resp = client.get(API, headers={"X-Api-Key": self.api_key})
            if resp.status_code != 200:
                report.ok = False
                report.error = f"HTTP {resp.status_code}"
                return report
            payload = resp.json()
            if not payload.get("success"):
                report.ok = False
                report.error = str(payload.get("message"))
                return report

            # 批内合并：同一活动保留报名截止最晚的那条
            merged: dict[str, dict] = {}
            for raw in payload.get("data") or []:
                fp = _fingerprint(raw)
                old = merged.get(fp)
                if old is None or (raw.get("signupEndTime") or "") > (
                    old.get("signupEndTime") or ""
                ):
                    merged[fp] = raw

            for fp, raw in merged.items():
                report.items.append(
                    Item(
                        source=self.key,
                        uid=fp,
                        title=(raw.get("name") or "").strip(),
                        # displayUrl 目前恒为 null，退回平台首页
                        url=(raw.get("displayUrl") or "").strip() or PORTAL,
                        summary=(raw.get("introduction") or "").replace("\n", " ")[:200],
                        category=raw.get("activityTypeName") or "",
                        published=_parse(raw.get("startTime")),
                        deadline=_parse(raw.get("signupEndTime")),
                        extra={
                            "board": "event",
                            "place": raw.get("address") or "",
                            "sponsor": raw.get("sponsor") or "",
                            "status": raw.get("status") or "",
                            "signup": raw.get("signupMethod") or "",
                            "signup_start": raw.get("signupStartTime") or "",
                            "signup_end": raw.get("signupEndTime") or "",
                            "start": raw.get("startTime") or "",
                            "end": raw.get("endTime") or "",
                            "presenter": raw.get("presenter") or "",
                            "presenter_bio": raw.get("presenterBio") or "",
                            "person_num": raw.get("personNum") or "",
                            "introduction": raw.get("introduction") or "",
                        },
                    )
                )
        except (httpx.HTTPError, ValueError) as exc:
            report.ok = False
            report.error = str(exc)

        # 补详情页地址：开放接口没有 id，得去活动平台要。
        # 拿不到就算了，不影响列表本身。
        if report.items:
            try:
                from .activity_portal import activity_index

                index = activity_index(self.session, {i.title for i in report.items})
                hit = 0
                for item in report.items:
                    found = index.get(item.title)
                    if found:
                        item.url = found["url"]
                        item.extra.update(found.get("props") or {})
                        hit += 1
                if hit:
                    report.error = (report.error + " " if report.error else "") + f"详情页 {hit}/{len(report.items)}"
            except Exception:
                pass

        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

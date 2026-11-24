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

        # 首选：直接从活动平台拿（带 jAccount 就能读，不需要 API Key）
        # 好处是有 id、能拼详情页，还带活动时间/报名时间/已报名人数
        try:
            from .activity_portal import detail_url, fetch_activities

            activities = fetch_activities(self.session)
        except Exception:                          # noqa: BLE001
            activities = []

        if activities:
            for raw in activities:
                name = (raw.get("name") or "").strip()
                if not name:
                    continue
                act_time = raw.get("activity_time") or ["", ""]
                sign_time = raw.get("registration_time") or ["", ""]
                try:
                    aid = int(raw.get("id"))
                except (TypeError, ValueError):
                    aid = str(raw.get("id") or "")

                report.items.append(
                    Item(
                        source=self.key,
                        uid=f"portal-{aid}",
                        title=name,
                        url=detail_url(aid),
                        summary="",
                        category="第二课堂",
                        published=_parse(act_time[0] if act_time else ""),
                        deadline=_parse(sign_time[1] if len(sign_time) > 1 else ""),
                        extra={
                            "board": "event",
                            "place": raw.get("address") or "",
                            "sponsor": raw.get("sponsor") or "",
                            "presenter": raw.get("presenter") or "",
                            "start": act_time[0] if act_time else "",
                            "end": act_time[1] if len(act_time) > 1 else "",
                            "signup_start": sign_time[0] if sign_time else "",
                            "signup_end": sign_time[1] if len(sign_time) > 1 else "",
                            "signed_up": raw.get("signed_up_num"),
                            "capacity": raw.get("person_num"),
                            "image": raw.get("img") or "",
                            "detail_id": aid,
                        },
                    )
                )

            report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
            return report

        # 退回：开放数据接口（需要 X-Api-Key，信息比平台少）
        report = self._fetch_open_api(started)
        if not report.ok and not report.items:
            report.error = (
                "读不到第二课堂活动：jAccount 没登录，或活动平台换不到令牌。"
                "先在「订阅」里点「全部登录一遍」。"
            )
        return report

    def _fetch_open_api(self, started) -> FetchReport:
        report = FetchReport(source=self.key)
        if not self.api_key:
            report.ok = False
            report.error = "活动平台读不到，且没有 SJTU_API_KEY"
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

        report.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return report


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

"""校园活动平台（activity.sjtu.edu.cn）。

学校开放数据接口（/api/open/second-classroom）没有 id，拿不到活动详情页。
真正的平台有这个信息，但要登录：SSO 之后拿一个 JWT（IMPSA-Token），
带着它请求 /api/v1/activity/list 就能拿到每条活动的 id。

详情页地址的 id 是 base64(JSON 字符串 + 两个空格)：
    19180 -> "19180"  -> IjE5MTgwIiAg
    https://activity.sjtu.edu.cn/activity/detail/IjE5MTgwIiAg?activity_path=/dekt

token 有效期 24 小时，缓存在 ~/.xinge/impsa.json，过期了再用浏览器换一次。
"""

from __future__ import annotations

import base64
import json
import os
import time

import httpx

from ..auth import SessionStore

HOST = "https://activity.sjtu.edu.cn"
API = f"{HOST}/api/v1"
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".xinge", "impsa.json")
TYPE_SECOND_CLASSROOM = 2          # activity_type_id=2 就是第二课堂


def detail_url(activity_id) -> str:
    token = base64.b64encode((json.dumps(str(activity_id)) + "  ").encode()).decode()
    return f"{HOST}/activity/detail/{token}?activity_path=/dekt"


# ---------------- token ----------------

def _read_token() -> str:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ""
    if data.get("exp", 0) < time.time() + 600:      # 留 10 分钟余量
        return ""
    return data.get("token") or ""


def _write_token(token: str) -> None:
    exp = 0
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
    except (IndexError, ValueError):
        pass
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        json.dump({"token": token, "exp": exp}, fh)


def _token_from_browser(session: SessionStore) -> str:
    """用本机浏览器走一次 SSO，把 IMPSA-Token 取出来。"""
    try:
        from playwright.sync_api import sync_playwright

        from ..browser import launch
    except ImportError:
        return ""

    cookies = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
        for c in session._jar.jar
        if "jaccount" in c.domain or "activity" in c.domain
    ]
    if not cookies:
        return ""

    with sync_playwright() as p:
        try:
            browser = launch(p, headless=True)
        except RuntimeError:
            return ""

        try:
            ctx = browser.new_context()
            ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.goto(f"{HOST}/dekt?locale=zh", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            for c in ctx.cookies():
                if c["name"] == "IMPSA-Token" and c["value"]:
                    _write_token(c["value"])
                    return c["value"]
        except Exception:
            return ""
        finally:
            browser.close()
    return ""


def get_token(session: SessionStore, refresh: bool = False) -> str:
    if not refresh:
        cached = _read_token()
        if cached:
            return cached
    return _token_from_browser(session)


# ---------------- 活动 ----------------

def fetch_activities(session: SessionStore, max_pages: int = 6) -> list[dict]:
    """从平台拿第二课堂活动（带 id）。拿不到就返回空列表，不影响主流程。"""
    token = get_token(session)
    if not token:
        return []

    headers = {"Authorization": f"Bearer {token}"}
    out: list[dict] = []

    with httpx.Client(verify=False, timeout=25, headers=headers) as client:
        for page in range(1, max_pages + 1):
            try:
                resp = client.get(
                    f"{API}/activity/list/home",
                    params={
                        "page": page,
                        "per_page": 50,
                        "activity_type_id": TYPE_SECOND_CLASSROOM,
                        "time_sort": "desc",
                    },
                )
            except httpx.HTTPError:
                break

            if resp.status_code == 401:          # token 过期，换一次再试
                token = get_token(session, refresh=True)
                if not token:
                    break
                client.headers["Authorization"] = f"Bearer {token}"
                continue
            if resp.status_code != 200:
                break

            try:
                data = resp.json().get("data") or []
            except ValueError:
                break
            if not data:
                break

            out.extend(data)
            if len(data) < 50:
                break

    return out


def fetch_detail(client: httpx.Client, activity_id) -> dict:
    """单条活动详情（带 custom_properties 的那部分）。"""
    try:
        resp = client.get(f"{API}/activity/{activity_id}", params={"detail": 1})
        if resp.status_code != 200:
            return {}
        return (resp.json().get("data") or {}).get("base_info") or {}
    except (httpx.HTTPError, ValueError):
        return {}


def fetch_properties(session: SessionStore, activity_ids) -> dict:
    """批量拿活动属性：同一个 token + 一个连接，避免每条活动重新登录。

    列表接口没有 custom_properties，只能逐条拉详情。token 过期（401）时
    换一次再重试；单条拿不到（活动被删/下架）直接跳过，不抛异常。

    Args:
        session: 登录态存储（只有换 token 时才需要浏览器）。
        activity_ids: 平台活动 id 列表。

    Returns:
        {activity_id: {"labor_counted", "labor_hours", "new_era", "duration"}}
    """
    ids: list[int] = []
    for aid in activity_ids or []:
        try:
            ids.append(int(aid))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}

    token = get_token(session)
    if not token:
        return {}

    headers = {"Authorization": f"Bearer {token}"}
    out: dict[int, dict] = {}
    with httpx.Client(verify=False, timeout=20, headers=headers) as client:
        refreshed = False
        for aid in ids:
            base = fetch_detail(client, aid)
            if not base and not refreshed:
                # 多半是 token 过期：整批只换一次，别每条都开浏览器
                token = get_token(session, refresh=True)
                if token:
                    client.headers["Authorization"] = f"Bearer {token}"
                    refreshed = True
                    base = fetch_detail(client, aid)
            if not base:
                continue
            out[aid] = parse_properties(base)
    return out


def parse_properties(base: dict) -> dict:
    """把 custom_properties 里的属性摊平。

    平台用 JSON 字符串存活动属性，目前只有三项：
        是否计入劳动学时 / 劳动学时 / 是否计入新时代社会认知实践讲座课
    """
    raw = base.get("custom_properties")
    props: dict = {}
    if raw:
        try:
            props = json.loads(raw)
        except (TypeError, ValueError):
            props = {}

    hours = props.get("劳动学时")
    try:
        hours = float(hours) if hours not in (None, "") else 0.0
    except (TypeError, ValueError):
        hours = 0.0

    return {
        "labor_counted": props.get("是否计入劳动学时") == "是",
        "labor_hours": hours,
        "new_era": props.get("是否计入新时代社会认知实践讲座课") == "是",
        "duration": base.get("duration"),
    }


def activity_index(session: SessionStore, names: set[str] | None = None) -> dict[str, dict]:
    """活动名 -> 活动（含 id、详情页地址、属性）。

    只对 names 里出现过的活动再拉一次详情，避免几百次请求。
    """
    activities = fetch_activities(session)
    if not activities:
        return {}

    token = get_token(session)
    headers = {"Authorization": f"Bearer {token}"}
    out: dict[str, dict] = {}

    with httpx.Client(verify=False, timeout=20, headers=headers) as client:
        for a in activities:
            name = (a.get("name") or "").strip()
            if not name or not a.get("id"):
                continue
            if names is not None and name not in names:
                continue

            base = fetch_detail(client, a["id"])
            out[name] = {
                **a,
                "url": detail_url(a["id"]),
                "props": parse_properties(base) if base else {},
            }

    return out

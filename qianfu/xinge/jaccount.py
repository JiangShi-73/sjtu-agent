"""jAccount 单点登录。

信鸽只需要**一次** jAccount 登录（Canvas 扫码那次），
之后所有走 jAccount 的站都能用同一个会话直接进去：

    带着 jaccount.sjtu.edu.cn 的 cookie 请求目标站
      → 目标站 302 到 jaccount.sjtu.edu.cn/jaccount/url?sid=<站>
      → JAAuthCookie 有效，jAccount 直接放行
      → 302 回目标站，带 ticket
      → 目标站回写自己的会话 cookie

不用再输密码，也不用弹浏览器。

新增一个站只要在 SITES 里加一行：

    "library": {
        "url": "https://lib.sjtu.edu.cn/",
        "check": ["SESSION", "JSESSIONID"],   # 出现任意一个就算登录成功
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HOME = Path(os.path.expanduser("~")) / ".xinge"
SESSION_PATH = HOME / "session.json"

# 站名 → 入口地址和登录成功的标志 cookie
#
# method:
#   sso   —— 带着 jAccount 会话走一次重定向就能进
#   token —— 站自己发令牌（OAuth / JWT），SSO 只能证明身份，还得走它自己的接口
SITES: dict[str, dict] = {
    "shuiyuan": {
        "name": "水源社区",
        "url": "https://shuiyuan.sjtu.edu.cn/",
        "check": ["_t"],
        "method": "sso",
    },
    "cnmooc": {
        "name": "好大学在线",
        "url": "https://cnmooc.sjtu.cn/",
        "check": ["moocsk", "moocvk"],
        "method": "sso",
    },
    "pe": {
        "name": "体育",
        "url": "https://pe.sjtu.edu.cn/",
        "check": ["JSESSIONID"],
        "method": "sso",
    },
    "canvas": {
        "name": "Canvas",
        "url": "https://oc.sjtu.edu.cn/",
        # Canvas 不用 cookie 会话，直接拿 jAccount 会话调它的 API 验
        "check": [],
        "probe": "https://oc.sjtu.edu.cn/api/v1/users/self",
        "method": "sso",
        "note": "和 jAccount 一起生效",
    },
    "activity": {
        "name": "校园活动",
        "url": "https://activity.sjtu.edu.cn/",
        "check": ["IMPSA-Token"],
        "method": "sso",
        "note": "用 jAccount 自动换 JWT，不用手动填令牌",
    },
}


class JAccountError(RuntimeError):
    pass


def cookies() -> dict[str, str]:
    """从 Canvas 扫码登录留下的会话里取 jAccount 的 cookie。"""
    if not SESSION_PATH.exists():
        return {}
    try:
        records = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    jar: dict[str, str] = {}
    for record in records:
        if isinstance(record, dict) and record.get("domain") == "jaccount.sjtu.edu.cn":
            jar[record.get("name")] = record.get("value")
    return jar


def has_session() -> bool:
    return bool(cookies().get("JAAuthCookie"))


def site_file(site: str) -> Path:
    return HOME / f"{site}.json"


def load_site(site: str) -> dict:
    path = site_file(site)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_site(site: str, jar: dict) -> None:
    site_file(site).parent.mkdir(parents=True, exist_ok=True)
    site_file(site).write_text(json.dumps(jar, ensure_ascii=False), encoding="utf-8")


def _ok(site: str, jar: dict) -> bool:
    checks = SITES.get(site, {}).get("check") or []
    if checks and any(jar.get(name) for name in checks):
        return True
    probe = SITES.get(site, {}).get("probe")
    if probe:
        return _probe(probe)
    return False


def _probe(url: str) -> bool:
    """用完整会话（含各站自己种的 cookie）请求一次，200 就算登录成功。

    Canvas 这类站靠的是 oc.sjtu.edu.cn 自己的 cookie，
    jAccount 那几个 cookie 单独拿过去调不通。
    """
    if not cookies().get("JAAuthCookie"):
        return False
    try:
        from .auth import SessionStore

        resp = SessionStore().client().get(url, timeout=20)
        return resp.status_code == 200
    except Exception:                             # noqa: BLE001
        try:
            with httpx.Client(
                headers={"User-Agent": UA}, timeout=15, follow_redirects=True
            ) as client:
                for name, value in cookies().items():
                    client.cookies.set(name, value, domain="jaccount.sjtu.edu.cn")
                return client.get(url).status_code == 200
        except Exception:                         # noqa: BLE001
            return False


def login(site: str) -> dict:
    """带着 jAccount 会话走一次 SSO，拿到目标站的 cookie 并存下来。

    Args:
        site: SITES 里的站名。

    Returns:
        {"ok": True, "site": ..., "cookies": 数量} 或 {"ok": False, "error": ...}
    """
    if site not in SITES:
        return {"ok": False, "error": f"不认识的站：{site}"}

    if SITES[site].get("method") == "token":
        return {
            "ok": False,
            "error": SITES[site].get("note") or "这个站用自己的令牌，不支持 SSO",
        }

    jar = cookies()
    if not jar.get("JAAuthCookie"):
        return {"ok": False, "error": "还没有 jAccount 登录态，先在信鸽里扫码登录一次"}

    target = SITES[site]["url"]
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as client:
        for name, value in jar.items():
            client.cookies.set(name, value, domain="jaccount.sjtu.edu.cn")

        resp = client.get(target)
        site_cookies = {
            c.name: c.value
            for c in client.cookies.jar
            if c.domain and "jaccount" not in c.domain
        }

    # 有 probe 的站（比如 Canvas）用 API 判断，不看 cookie
    if SITES[site].get("probe"):
        save_site(site, site_cookies)
        if _probe(SITES[site]["probe"]):
            return {"ok": True, "site": site, "cookies": len(site_cookies)}
        return {"ok": False, "error": "jAccount 会话调不通这个站，可能要先重新扫码登录"}

    if not _ok(site, site_cookies):
        return {
            "ok": False,
            "error": f'SSO 没拿到会话（HTTP {resp.status_code}，cookie {len(site_cookies)} 个）',
        }

    save_site(site, site_cookies)
    return {"ok": True, "site": site, "cookies": len(site_cookies)}


def client(site: str) -> httpx.Client:
    """拿到一个已经登录好的 httpx 客户端。"""
    jar = load_site(site)
    if not _ok(site, jar):
        result = login(site)
        if not result.get("ok"):
            raise JAccountError(result.get("error") or "登录失败")
        jar = load_site(site)
    return httpx.Client(
        headers={"User-Agent": UA}, cookies=jar, timeout=25, follow_redirects=True
    )


def _site_logged_in(site: str) -> bool:
    """各站登录状态。个别站有自己的一套存放方式。"""
    # 校园活动把 JWT 存在 impsa.json 里，不在 activity.json
    if site == "activity":
        try:
            from .sources.activity_portal import get_token
            from .auth import SessionStore

            return bool(get_token(SessionStore(), refresh=False))
        except Exception:                         # noqa: BLE001
            return False
    return _ok(site, load_site(site))


def status() -> dict:
    """jAccount 会话状态 + 各站登录情况。"""
    jar = cookies()
    return {
        "session": bool(jar.get("JAAuthCookie")),
        "sites": [
            {
                "key": key,
                "name": meta["name"],
                "method": meta.get("method", "sso"),
                "note": meta.get("note", ""),
                "logged_in": _site_logged_in(key),
            }
            for key, meta in SITES.items()
        ],
    }

"""扫码登录的会话管理。

前端拿到二维码后轮询状态，扫完就刷新页面。
JAccountLogin.wait() 是阻塞的，所以放后台线程里跑。
"""

from __future__ import annotations

import base64
import threading
import uuid

from xinge.auth import JAccountLogin, SessionStore
from xinge.auth.sites import BY_KEY

_sessions: dict[str, dict] = {}
_lock = threading.Lock()


def status() -> dict:
    """当前登录状态（以 Canvas 为准，所有站共用一套 jAccount 会话）。"""
    login = JAccountLogin(SessionStore())
    sites = {}
    for key in ("canvas", "cnmooc", "pe", "aihaoke"):
        site = BY_KEY.get(key)
        if not site:
            continue
        try:
            sites[key] = bool(login.is_authenticated(site))
        except Exception:
            sites[key] = False
    return {"logged_in": sites.get("canvas", False), "sites": sites}


def start() -> dict:
    session = SessionStore()
    login = JAccountLogin(session)
    site = BY_KEY["canvas"]

    if login.is_authenticated(site):
        return {"ok": True, "already": True}

    challenge = login.start(site)
    if challenge is None:
        return {"ok": True, "already": True}

    sid = uuid.uuid4().hex
    entry = {
        "login": login,
        "challenge": challenge,
        "state": "pending",
        "image": challenge.image,
        "version": 0,
    }
    with _lock:
        _sessions[sid] = entry

    threading.Thread(target=_watch, args=(sid,), daemon=True).start()
    return {"ok": True, "id": sid, "png": _b64(entry["image"]), "version": 0}


def _watch(sid: str) -> None:
    entry = _sessions.get(sid)
    if not entry:
        return

    def on_refresh(png: bytes) -> None:
        entry["image"] = png
        entry["version"] += 1

    try:
        ok = entry["login"].wait(entry["challenge"], timeout=300, on_refresh=on_refresh)
        entry["state"] = "ok" if ok else "timeout"
    except Exception as exc:
        entry["state"] = "error"
        entry["error"] = str(exc)[:200]


def poll(sid: str) -> dict:
    entry = _sessions.get(sid)
    if not entry:
        return {"state": "missing"}
    out = {"state": entry["state"], "version": entry["version"]}
    if entry["state"] == "pending":
        out["png"] = _b64(entry["image"])
    if entry.get("error"):
        out["error"] = entry["error"]
    return out


def cancel(sid: str) -> None:
    entry = _sessions.pop(sid, None)
    if entry:
        try:
            entry["challenge"].ws.close()
        except Exception:
            pass


def _b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")

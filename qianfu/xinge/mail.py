"""交大邮箱（Zimbra）的 IMAP 读取。

参考 Dela 的做法：不用 Zimbra 的 SOAP，直接用 IMAP。
Zimbra 的 Web 接口要 token 且认证方式古怪，IMAP 是标准协议，稳定得多。

    mail.sjtu.edu.cn:993  SSL

需要邮箱地址 + 密码（不是 jAccount 扫码）。密码存在 ~/.xinge/config.json，
只在本机，不上传。如果密码不对，多半是要先去 mail.sjtu.edu.cn 开启 IMAP
或者用"客户端专用密码"。
"""

from __future__ import annotations

import email
import imaplib
import re
import socket
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime

HOST = "mail.sjtu.edu.cn"
PORT = 993


class MailError(RuntimeError):
    pass


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _connect(address: str, password: str) -> imaplib.IMAP4_SSL:
    if not address or not password:
        raise MailError("还没有配置邮箱地址或密码")
    try:
        conn = imaplib.IMAP4_SSL(HOST, PORT, timeout=20)
    except (socket.error, imaplib.IMAP4.error) as exc:
        raise MailError(f"连不上 {HOST}:{PORT} — {exc}") from exc
    try:
        conn.login(address, password)
    except imaplib.IMAP4.error as exc:
        conn.logout()
        raise MailError(
            "邮箱登录失败。如果密码没错，先去 mail.sjtu.edu.cn 的"
            "「首选项 → 邮件 → 允许 IMAP」打开，或者用客户端专用密码。"
            f"（{exc}）"
        ) from exc
    return conn


def fetch_messages(address: str, password: str, limit: int = 20, folder: str = "INBOX") -> dict:
    """返回最近 limit 封邮件的摘要 + 未读数。"""
    conn = _connect(address, password)
    try:
        conn.select(folder, readonly=True)

        status, data = conn.status(folder, "(MESSAGES UNSEEN)")
        total = unread = 0
        if status == "OK" and data:
            text = data[0].decode("utf-8", "replace") if isinstance(data[0], bytes) else str(data[0])
            m = re.search(r"MESSAGES\s+(\d+)", text)
            u = re.search(r"UNSEEN\s+(\d+)", text)
            total = int(m.group(1)) if m else 0
            unread = int(u.group(1)) if u else 0

        status, data = conn.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return {"total": total, "unread": unread, "messages": []}

        ids = data[0].split()
        picked = ids[-max(1, min(limit, 100)):]

        messages = []
        for uid in reversed(picked):
            status, raw = conn.fetch(uid, "(BODY.PEEK[HEADER] FLAGS)")
            if status != "OK" or not raw or not isinstance(raw[0], tuple):
                continue
            header = email.message_from_bytes(raw[0][1])
            flags = raw[0][0].decode("utf-8", "replace") if isinstance(raw[0][0], bytes) else ""

            try:
                when = parsedate_to_datetime(header.get("Date", ""))
                when = when.astimezone().strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                when = ""

            sender_raw = header.get("From", "")
            sender_name = _decode(email.utils.parseaddr(sender_raw)[0]) if sender_raw else ""
            sender_addr = email.utils.parseaddr(sender_raw)[1] if sender_raw else ""

            messages.append(
                {
                    "subject": _decode(header.get("Subject", "")) or "(无主题)",
                    "from_name": sender_name,
                    "from_addr": sender_addr,
                    "date": when,
                    "seen": "\\Seen" in flags,
                }
            )
        return {"total": total, "unread": unread, "messages": messages}
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def test(address: str = "", password: str = "") -> dict:
    """优先试 jAccount；没有再退回 IMAP 密码。"""
    if not (address and password):
        return test_via_jaccount()
    try:
        conn = _connect(address, password)
    except MailError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        conn.select("INBOX", readonly=True)
        status, data = conn.status("INBOX", "(MESSAGES UNSEEN)")
        return {"ok": True, "detail": str(data)[:120]}
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ---------------- jAccount 直连（不用邮箱密码） ----------------
#
# 交大邮箱是 Zimbra，支持 jAccount 单点登录。
# 带着 jAccount 会话请求 mail.sjtu.edu.cn，会直接换到 ZM_AUTH_TOKEN，
# 然后就能用 Zimbra 的 REST 接口读收件箱，不需要 IMAP 密码。

ZIMBRA = "https://mail.sjtu.edu.cn/zimbra"


def _zimbra_session():
    """用 jAccount 会话登录 Zimbra，返回 (httpx.Client, token)。"""
    import httpx

    from . import jaccount

    jar = jaccount.cookies()
    if not jar.get("JAAuthCookie"):
        raise MailError("还没有 jAccount 登录态，先扫码登录一次")

    client = httpx.Client(headers={"User-Agent": jaccount.UA}, timeout=40, follow_redirects=True)
    for name, value in jar.items():
        client.cookies.set(name, value, domain="jaccount.sjtu.edu.cn")

    client.get("https://mail.sjtu.edu.cn/")

    token = ""
    for cookie in client.cookies.jar:
        if cookie.name == "ZM_AUTH_TOKEN":
            token = cookie.value
    if not token:
        client.close()
        raise MailError("jAccount 换不到邮箱会话，可能要重新扫码登录")
    return client, token


def fetch_via_jaccount(limit: int = 20, unread_only: bool = False) -> dict:
    """走 jAccount 读邮箱，不需要邮箱密码。

    Args:
        limit: 取几封。
        unread_only: 只要未读。

    Returns:
        和 fetch_messages 一样的结构。
    """
    client, _ = _zimbra_session()
    try:
        query = "&query=is:unread" if unread_only else ""
        resp = client.get(f"{ZIMBRA}/home/~/inbox?fmt=json&limit={max(1, min(limit, 100))}{query}")
        if resp.status_code != 200:
            raise MailError(f"邮箱接口返回 HTTP {resp.status_code}")

        payload = resp.json()
        raw = payload.get("m") or []

        # 顺手拿总未读数
        unread = len(raw) if unread_only else 0
        if not unread_only:
            try:
                cnt = client.get(f"{ZIMBRA}/home/~/inbox?fmt=json&limit=1&query=is:unread").json()
                unread = len(cnt.get("m") or [])
            except Exception:                     # noqa: BLE001
                unread = 0

        messages = []
        for item in raw:
            sender = (item.get("e") or [{}])[0]
            stamp = item.get("d")
            when = ""
            if isinstance(stamp, (int, float)):
                from datetime import datetime as _dt

                when = _dt.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d %H:%M")
            messages.append(
                {
                    "subject": (item.get("su") or "(无主题)").strip(),
                    "from_name": (sender.get("p") or sender.get("d") or "").strip(),
                    "from_addr": (sender.get("a") or "").strip(),
                    "date": when,
                    "seen": bool(item.get("f")) and "u" not in str(item.get("f") or ""),
                }
            )

        return {"total": len(raw), "unread": unread, "messages": messages}
    finally:
        client.close()


def test_via_jaccount() -> dict:
    """试一下 jAccount 能不能读邮箱。"""
    try:
        data = fetch_via_jaccount(limit=3)
    except MailError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "error": f"读取失败：{exc}"}
    return {"ok": True, "detail": f'未读 {data.get("unread", 0)} 封'}

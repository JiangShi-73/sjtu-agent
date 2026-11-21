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


def test(address: str, password: str) -> dict:
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

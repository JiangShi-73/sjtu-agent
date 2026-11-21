"""邮箱：IMAP 读取收件箱（做法参考 Dela）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import config
from xinge import mail as mailer

router = APIRouter(tags=["mail"])


def _creds() -> tuple[str, str]:
    cfg = config.load()
    return cfg.get("mail_address") or "", cfg.get("mail_password") or ""


@router.get("/mail/settings")
def get_settings():
    address, password = _creds()
    return {
        "address": address,
        "has_password": bool(password),
        "configured": bool(address and password),
    }


@router.put("/mail/settings")
def save_settings(body: dict):
    values = {}
    if "address" in body:
        values["mail_address"] = (body.get("address") or "").strip()
    if body.get("password"):
        values["mail_password"] = body["password"]
    config.save(values)
    address, password = _creds()
    if not (address and password):
        return {"ok": True, "tested": False}
    return {"ok": True, "tested": True, **mailer.test(address, password)}


@router.get("/mail/list")
def list_mail(limit: int = 20):
    address, password = _creds()
    if not (address and password):
        raise HTTPException(status_code=400, detail="还没有配置邮箱")
    try:
        return mailer.fetch_messages(address, password, limit=max(1, min(limit, 100)))
    except mailer.MailError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取邮件失败：{exc}") from exc

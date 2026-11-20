"""CNMOOC 课程管理。

课程列表接口（ajaxMyCourseIndex）会被服务端以 408 直接拒绝，
所以课程靠用户添加一次，之后按 id 抓「测验与作业」。

添加时会给一次课程页，把正式课程名读出来，省得用户手填。
"""

from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, HTTPException

from .. import config
from xinge.auth import SessionStore

router = APIRouter(tags=["courses"])

EXAM_URL = "https://cnmooc.sjtu.cn/examTest/stuExamList/{cid}.mooc"
RE_ID = re.compile(r"stuExamList/(\d+)")
RE_TITLE = re.compile(r'<h3 class="model-title[^"]*"[^>]*title="([^"]+)"')
RE_ANY_ID = re.compile(r"(\d{4,6})")


def _courses() -> list[dict]:
    return list(config.load().get("cnmooc_courses") or [])


def _save(courses: list[dict]) -> None:
    config.save({"cnmooc_courses": courses})


@router.get("/cnmooc/courses")
def list_courses():
    return {"courses": _courses()}


@router.post("/cnmooc/discover")
def discover():
    """自动检测「正在进行」的课程（用真实浏览器，接口拦非浏览器请求）。"""
    from xinge.sources.cnmooc import discover_courses

    found = discover_courses(SessionStore())
    if not found:
        return {
            "ok": False,
            "error": "没能自动检测：需要 Playwright + 本机 Edge/Chrome，且登录态有效",
            "courses": _courses(),
        }

    existing = {c.get("id"): c for c in _courses()}
    merged = [
        {
            "id": c["id"],
            "name": c.get("name") or existing.get(c["id"], {}).get("name", ""),
            "enabled": existing.get(c["id"], {}).get("enabled", True),
        }
        for c in found
    ]
    _save(merged)
    return {"ok": True, "courses": merged, "count": len(merged)}


@router.post("/cnmooc/courses")
def add_course(body: dict):
    raw = (body.get("url") or body.get("id") or "").strip()
    m = RE_ID.search(raw) or RE_ANY_ID.search(raw)
    if not m:
        raise HTTPException(status_code=400, detail="没从地址里认出课程 id")
    cid = m.group(1)

    courses = _courses()
    if any(c.get("id") == cid for c in courses):
        return {"ok": True, "courses": courses, "detail": "已存在"}

    name = (body.get("name") or "").strip()
    if not name:
        # 打开一次作业页，读课程名
        try:
            with SessionStore().client() as client:
                resp = client.get(EXAM_URL.format(cid=cid), timeout=25)
            t = RE_TITLE.search(resp.text)
            name = t.group(1).strip() if t else f"课程 {cid}"
        except httpx.HTTPError:
            name = f"课程 {cid}"

    courses.append({"id": cid, "name": name, "enabled": True})
    _save(courses)
    return {"ok": True, "courses": courses}


@router.patch("/cnmooc/courses/{cid}")
def update_course(cid: str, body: dict):
    courses = _courses()
    for c in courses:
        if c.get("id") == cid:
            if "enabled" in body:
                c["enabled"] = bool(body["enabled"])
            if body.get("name"):
                c["name"] = body["name"]
            _save(courses)
            return {"ok": True, "courses": courses}
    raise HTTPException(status_code=404, detail="课程不存在")


@router.delete("/cnmooc/courses/{cid}")
def delete_course(cid: str):
    courses = [c for c in _courses() if c.get("id") != cid]
    _save(courses)
    return {"ok": True, "courses": courses}

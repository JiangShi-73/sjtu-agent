"""知途好课：课程列表（右边栏用）。"""

from __future__ import annotations

from fastapi import APIRouter

from xinge.auth import SessionStore
from xinge.sources.aihaoke import fetch_courses

router = APIRouter(tags=["aihaoke"])


@router.get("/aihaoke/courses")
def courses(refresh: bool = False):
    if refresh:
        from xinge.sources.aihaoke import credentials

        credentials(SessionStore(), refresh=True)

    rows = fetch_courses(SessionStore())
    return {
        "count": len(rows),
        "continuing": sum(1 for r in rows if not r["finished"]),
        "courses": rows,
    }

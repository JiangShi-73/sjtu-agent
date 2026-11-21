"""Canvas 作业视图：按课程分组，给右边栏用。"""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter

from .. import db

router = APIRouter(tags=["canvas"])


@router.get("/canvas/homework")
def canvas_homework(include_submitted: bool = False):
    """按课程分组返回 Canvas 作业。

    默认只给未提交、未过期的，右边栏就是待办清单。
    """
    groups: dict[str, list[dict]] = {}

    for row in db.list_items("course", False, 500):
        if row["category"] != "作业":
            continue
        extra = row.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = {}

        if not include_submitted and extra.get("submitted"):
            continue

        deadline = row.get("deadline") or ""
        overdue = bool(deadline) and deadline[:10] < date.today().isoformat()

        course = extra.get("course_name") or "未分组"
        groups.setdefault(course, []).append(
            {
                "id": row["id"],
                "title": row["title"],
                "deadline": deadline or None,
                "overdue": overdue,
                "url": row.get("url"),
                "read": bool(row.get("read_at")),
            }
        )

    out = []
    for course, items in groups.items():
        # 未过期的排前面，过期的沉底
        items.sort(key=lambda x: (x["overdue"], x["deadline"] or "9999"))
        out.append({"course": course, "count": len(items), "items": items})

    out.sort(key=lambda g: (g["items"][0]["overdue"], g["items"][0]["deadline"] or "9999"))
    return {"groups": out, "total": sum(g["count"] for g in out)}

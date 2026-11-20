"""统一的数据模型。

上层（推送、汇总、bot 管理）只依赖这里的结构，不依赖具体站点。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Item:
    """一条被聚合的信息。"""

    source: str                      # 来源 key，如 "canvas"
    uid: str                         # 来源内唯一标识
    title: str
    url: str = ""
    summary: str = ""
    category: str = ""               # 公告 / 作业 / 活动 / 通知 ...
    published: datetime | None = None
    deadline: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """跨轮次去重用的稳定指纹。"""
        raw = f"{self.source}|{self.uid}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "uid": self.uid,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "category": self.category,
            "published": self.published.isoformat() if self.published else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "extra": self.extra,
        }


@dataclass(slots=True)
class FetchReport:
    """一次抓取的结果与诊断信息。"""

    source: str
    items: list[Item] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    elapsed_ms: int = 0

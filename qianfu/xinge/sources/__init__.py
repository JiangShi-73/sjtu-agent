"""信息源集合。

新增一个源 = 写一个类 + 打 @register_source，然后在 build() 里补一行。
"""

from __future__ import annotations

from ..auth import SessionStore
from .activity_portal import activity_index, detail_url
from .aihaoke import AihaokeSource
from .canvas import CanvasSource
from .college import CollegeSource
from .cnmooc import CnmoocSource
from .pe import PeSource
from .rss import RssSource
from .second_classroom import SecondClassroomSource

__all__ = [
    "CanvasSource",
    "CnmoocSource",
    "PeSource",
    "AihaokeSource",
    "CollegeSource",
    "RssSource",
    "activity_index",
    "detail_url",
    "SecondClassroomSource",
    "build",
]

# 源的构造签名各不相同：有的要会话，有的要 API Key
_FACTORIES = {
    "canvas": lambda s, **kw: CanvasSource(s, **kw),
    "cnmooc": lambda s, **kw: CnmoocSource(s, **kw),
    "pe": lambda s, **kw: PeSource(s),
    "second_classroom": lambda s, **kw: SecondClassroomSource(s, **kw),
    "rss": lambda s, **kw: RssSource(),
    "college": lambda s, **kw: CollegeSource(),
    "aihaoke": lambda s, **kw: AihaokeSource(s),
}


def build(key: str, session: SessionStore, **kwargs):
    if key not in _FACTORIES:
        raise SystemExit(f"未知信息源: {key}\n可用: {', '.join(_FACTORIES)}")
    return _FACTORIES[key](session, **kwargs)

"""信息源集合。

新增一个源 = 写一个类 + 打 @register_source，然后在 build() 里补一行。
"""

from __future__ import annotations

from ..auth import SessionStore
from .canvas import CanvasSource
from .cnmooc import CnmoocSource
from .pe import PeNoticeSource
from .second_classroom import SecondClassroomSource

__all__ = [
    "CanvasSource",
    "CnmoocSource",
    "PeNoticeSource",
    "SecondClassroomSource",
    "build",
]

# 源的构造签名各不相同：有的要会话，有的要 API Key
_FACTORIES = {
    "canvas": lambda s, **kw: CanvasSource(s, **kw),
    "cnmooc": lambda s, **kw: CnmoocSource(s, **kw),
    "pe": lambda s, **kw: PeNoticeSource(**kw),
    "second_classroom": lambda s, **kw: SecondClassroomSource(**kw),
}


def build(key: str, session: SessionStore, **kwargs):
    if key not in _FACTORIES:
        raise SystemExit(f"未知信息源: {key}\n可用: {', '.join(_FACTORIES)}")
    return _FACTORIES[key](session, **kwargs)

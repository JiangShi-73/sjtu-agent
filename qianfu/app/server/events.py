"""进程内事件总线。

现在是"一对多广播"，WebSocket 客户端和未来的插件都能订阅。
换成 Redis / 消息队列时，只要保持 publish/subscribe 这两个方法。
"""

from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 订阅者太慢，丢弃旧事件，避免拖垮发布方
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass


bus = EventBus()

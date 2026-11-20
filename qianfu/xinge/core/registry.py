"""极简注册表。

用装饰器把实现挂到全局，CLI 和将来的 bot 管理层都能按 key 发现它们。
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")

_SOURCES: dict[str, type] = {}
_AUTH: dict[str, type] = {}
_NOTIFIERS: dict[str, type] = {}


def register_source(cls: type) -> type:
    _SOURCES[cls.key] = cls
    return cls


def register_auth(cls: type) -> type:
    _AUTH[cls.key] = cls
    return cls


def register_notifier(cls: type) -> type:
    _NOTIFIERS[cls.key] = cls
    return cls


def sources() -> dict[str, type]:
    return dict(_SOURCES)


def authenticators() -> dict[str, type]:
    return dict(_AUTH)


def notifiers() -> dict[str, type]:
    return dict(_NOTIFIERS)

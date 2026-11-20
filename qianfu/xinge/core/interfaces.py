"""核心接口。

刻意做得很薄：只要实现这三个 Protocol，就能接进主流程。
将来做 bot 管理 / 多通道推送时，新增实现即可，不用改调用方。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import FetchReport, Item


@runtime_checkable
class Authenticator(Protocol):
    """认证提供者。

    典型实现：JAccount 扫码、账号密码、静态 Token、无认证。
    """

    key: str
    name: str

    def is_authenticated(self) -> bool:
        """当前凭据是否仍然可用。"""
        ...

    def login(self) -> None:
        """执行登录（可能需要交互）。"""
        ...

    def logout(self) -> None:
        ...


@runtime_checkable
class Source(Protocol):
    """信息源。

    一个 Source 负责：拉取 -> 归一化成 Item 列表。
    不关心怎么推送、推给谁。
    """

    key: str
    name: str
    auth: str | None          # 依赖的 Authenticator.key，None 表示免登录

    def fetch(self) -> FetchReport:
        ...


@runtime_checkable
class Notifier(Protocol):
    """推送通道。

    预留：QQ 官方机器人、企业微信、邮件、Webhook。
    """

    key: str
    name: str

    def send(self, items: list[Item], target: str) -> bool:
        ...

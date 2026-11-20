"""凭据与会话持久化。

设计要点：
  * 所有 cookie 存在用户目录 ~/.xinge/，**永远不进仓库**
  * 一个 CookieJar 覆盖所有域名，天然实现"扫一次码，全站可用"
  * 对外只暴露 httpx.Client，抓取层不需要知道认证细节
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HOME = Path(os.environ.get("XINGE_HOME", Path.home() / ".xinge"))


class SessionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (DEFAULT_HOME / "session.json")
        self._jar = httpx.Cookies()
        self.load()

    # ---------- 持久化 ----------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for row in rows:
            self._jar.set(
                row["name"],
                row["value"],
                domain=row.get("domain", ""),
                path=row.get("path", "/"),
            )

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in self._jar.jar
        ]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        return self.path

    def clear(self) -> None:
        self._jar = httpx.Cookies()
        if self.path.exists():
            self.path.unlink()

    # ---------- 使用 ----------

    def client(self, **kwargs) -> httpx.Client:
        """返回共享同一 CookieJar 的客户端；用完请 close()。"""
        client = httpx.Client(
            follow_redirects=True,
            timeout=kwargs.pop("timeout", 20),
            headers={"User-Agent": UA},
            **kwargs,
        )
        client.cookies = self._jar          # 共享，写入即持久
        return client

    def cookie_header(self, domain: str) -> str:
        return "; ".join(
            f"{c.name}={c.value}" for c in self._jar.jar if domain in c.domain
        )

    def has(self, domain: str) -> bool:
        return any(domain in c.domain for c in self._jar.jar)

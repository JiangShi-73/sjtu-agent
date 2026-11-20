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
        """创建一个带当前 cookie 的客户端。

        注意：httpx 的 cookies setter 会**拷贝**一份 CookieJar，
        所以 client 里新拿到的 cookie 不会自动回到 self._jar，
        用完必须调用 merge(client.cookies) 同步回来。
        """
        return httpx.Client(
            cookies=self._jar,
            follow_redirects=True,
            timeout=kwargs.pop("timeout", 20),
            headers={"User-Agent": UA},
            **kwargs,
        )

    def merge(self, cookies: httpx.Cookies) -> int:
        """把客户端里的 cookie 合并回持久化 jar。"""
        added = 0
        for c in cookies.jar:
            self._jar.set(c.name, c.value, domain=c.domain, path=c.path)
            added += 1
        return added

    def cookie_header(self, domain: str, path_prefix: str | None = None) -> str:
        """拼 Cookie 头。

        jAccount 会同时下发多个同名 JSESSIONID（/oauth2 与 /jaccount 各一个），
        必须按 path 过滤，否则会把错的会话发给服务端。
        """
        parts = []
        for c in self._jar.jar:
            if domain not in c.domain:
                continue
            if path_prefix and not c.path.startswith(path_prefix):
                continue
            parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)

    def has(self, domain: str) -> bool:
        return any(domain in c.domain for c in self._jar.jar)

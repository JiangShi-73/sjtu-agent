"""JAccount 扫码登录。

协议（从登录页 JS 逆出来的，见 README）：

    1. GET  <站点 SSO 入口>            -> 302 到 jaccount/jalogin，页面里带 uuid
    2. WS   wss://jaccount.sjtu.edu.cn/jaccount/sub/<uuid>
           发送 {"type":"UPDATE_QR_CODE"}
           收到 {"type":"UPDATE_QR_CODE","payload":{"ts":..,"sig":..}}
    3. GET  /jaccount/qrcode?uuid=..&ts=..&sig=..   -> PNG 二维码
    4. 用户用「微信」或「My SJTU」扫码
    5. WS   收到 {"type":"LOGIN"}
    6. GET  /jaccount/expresslogin?uuid=..          -> 回落到站点，站点写入自己的会话

全过程不弹浏览器、不接触用户密码。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx
import websocket

from .session import UA, SessionStore
from .sites import Site

JA_HOST = "jaccount.sjtu.edu.cn"
UUID_RE = re.compile(r'uuid:\s*"([0-9a-f-]{36})"')
REFRESH_INTERVAL = 55.0


class JAccountError(RuntimeError):
    pass


@dataclass
class QRChallenge:
    """一次待完成的扫码。"""

    site: Site
    uuid: str
    image: bytes
    ws: object
    created: float = field(default_factory=time.time)

    def save(self, path: str) -> str:
        with open(path, "wb") as fh:
            fh.write(self.image)
        return path


class JAccountLogin:
    """统一登录器：一个 CookieJar，覆盖所有走 jAccount 的站点。"""

    key = "jaccount"
    name = "JAccount 扫码登录"

    def __init__(self, session: SessionStore) -> None:
        self.session = session

    # ---------------- 状态判定 ----------------

    def is_authenticated(self, site: Site) -> bool:
        if not site.check_url:
            return False
        try:
            with self.session.client() as client:
                resp = client.get(site.check_url, timeout=15)
        except httpx.HTTPError:
            return False
        if resp.status_code != site.ok_status:
            return False
        body = resp.text
        if site.ok_body and site.ok_body not in body:
            return False
        return not any(bad in body for bad in site.fail_body)

    # ---------------- 第一步：拿二维码 ----------------

    def start(self, site: Site) -> QRChallenge | None:
        """打开站点的 SSO 入口。

        返回二维码；若该站点已有有效会话，返回 None。
        """
        client = self.session.client()
        try:
            resp = client.get(site.sso_url, timeout=20)
            if JA_HOST not in str(resp.url):
                self.session.save()
                return None

            match = UUID_RE.search(resp.text)
            if not match:
                raise JAccountError(
                    f"{site.key}: 登录页里没有 uuid，站点可能改版了"
                )
            uuid = match.group(1)

            ws = self._open_ws(uuid)
            payload = self._request_qr(ws)
            image = self._fetch_qr(client, uuid, payload)
            self.session.save()
            return QRChallenge(site=site, uuid=uuid, image=image, ws=ws)
        finally:
            client.close()

    def _open_ws(self, uuid: str) -> websocket.WebSocket:
        cookie = self.session.cookie_header(JA_HOST)
        return websocket.create_connection(
            f"wss://{JA_HOST}/jaccount/sub/{uuid}",
            header=[
                f"Cookie: {cookie}",
                f"User-Agent: {UA}",
                f"Origin: https://{JA_HOST}",
            ],
            timeout=30,
        )

    @staticmethod
    def _request_qr(ws: websocket.WebSocket) -> dict:
        ws.send(json.dumps({"type": "UPDATE_QR_CODE"}))
        for _ in range(6):
            msg = json.loads(ws.recv())
            if msg.get("type") == "UPDATE_QR_CODE" and msg.get("payload"):
                return msg["payload"]
            if msg.get("type") == "ERROR_MESSAGE":
                raise JAccountError(f"服务端返回错误: {msg}")
        raise JAccountError("没有收到二维码数据")

    @staticmethod
    def _fetch_qr(client: httpx.Client, uuid: str, payload: dict) -> bytes:
        resp = client.get(
            f"https://{JA_HOST}/jaccount/qrcode",
            params={"uuid": uuid, "ts": payload["ts"], "sig": payload["sig"]},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.content

    # ---------------- 第二步：等扫码 ----------------

    def wait(
        self,
        challenge: QRChallenge,
        timeout: float = 180,
        on_refresh: Callable[[bytes], None] | None = None,
    ) -> bool:
        """阻塞等待扫码结果。GUI 里请放到后台线程。"""
        ws = challenge.ws
        deadline = time.time() + timeout
        last_ping = time.time()
        client = self.session.client()
        try:
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    return False

                # 二维码会过期，定期换一张
                if time.time() - last_ping > REFRESH_INTERVAL:
                    ws.send(json.dumps({"type": "UPDATE_QR_CODE"}))
                    last_ping = time.time()

                ws.settimeout(min(remain, 30))
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue

                msg = json.loads(raw)
                kind = msg.get("type")

                if kind == "LOGIN":
                    client.get(
                        f"https://{JA_HOST}/jaccount/expresslogin",
                        params={"uuid": challenge.uuid},
                        timeout=25,
                    )
                    self.session.save()
                    return True

                if kind == "UPDATE_QR_CODE" and on_refresh and msg.get("payload"):
                    try:
                        on_refresh(self._fetch_qr(client, challenge.uuid, msg["payload"]))
                    except httpx.HTTPError:
                        pass
                    last_ping = time.time()

                if kind == "ERROR_MESSAGE":
                    raise JAccountError(str(msg))
        finally:
            client.close()
            try:
                ws.close()
            except Exception:
                pass

    def login(self, site: Site, timeout: float = 180, on_qr=None) -> bool:
        """一步到位：拿码 -> 等扫 -> 校验。"""
        challenge = self.start(site)
        if challenge is None:
            return self.is_authenticated(site)
        if on_qr:
            on_qr(challenge.image)
        ok = self.wait(challenge, timeout=timeout, on_refresh=on_qr)
        return ok and self.is_authenticated(site)

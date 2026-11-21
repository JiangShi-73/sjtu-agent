"""微信读书中继（替代 weread.111965.xyz）。

WeWe RSS 把几个接口转发给一个第三方中继，那个服务挂了（502）。
这里自己实现一份，直接对接 weread.qq.com。

WeWe RSS 只用到四个接口：

    GET /api/v2/login/platform              -> {uuid, scanUrl}
    GET /api/v2/login/platform/{uuid}       -> {message, vid?, token?, username?}
    GET /api/v2/platform/wxs2mp?url=        -> 公众号信息（要 Bearer + xid）
    GET /api/v2/platform/mps/{id}/articles  -> 文章列表（要 Bearer + xid）

登录用真浏览器：打开 weread.qq.com，点登录，把二维码抠出来给前端，
然后轮询 cookie 直到拿到 wr_vid（扫码成功）。

跑法：
    python -m relay.server --port 4001
然后把 wewe-rss 的 PLATFORM_URL 指到 http://127.0.0.1:4001
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid as uuidlib

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

app = FastAPI(title="xinge wechat relay")

_sessions: dict[str, dict] = {}
_lock = threading.Lock()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ---------------- 登录 ----------------

def _login_worker(sid: str) -> None:
    """在后台线程里跑浏览器：拿二维码 → 等扫码 → 抄 cookie。"""
    entry = _sessions[sid]
    try:
        from playwright.sync_api import sync_playwright

        from xinge.browser import launch
    except ImportError:
        entry["state"] = "error"
        entry["message"] = "没有 playwright"
        return

    with sync_playwright() as p:
        try:
            browser = launch(p, headless=True)
        except RuntimeError as exc:
            entry["state"] = "error"
            entry["message"] = str(exc)[:200]
            return

        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            page.goto("https://weread.qq.com/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)

            try:
                page.click("text=登录", timeout=8000)
            except Exception:
                pass

            # 二维码是 base64 图片
            qr = ""
            for _ in range(20):
                page.wait_for_timeout(700)
                qr = page.evaluate(
                    """() => {
                        const imgs = Array.from(document.querySelectorAll('img'));
                        const hit = imgs.find(i => i.src && i.src.startsWith('data:image'));
                        return hit ? hit.src : '';
                    }"""
                )
                if qr:
                    break

            if not qr:
                entry["state"] = "error"
                entry["message"] = "没能拿到二维码"
                return

            entry["qr"] = qr
            entry["state"] = "waiting"

            # 等扫码：cookie 里出现 wr_vid 就算成功
            deadline = time.time() + 300
            while time.time() < deadline:
                page.wait_for_timeout(2000)
                jar = {c["name"]: c["value"] for c in ctx.cookies() if "weread" in c["domain"]}
                if jar.get("wr_vid") and jar.get("wr_skey"):
                    entry["cookies"] = jar
                    entry["vid"] = jar.get("wr_vid")
                    entry["token"] = uuidlib.uuid4().hex
                    entry["username"] = jar.get("wr_vid", "")
                    entry["state"] = "ok"
                    with _lock:
                        _tokens[entry["token"]] = {"cookies": jar, "vid": entry["vid"]}
                    return
            entry["state"] = "timeout"
            entry["message"] = "等待扫码超时"
        except Exception as exc:
            entry["state"] = "error"
            entry["message"] = str(exc)[:200]
        finally:
            try:
                browser.close()
            except Exception:
                pass


_tokens: dict[str, dict] = {}


@app.get("/api/v2/login/platform")
def create_login():
    sid = uuidlib.uuid4().hex
    _sessions[sid] = {"state": "starting", "qr": "", "created": time.time()}
    threading.Thread(target=_login_worker, args=(sid,), daemon=True).start()

    # 等二维码出来再返回，前端只调一次
    for _ in range(40):
        time.sleep(0.5)
        if _sessions[sid]["state"] != "starting":
            break

    entry = _sessions[sid]
    if entry["state"] == "error":
        raise HTTPException(status_code=502, detail=entry.get("message") or "登录初始化失败")

    # WeWe RSS 前端是 <QRCodeSVG value={scanUrl}>，它要的是二维码里的字符串，
    # 不是图片地址，所以这里把二维码解码成链接再返回。
    scan = _decode_qr(entry.get("qr") or "")
    if not scan:
        raise HTTPException(status_code=502, detail="二维码解码失败")

    return {"uuid": sid, "scanUrl": scan}


def _decode_qr(data_url: str) -> str:
    if not data_url:
        return ""
    try:
        import base64 as _b64

        import cv2
        import numpy as np

        raw = _b64.b64decode(data_url.split(",", 1)[-1])
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return ""
        # 原始图只有 171px，直接解码识别不出来，要二值化 + 加白边 + 放大
        _, binary = cv2.threshold(img, 128, 255, cv2.THRESH_BINARY)
        padded = cv2.copyMakeBorder(binary, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
        big = cv2.resize(padded, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
        text, _pts, _ = cv2.QRCodeDetector().detectAndDecode(big)
        return text or ""
    except Exception:
        return ""


@app.get("/api/v2/login/platform/{sid}/qr")
def login_qr(sid: str):
    entry = _sessions.get(sid)
    if not entry or not entry.get("qr"):
        raise HTTPException(status_code=404, detail="二维码还没准备好")
    data = entry["qr"].split(",", 1)[-1]
    return Response(content=base64.b64decode(data), media_type="image/png")


@app.get("/api/v2/login/platform/{sid}")
def login_result(sid: str):
    """长轮询：WeWe RSS 那边超时设的是 120 秒，这里最多等 110 秒。

    不能一进来就返回"等待扫码"——它会把非 token 的响应当成登录失败弹错。
    """
    entry = _sessions.get(sid)
    if not entry:
        raise HTTPException(status_code=404, detail="会话不存在")

    deadline = time.time() + 110
    while time.time() < deadline and entry["state"] in ("starting", "waiting"):
        time.sleep(1.0)

    if entry["state"] == "ok":
        return {
            "message": "登录成功",
            "vid": entry["vid"],
            "token": entry["token"],
            "username": entry.get("username") or entry["vid"],
        }
    if entry["state"] == "timeout":
        return {"message": "扫码超时，请重新添加账号"}
    if entry["state"] == "error":
        return {"message": entry.get("message") or "登录失败"}
    return {"message": "等待扫码"}


# ---------------- 公众号 ----------------

RE_NAME = re.compile(r'<a[^>]*id="js_name"[^>]*>\s*([^<]+)', re.S)
RE_BIZ = re.compile(r'var biz = "([^"]*)"')
RE_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')
RE_CT = re.compile(r'var ct = "?(\d+)"?')


def _auth(headers) -> dict:
    auth = headers.get("authorization") or ""
    token = auth[7:] if auth.startswith("Bearer ") else ""
    info = _tokens.get(token)
    if not info:
        raise HTTPException(status_code=401, detail="token 无效")
    return info


@app.get("/api/v2/platform/wxs2mp")
def wxs2mp(url: str):
    """把一个公众号文章链接解析成公众号信息。

    这一步不需要微信读书，直接读文章页就行。
    """
    if "mp.weixin.qq.com" not in url:
        raise HTTPException(status_code=400, detail="不是微信公众号文章链接")

    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=30,
                          follow_redirects=True, verify=False) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"抓取文章失败：{exc}") from exc

    name = RE_NAME.search(resp.text)
    biz = RE_BIZ.search(resp.text)
    if not name or not biz:
        raise HTTPException(status_code=422, detail="文章页里没找到公众号信息")

    return {
        "id": biz.group(1),
        "name": name.group(1).strip(),
        "intro": "",
        "updateTime": 0,
    }


@app.get("/api/v2/platform/mps/{mp_id}/articles")
def mp_articles(mp_id: str):
    """文章列表。

    weread 的公众号文章接口还没打通（需要登录态才能摸到），
    这里先返回空列表，WeWe RSS 会显示"没有新文章"而不是报错。
    """
    return {"articles": [], "hasMore": False, "note": "文章列表接口待实现"}


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4001)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()

"""命令行入口：python -m xinge <命令>

命令只是薄薄一层，所有能力都在 auth / core 里，方便将来接到 GUI 或 bot。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .auth import JAccountError, JAccountLogin, SessionStore, select
from .auth.sites import SITES


def _open_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except Exception:
        pass


def cmd_sites(args) -> int:
    store = SessionStore()
    login = JAccountLogin(store)
    print(f"{'key':<10}{'名称':<22}{'状态'}")
    print("-" * 46)
    for site in SITES:
        if not site.check_url:
            state = "未检测"
        else:
            state = "已登录" if login.is_authenticated(site) else "未登录"
        print(f"{site.key:<10}{site.name:<22}{state}")
    print(f"\n凭据文件: {store.path}")
    return 0


def cmd_qr(args) -> int:
    store = SessionStore()
    login = JAccountLogin(store)
    site = select([args.site])[0]

    if login.is_authenticated(site):
        print(f"{site.name} 已是登录状态，无需扫码。")
        return 0

    challenge = login.start(site)
    if challenge is None:
        print(f"{site.name} 已是登录状态，无需扫码。")
        return 0

    out = Path(args.out or f"qr-{site.key}.png")
    challenge.save(str(out))
    print(f"二维码已保存: {out.resolve()}")
    print(f"uuid: {challenge.uuid}")
    if args.open:
        _open_file(out)
    challenge.ws.close()
    return 0


def cmd_login(args) -> int:
    store = SessionStore()
    login = JAccountLogin(store)
    sites = select(args.site)

    for site in sites:
        if login.is_authenticated(site):
            print(f"[跳过] {site.name} 已登录")
            continue

        print(f"[登录] {site.name}")
        challenge = login.start(site)
        if challenge is None:
            print(f"       已登录")
            continue

        out = Path(f"qr-{site.key}.png")
        challenge.save(str(out))
        print(f"       二维码: {out.resolve()}")
        print(f"       请用微信或 My SJTU 扫码（{args.timeout}s 内有效）")
        if args.open:
            _open_file(out)

        try:
            ok = login.wait(challenge, timeout=args.timeout)
        except JAccountError as exc:
            print(f"       失败: {exc}")
            continue

        if ok and login.is_authenticated(site):
            print(f"       成功")
        else:
            print(f"       未完成（超时或未扫码）")
    return 0


def cmd_fetch(args) -> int:
    from .sources import CanvasSource

    store = SessionStore()
    if args.source != "canvas":
        print(f"暂未实现: {args.source}")
        return 1

    src = CanvasSource(store, token=os.environ.get("CANVAS_TOKEN"))
    report = src.fetch()

    if not report.ok:
        print(f"抓取失败: {report.error}")
        return 1

    print(f"{src.name} -> {len(report.items)} 条（{report.elapsed_ms} ms）\n")
    for item in report.items[: args.limit]:
        when = item.published.strftime("%m-%d %H:%M") if item.published else "--"
        print(f"[{item.category or '其他'}] {when}  {item.title[:60]}")
        if item.url:
            print(f"           {item.url}")
    return 0


def cmd_logout(args) -> int:
    store = SessionStore()
    store.clear()
    print(f"已清空本地凭据: {store.path}")
    return 0


def cmd_cookies(args) -> int:
    store = SessionStore()
    header = store.cookie_header(args.domain)
    if not header:
        print(f"没有 {args.domain} 的 cookie")
        return 1
    print(header)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xinge", description="信鸽 SJTU —— 统一登录与信息聚合")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sites", help="查看各站点登录状态")
    sp.set_defaults(func=cmd_sites)

    sp = sub.add_parser("qr", help="只取一张二维码")
    sp.add_argument("--site", required=True)
    sp.add_argument("--out")
    sp.add_argument("--open", action="store_true", help="取到后自动打开图片")
    sp.set_defaults(func=cmd_qr)

    sp = sub.add_parser("login", help="扫码登录一个或多个站点")
    sp.add_argument("--site", action="append", help="可重复；不传则登录全部")
    sp.add_argument("--timeout", type=float, default=180)
    sp.add_argument("--open", action="store_true", default=True)
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("fetch", help="抓取某个信息源")
    sp.add_argument("--source", default="canvas")
    sp.add_argument("--limit", type=int, default=15)
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("logout", help="清空本地凭据")
    sp.set_defaults(func=cmd_logout)

    sp = sub.add_parser("cookies", help="导出某个域名的 Cookie 头")
    sp.add_argument("--domain", required=True)
    sp.set_defaults(func=cmd_cookies)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

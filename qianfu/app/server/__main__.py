"""python -m app.server"""

from __future__ import annotations

import argparse

try:
    import uvicorn
except ModuleNotFoundError as exc:  # 只在便携版解压不完整时走到
    raise SystemExit(
        f"信鸽后端启动失败：{exc}。"
        "便携版文件不完整（多半是 zip 没解压完），请重新完整解压后再启动。"
    ) from None


def main() -> None:
    parser = argparse.ArgumentParser(description="信鸽 SJTU 本地服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8722)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "app.server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()

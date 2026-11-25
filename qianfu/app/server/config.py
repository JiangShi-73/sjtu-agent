"""本地配置。

桌面端没有环境变量可用（快捷方式启动），所以把 API Key 这类设置
落在 ~/.xinge/config.json，由设置接口读写。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path.home() / ".xinge"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS: dict = {
    "sjtu_api_key": "",
    "canvas_token": "",
    "fetch_interval_min": 30,
    "rss_feeds": [],
    "cnmooc_courses": [],
    "api_tools": {},
    "api_bot": {},
    "qq_appid": "",
    "qq_secret": "",
    # 默认走校内的「致远一号」：数据不出境（用户已存的配置不会被覆盖）
    "llm_base": "https://models.sjtu.edu.cn/api/v1",
    "llm_key": "",
    "llm_model": "deepseek-chat",
    "college_enabled": {},
    "mail_address": "",
    "mail_password": "",
}


def load() -> dict:
    data = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            data.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    # 环境变量优先，方便调试
    if os.environ.get("SJTU_API_KEY"):
        data["sjtu_api_key"] = os.environ["SJTU_API_KEY"]
    if os.environ.get("CANVAS_TOKEN"):
        data["canvas_token"] = os.environ["CANVAS_TOKEN"]
    return data


def save(values: dict) -> dict:
    current = load()
    # rss_feeds 是列表，不能按标量规则过滤
    current.update(
        {k: v for k, v in values.items() if k in DEFAULTS or k in ("rss_feeds", "college_enabled")}
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return current


def get(key: str, default: str = "") -> str:
    return str(load().get(key, default))

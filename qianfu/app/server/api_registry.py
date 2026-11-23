"""接口开关：哪些能力对「问助手」开放、哪些对「机器人」开放。

两套消费者共用同一份后端能力，但暴露面不一样：

    问助手  →  11 个工具（模型调）
    机器人  →  19 个 /bot 接口（QQ 调）

这里集中登记，开关存进 config.json 的 api_tools / api_bot。
"""

from __future__ import annotations

from . import config

# 工具名 → 中文说明（问助手用）
AGENT_TOOLS: dict[str, str] = {
    "list_boards": "各板块未读数",
    "search_items": "全库搜索",
    "get_deadlines": "未来几天截止",
    "list_canvas_homework": "Canvas 作业",
    "list_cnmooc_homework": "慕课作业",
    "list_lab": "体育实验",
    "list_second_classroom": "第二课堂活动",
    "list_colleges": "学院列表",
    "list_college_notices": "某学院公告",
    "list_mail": "最近邮件",
    "fetch_all": "触发全量抓取",
    "shuiyuan_latest": "水源最新话题",
    "shuiyuan_search": "水源搜索",
    "shuiyuan_read_topic": "水源话题正文",
    "shuiyuan_categories": "水源分类",
    "list_items": "按板块列条目",
    "get_item_detail": "条目详情",
    "mark_item_read": "标记已读",
    "add_to_calendar": "加入日程",
    "list_events": "看日程",
    "create_event": "新建日程",
    "list_cnmooc_courses": "已订阅慕课",
    "list_feeds": "RSS 订阅列表",
    "get_mail_detail": "邮件正文",
    "diagnose": "体检",
}

# 路径前缀 → 中文说明（机器人用）
BOT_ENDPOINTS: dict[str, str] = {
    "/api/v1/bot/ping": "探活",
    "/api/v1/bot/boards": "板块与未读数",
    "/api/v1/bot/digest": "待推送条目",
    "/api/v1/bot/ack": "标记已推送",
    "/api/v1/bot/deadlines": "未来截止",
    "/api/v1/bot/canvas": "Canvas 作业",
    "/api/v1/bot/cnmooc": "慕课作业",
    "/api/v1/bot/pe": "体育实验",
    "/api/v1/bot/second-classroom": "第二课堂",
    "/api/v1/bot/activities": "活动列表",
    "/api/v1/bot/activity": "活动详情/截图",
    "/api/v1/bot/colleges": "学院公告",
    "/api/v1/bot/zhitu": "知途好课",
    "/api/v1/bot/aihaoke": "知途好课（别名）",
    "/api/v1/bot/mail": "最近邮件",
    "/api/v1/bot/search": "全库搜索",
    "/api/v1/bot/calendar": "日历图/文字",
}


def _cfg() -> dict:
    return config.load()


def agent_enabled() -> set[str]:
    """问助手能用的工具。默认全开。"""
    m = _cfg().get("api_tools") or {}
    return {k for k in AGENT_TOOLS if m.get(k, True)}


def bot_enabled() -> set[str]:
    """机器人能用的接口。默认全开。"""
    m = _cfg().get("api_bot") or {}
    return {k for k in BOT_ENDPOINTS if m.get(k, True)}


def bot_allows(path: str) -> bool:
    """某个 /bot 路径是否放行（前缀匹配）。"""
    enabled = bot_enabled()
    for prefix in BOT_ENDPOINTS:
        if path == prefix or path.startswith(prefix + "/"):
            return prefix in enabled
    return True


def overview() -> dict:
    """给前端看的完整清单。"""
    m_tools = _cfg().get("api_tools") or {}
    m_bot = _cfg().get("api_bot") or {}

    return {
        "agent": [
            {
                "key": k,
                "name": desc,
                "enabled": bool(m_tools.get(k, True)),
            }
            for k, desc in AGENT_TOOLS.items()
        ],
        "bot": [
            {
                "key": k,
                "name": desc,
                "enabled": bool(m_bot.get(k, True)),
            }
            for k, desc in BOT_ENDPOINTS.items()
        ],
    }


def toggle(kind: str, key: str, enabled: bool) -> dict:
    """开关一个工具或接口。"""
    if kind == "agent":
        if key not in AGENT_TOOLS:
            return {"ok": False, "error": "工具不存在"}
        m = dict(_cfg().get("api_tools") or {})
        m[key] = bool(enabled)
        config.save({"api_tools": m})
        return {"ok": True, "kind": kind, "key": key, "enabled": bool(enabled)}

    if kind == "bot":
        if key not in BOT_ENDPOINTS:
            return {"ok": False, "error": "接口不存在"}
        m = dict(_cfg().get("api_bot") or {})
        m[key] = bool(enabled)
        config.save({"api_bot": m})
        return {"ok": True, "kind": kind, "key": key, "enabled": bool(enabled)}

    return {"ok": False, "error": "kind 只能是 agent 或 bot"}

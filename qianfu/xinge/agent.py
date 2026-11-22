"""信鸽的 Agent：让模型能直接调用信鸽的接口。

和 Dela 一样的思路——工具注册表 + function calling 循环：

    用户提问 → 模型选工具 → 本地执行 → 结果回灌 → 模型组织回答

工具本身不重复实现业务，全部打到信鸽自己的 HTTP 接口上，
所以接口一改，工具跟着就变，不会出现两套逻辑。
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

API = os.environ.get("XINGE_API", "http://127.0.0.1:8722/api/v1")

DEFAULT_BASE = "https://opencode.ai/inference/go/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"

MAX_ROUNDS = 6

SYSTEM_PROMPT = (
    "你是信鸽 SJTU 的助手，帮上海交通大学的学生查校园信息。\n"
    "你能调用工具查：作业、慕课、实验、第二课堂活动、学院公告、邮件、截止日期。\n"
    "规则：\n"
    "1. 需要事实就调工具，不要凭印象编。\n"
    "2. 回答简洁，中文，列表用 1. 2. 3.。\n"
    "3. 查不到就说查不到，不要编造。\n"
    "4. 涉及时间要写清楚日期。\n"
)


# ---------------- 工具定义 ----------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_boards",
            "description": "列出所有信息板块和各自的未读条数。想了解有什么内容时先调这个。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_items",
            "description": "在全部信息里按标题搜索，比如「奖学金」「讲座」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "搜索词"},
                    "limit": {"type": "integer", "description": "返回条数，默认 8"},
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_deadlines",
            "description": "查未来几天要交的作业和截止事项。",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "看未来几天，默认 7"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_canvas_homework",
            "description": "列出 Canvas 上待交的作业。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "默认 10"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_cnmooc_homework",
            "description": "列出慕课（好大学在线）的作业和测验。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "默认 10"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_lab",
            "description": "列出体育实验安排。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "默认 10"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_second_classroom",
            "description": "列出第二课堂活动（志愿公益、劳动学时等）。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "默认 10"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_colleges",
            "description": "列出所有学院和各自的公告条数。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_college_notices",
            "description": "查某个学院的公告。需要先知道学院代号（用 list_colleges 拿）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "学院代号，如 seiee / cs / pharm"},
                    "limit": {"type": "integer", "description": "默认 8"},
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_mail",
            "description": "列出最近的邮件。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "默认 10"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shuiyuan_latest",
            "description": "看水源社区（SJTU BBS）的最新话题。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "默认 15"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shuiyuan_search",
            "description": "在水源社区里搜话题，比如「选课」「绩点」「宿舍」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "搜索词"},
                    "limit": {"type": "integer", "description": "默认 10"},
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shuiyuan_read_topic",
            "description": "读水源某个话题的正文和回复。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "integer", "description": "话题 id"},
                    "posts": {"type": "integer", "description": "读几层，默认 15"},
                },
                "required": ["topic_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shuiyuan_categories",
            "description": "列出水源的所有分类。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_items",
            "description": "按板块列信息条目，可指定学院或只看未读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "board": {
                        "type": "string",
                        "description": "course/mooc/lab/event/zhitu/college/shuiyuan/custom",
                    },
                    "college": {"type": "string", "description": "学院名，如 药学院"},
                    "unread": {"type": "boolean", "description": "只看未读"},
                    "limit": {"type": "integer", "description": "默认 20"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_item_detail",
            "description": "看某条信息的完整内容（摘要、链接、时间）。",
            "parameters": {
                "type": "object",
                "properties": {"item_id": {"type": "integer", "description": "条目 id"}},
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_item_read",
            "description": "把某条信息标记为已读。",
            "parameters": {
                "type": "object",
                "properties": {"item_id": {"type": "integer", "description": "条目 id"}},
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_calendar",
            "description": "把某条信息加进日程表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "条目 id"},
                    "kind": {"type": "string", "description": "start 开始时间 / signup 报名截止"},
                },
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "看日程表里某段时间的事件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "YYYY-MM-DD"},
                    "end": {"type": "string", "description": "YYYY-MM-DD"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "新建一个日程事件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "标题"},
                    "event_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "start_time": {"type": "string", "description": "HH:MM"},
                    "end_time": {"type": "string", "description": "HH:MM"},
                    "location": {"type": "string", "description": "地点"},
                },
                "required": ["title", "event_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_cnmooc_courses",
            "description": "看已订阅的慕课课程。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_feeds",
            "description": "看 RSS 订阅列表。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_mail_detail",
            "description": "看某封邮件的正文。",
            "parameters": {
                "type": "object",
                "properties": {"index": {"type": "integer", "description": "邮件序号，从 1 开始"}},
                "required": ["index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose",
            "description": "体检：各信息源能不能正常抓取。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_all",
            "description": "触发一次全量抓取，把最新的作业和公告拉下来。用户说「刷新」时用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]



# ---------------- 工具执行 ----------------

def _text(payload: dict, fallback: str = "") -> str:
    return payload.get("text") or fallback or json.dumps(payload, ensure_ascii=False)[:1500]


async def call_tool(name: str, args: dict[str, Any]) -> str:
    """执行一个工具，返回给模型看的文本。"""
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            if name == "list_boards":
                data = (await client.get(f"{API}/bot/boards")).json()
                lines = ["板块："]
                for b in data.get("boards") or []:
                    lines.append(f'- {b["name"]}：{b["unread"]} 条未读')
                return "\n".join(lines)

            if name == "search_items":
                data = (
                    await client.get(
                        f"{API}/bot/search",
                        params={"q": args.get("q", ""), "limit": args.get("limit", 8), "format": "text"},
                    )
                ).json()
                return _text(data, "没找到")

            if name == "get_deadlines":
                data = (
                    await client.get(f"{API}/bot/deadlines", params={"days": args.get("days", 7)})
                ).json()
                items = data.get("items") or []
                if not items:
                    return "没有要交的东西"
                return "\n".join(
                    f'- {it["title"]}（截止 {str(it.get("deadline"))[:16]}）' for it in items[:15]
                )

            simple = {
                "list_canvas_homework": "/bot/canvas",
                "list_cnmooc_homework": "/bot/cnmooc",
                "list_lab": "/bot/pe",
                "list_second_classroom": "/bot/second-classroom",
                "list_mail": "/bot/mail",
            }
            if name in simple:
                data = (
                    await client.get(
                        f"{API}{simple[name]}",
                        params={"limit": args.get("limit", 10), "format": "text"},
                    )
                ).json()
                return _text(data, "暂无内容")

            if name == "list_colleges":
                data = (
                    await client.get(f"{API}/bot/colleges", params={"format": "text"})
                ).json()
                return _text(data, "暂无学院")

            if name == "list_college_notices":
                data = (
                    await client.get(
                        f"{API}/bot/colleges/{args.get('key', '')}",
                        params={"limit": args.get("limit", 8), "format": "text"},
                    )
                ).json()
                return _text(data, "该学院暂无公告")

            if name == "shuiyuan_latest":
                data = (
                    await client.get(f"{API}/shuiyuan/latest", params={"limit": args.get("limit", 15)})
                ).json()
                topics = data.get("topics") or []
                if not topics:
                    return "水源没有新话题，或者还没登录"
                return "\n".join(
                    f'- [{t["id"]}] {t["title"]}（{t["posts_count"]} 楼，{t["last_posted_at"]}）'
                    for t in topics
                )

            if name == "shuiyuan_search":
                data = (
                    await client.get(
                        f"{API}/shuiyuan/search",
                        params={"q": args.get("q", ""), "limit": args.get("limit", 10)},
                    )
                ).json()
                topics = data.get("topics") or []
                if not topics:
                    return f'水源没搜到「{args.get("q", "")}」'
                return "\n".join(f'- [{t["id"]}] {t["title"]}' for t in topics)

            if name == "shuiyuan_read_topic":
                data = (
                    await client.get(
                        f"{API}/shuiyuan/topic/{args.get('topic_id')}",
                        params={"posts": args.get("posts", 15)},
                    )
                ).json()
                lines = [f'# {data.get("title")}', str(data.get("url") or "")]
                for p in data.get("posts") or []:
                    lines.append(
                        "\n{floor} 楼 {user}（{at}）：\n{text}".format(
                            floor=p["floor"], user=p["user"], at=p["at"], text=p["text"]
                        )
                    )
                return "\n".join(lines)[:4000]

            if name == "shuiyuan_categories":
                data = (await client.get(f"{API}/shuiyuan/categories")).json()
                cats = data.get("categories") or []
                return "\n".join(f'- {c["name"]}（{c["topics"]} 个话题）' for c in cats)

            if name == "list_items":
                params = {"limit": args.get("limit", 20)}
                if args.get("board"):
                    params["board"] = args["board"]
                if args.get("college"):
                    params["college"] = args["college"]
                if args.get("unread"):
                    params["unread"] = "true"
                data = (await client.get(f"{API}/items", params=params)).json()
                items = data.get("items") or []
                if not items:
                    return "没有符合条件的条目"
                return "\n".join(
                    f'[{it["id"]}] ({it["category"]}) {it["title"]}' for it in items[:30]
                )

            if name == "get_item_detail":
                data = (await client.get(f"{API}/items", params={"limit": 2000})).json()
                for it in data.get("items") or []:
                    if it["id"] == args.get("item_id"):
                        parts = [
                            f'标题：{it["title"]}',
                            f'板块：{it["category"]}',
                            f'时间：{it.get("deadline") or it.get("published") or ""}',
                            f'链接：{it.get("url") or ""}',
                            f'摘要：{it.get("summary") or ""}',
                        ]
                        return "\n".join(parts)
                return "找不到这条"

            if name == "mark_item_read":
                resp = await client.post(f"{API}/items/{args.get('item_id')}/read")
                return "已标记已读" if resp.status_code < 400 else f"失败：{resp.text[:80]}"

            if name == "add_to_calendar":
                resp = await client.post(
                    f"{API}/items/{args.get('item_id')}/schedule",
                    params={"kind": args.get("kind", "start")},
                )
                return "已加入日程" if resp.status_code < 400 else f"失败：{resp.text[:80]}"

            if name == "list_events":
                data = (
                    await client.get(
                        f"{API}/events",
                        params={
                            "start": args.get("start") or "2026-01-01",
                            "end": args.get("end") or "2027-12-31",
                        },
                    )
                ).json()
                events = data if isinstance(data, list) else (data.get("events") or [])
                if not events:
                    return "这段时间没有日程"
                return "\n".join(
                    f'- {e.get("event_date")} {e.get("start_time") or ""} {e.get("title")}'
                    for e in events[:30]
                )

            if name == "create_event":
                resp = await client.post(
                    f"{API}/events",
                    json={
                        "title": args.get("title"),
                        "event_date": args.get("event_date"),
                        "start_time": args.get("start_time") or "",
                        "end_time": args.get("end_time") or "",
                        "location": args.get("location") or "",
                        "source": "agent",
                    },
                )
                return "已创建日程" if resp.status_code < 400 else f"失败：{resp.text[:80]}"

            if name == "list_cnmooc_courses":
                data = (await client.get(f"{API}/cnmooc/courses")).json()
                courses = data.get("courses") or []
                return "\n".join(f'- {c.get("name")}' for c in courses) or "还没有订阅慕课"

            if name == "list_feeds":
                data = (await client.get(f"{API}/feeds")).json()
                feeds = data.get("feeds") or []
                return "\n".join(f'- {f.get("name")} {f.get("url")}' for f in feeds) or "还没有 RSS 订阅"

            if name == "get_mail_detail":
                data = (await client.get(f"{API}/mail/list", params={"limit": 60})).json()
                mails = data.get("mails") or []
                idx = int(args.get("index", 1)) - 1
                if not (0 <= idx < len(mails)):
                    return "没有这封邮件"
                m = mails[idx]
                return "\n".join(
                    [
                        f'发件人：{m.get("from")}',
                        f'主题：{m.get("subject")}',
                        f'时间：{m.get("date")}',
                        "",
                        (m.get("preview") or "")[:1500],
                    ]
                )

            if name == "diagnose":
                data = (await client.get(f"{API}/diagnose")).json()
                return json.dumps(data, ensure_ascii=False)[:2000]

            if name == "fetch_all":
                data = (await client.post(f"{API}/sources/fetch-all")).json()
                results = data.get("results") or data or []
                added = sum(r.get("added", 0) for r in results if isinstance(r, dict))
                return f"抓取完成，新增 {added} 条"

            return f"没有叫 {name} 的工具"
    except Exception as exc:                       # noqa: BLE001
        return f"工具 {name} 执行失败：{exc}"


# ---------------- 对话循环 ----------------

def enabled_tools(names: set[str] | None) -> list[dict]:
    """按开关筛工具。names 为 None 表示全开。"""
    if names is None:
        return TOOLS
    return [t for t in TOOLS if t["function"]["name"] in names]


async def chat(
    messages: list[dict],
    base: str = "",
    key: str = "",
    model: str = "",
    tool_names: set[str] | None = None,
) -> dict:
    """跑一轮带工具调用的对话。

    Args:
        messages: 前端传来的对话历史，形如 [{"role": "user", "content": "..."}]。
        base: 模型接口地址，空则用默认。
        key: 模型 API Key。
        model: 模型名。
        tool_names: 允许调用的工具集合，None 表示全部。

    Returns:
        {"reply": 最终回答, "steps": [工具调用记录], "usage": {...}}
    """
    base = (base or DEFAULT_BASE).rstrip("/")
    model = model or DEFAULT_MODEL

    if not key:
        return {"reply": "还没配模型 Key。在「机器人」里填一下 API Key 就能对话了。", "steps": []}

    active = enabled_tools(tool_names)
    convo: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in messages[-12:]:
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            convo.append({"role": role, "content": str(m["content"])[:4000]})

    steps: list[dict] = []
    usage = {"prompt": 0, "completion": 0}

    async with httpx.AsyncClient(timeout=180) as client:
        for _ in range(MAX_ROUNDS):
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "x-opencode-session": "xinge-agent"},
                json={"model": model, "messages": convo, "tools": active, "temperature": 0.3},
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("usage"):
                usage["prompt"] += data["usage"].get("prompt_tokens", 0)
                usage["completion"] += data["usage"].get("completion_tokens", 0)

            msg = (data.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls") or []

            if not calls:
                return {
                    "reply": msg.get("content") or "（模型没有返回内容）",
                    "steps": steps,
                    "usage": usage,
                }

            convo.append(
                {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
            )

            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}

                result = await call_tool(name, args)
                steps.append({"tool": name, "args": args, "result": result[:600]})

                convo.append(
                    {"role": "tool", "tool_call_id": call.get("id"), "content": result}
                )

    return {"reply": "想得太久了，换个问法试试？", "steps": steps, "usage": usage}

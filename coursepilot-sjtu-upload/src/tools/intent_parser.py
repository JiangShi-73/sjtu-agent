"""自然语言排课偏好解析器（由交大官方大模型解析，内置正则兜底保障健壮性）。"""

from __future__ import annotations

import re

from src.core.llm_client import LLMClient
from src.core.schemas import FreeBlock, SchedulePreferences


_SYSTEM_PROMPT = """你是上海交通大学选课助手的意图解析模块。
请将用户的自然语言选课偏好严格转换为如下 JSON 对象（只输出合法 JSON，不要任何多余文字）：
{
  "min_credits": 14.0,
  "max_credits": 22.0,
  "avoid_early_8am": true,
  "avoid_evening": false,
  "minimize_building_travel": true,
  "prioritize_modules": ["通识核心-人文"],
  "free_blocks": [{"day": 3, "half_day": "afternoon"}]
}
说明：
- day 取值 1-7（1=周一，5=周五）；half_day 取值 "morning" / "afternoon" / "evening"。
- prioritize_modules 请尽量匹配常见模块名（如 公共基础、通识核心-人文、通识核心-社科、通识核心-自然、专业必修、专业选修、实践环节、个性化模块）。
"""

_DAY_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}

_MODULE_KEYWORDS = {
    "通识核心-人文": ["人文", "文史", "哲学"],
    "通识核心-社科": ["社科", "社会科学", "经济管理"],
    "通识核心-自然": ["自然", "自然科学"],
    "专业必修": ["专必", "专业必修", "核心必修"],
    "专业选修": ["专选", "专业选修"],
    "实践环节": ["实践", "实验", "实训"],
    "个性化模块": ["个性化", "跨学科", "任选"],
}


def rule_parse_preferences(
    query: str, base_prefs: SchedulePreferences | None = None
) -> SchedulePreferences:
    """结构化中文选课偏好正则解析器（作为大模型 JSON 异常时的安全兜底）。"""
    prefs = base_prefs.model_copy(deep=True) if base_prefs else SchedulePreferences()
    prefs.raw_query = query
    if not query.strip():
        return prefs

    m_range = re.search(
        r"(\d{1,2}(?:\.\d)?)\s*[-~到至]\s*(\d{1,2}(?:\.\d)?)\s*(?:个)?学?分", query
    )
    if m_range:
        lo, hi = float(m_range.group(1)), float(m_range.group(2))
        if lo <= hi:
            prefs.min_credits = lo
            prefs.max_credits = hi
    else:
        m_max = re.search(
            r"(?:不超过|最多|上限|小于等于)\s*(\d{1,2}(?:\.\d)?)\s*(?:个)?学?分", query
        )
        if m_max:
            prefs.max_credits = float(m_max.group(1))
        m_min = re.search(
            r"(?:至少|不少于|最低|下限)\s*(\d{1,2}(?:\.\d)?)\s*(?:个)?学?分", query
        )
        if m_min:
            prefs.min_credits = float(m_min.group(1))

    if any(k in query for k in ("避开早八", "不要早八", "无早八", "别排早八", "不想早起", "别太早")):
        prefs.avoid_early_8am = True
    elif any(k in query for k in ("接受早八", "可以早八", "不怕早八")):
        prefs.avoid_early_8am = False

    if any(k in query for k in ("避开晚课", "不要晚课", "无晚课", "晚上不想上课", "别排晚上")):
        prefs.avoid_evening = True

    if any(k in query for k in ("少跨楼", "不要跨区", "不跨校区", "别跑太远", "减少赶课")):
        prefs.minimize_building_travel = True

    free_blocks: list[FreeBlock] = list(prefs.free_blocks)
    existing_fb = {(fb.day, fb.half_day) for fb in free_blocks}
    for m in re.finditer(
        r"(?:周|星期)([一二三四五六日天])\s*(上午|下午|晚上|全天)\s*(?:留空|空出|空出来|不上课|不要课|没课|有事|休息)",
        query,
    ):
        day = _DAY_CN[m.group(1)]
        period_str = m.group(2)
        targets = (
            ["morning", "afternoon", "evening"]
            if period_str == "全天"
            else [{"上午": "morning", "下午": "afternoon", "晚上": "evening"}[period_str]]
        )
        for hd in targets:
            if (day, hd) not in existing_fb:
                existing_fb.add((day, hd))
                free_blocks.append(FreeBlock(day=day, half_day=hd))
    prefs.free_blocks = free_blocks

    prioritized = list(prefs.prioritize_modules)
    for mod_name, kws in _MODULE_KEYWORDS.items():
        if any(kw in query for kw in kws) and mod_name not in prioritized:
            prioritized.append(mod_name)
    prefs.prioritize_modules = prioritized

    return prefs


def parse_user_preferences(
    query: str,
    llm_client: LLMClient | None = None,
    base_prefs: SchedulePreferences | None = None,
) -> tuple[SchedulePreferences, str]:
    """将自然语言转换为结构化排课约束，返回 (偏好对象, 解析来源标签)。"""
    client = llm_client or LLMClient()
    if query.strip() and client.is_configured:
        data = client.chat_json(_SYSTEM_PROMPT, f"用户选课需求：{query}")
        if isinstance(data, dict):
            try:
                merged = base_prefs.model_dump() if base_prefs else {}
                merged.update(data)
                merged["raw_query"] = query
                return (
                    SchedulePreferences.model_validate(merged),
                    f"交大官方大模型（{client.model_name}）",
                )
            except Exception:
                pass

    return rule_parse_preferences(query, base_prefs=base_prefs), "本地规则兜底解析"

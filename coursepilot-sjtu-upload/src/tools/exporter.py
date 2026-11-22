"""标准日历 (.ics) 与 Markdown 核查排课报告导出工具。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from icalendar import Alarm, Calendar, Event

from src.core.schemas import AuditReport, ScheduleCandidate


# 上海交通大学闵行校区作息节次起止时间映射
_PERIOD_START: dict[int, time] = {
    1: time(8, 0),
    2: time(8, 55),
    3: time(10, 0),
    4: time(10, 55),
    5: time(12, 0),
    6: time(12, 55),
    7: time(14, 0),
    8: time(14, 55),
    9: time(16, 0),
    10: time(16, 55),
    11: time(18, 0),
    12: time(18, 55),
    13: time(19, 50),
}

_PERIOD_END: dict[int, time] = {
    1: time(8, 45),
    2: time(9, 40),
    3: time(10, 45),
    4: time(11, 40),
    5: time(12, 45),
    6: time(13, 40),
    7: time(14, 45),
    8: time(15, 40),
    9: time(16, 45),
    10: time(17, 40),
    11: time(18, 45),
    12: time(19, 40),
    13: time(20, 35),
}

_DAY_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def export_schedule_to_ics(
    candidate: ScheduleCandidate,
    semester_start_monday: date | None = None,
    total_weeks: int = 16,
) -> bytes:
    """将选定的无冲突课表导出为标准 .ics 重复日程文件（支持单双周）。"""
    if semester_start_monday is None:
        # 默认取下一个周一作为第 1 教学周起始日
        today = date.today()
        days_ahead = (7 - today.weekday()) % 7
        semester_start_monday = today + timedelta(days=days_ahead or 7)

    cal = Calendar()
    cal.add("prodid", "-//CoursePilot SJTU//SJTU Course Scheduler//ZH")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"交大智能课表-{candidate.strategy_name}")

    for sec in candidate.sections:
        for idx, slot in enumerate(sec.time_slots):
            week_offset = 1 if slot.week_type == "even" else 0
            first_date = semester_start_monday + timedelta(
                days=(slot.day - 1) + week_offset * 7
            )
            dt_start = datetime.combine(first_date, _PERIOD_START[slot.start_period])
            dt_end = datetime.combine(first_date, _PERIOD_END[slot.end_period])

            ev = Event()
            ev.add("uid", f"{sec.section_id}-{idx}@coursepilot.sjtu.edu.cn")
            ev.add("summary", f"{sec.course_name} ({sec.course_code})")
            ev.add("location", f"{sec.building} [{sec.zone}]")
            ev.add(
                "description",
                f"授课教师：{sec.teacher}\n教学班：{sec.section_id}\n模块：{sec.module}（{sec.credits}学分）\n周次类型：{slot.week_type}",
            )
            ev.add("dtstart", dt_start)
            ev.add("dtend", dt_end)

            interval = 2 if slot.week_type in {"odd", "even"} else 1
            count = total_weeks // 2 if interval == 2 else total_weeks
            ev.add("rrule", {"freq": "weekly", "interval": interval, "count": count})

            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", f"即将上课：{sec.course_name} @ {sec.building}")
            alarm.add("trigger", timedelta(minutes=-15))
            ev.add_component(alarm)

            cal.add_component(ev)

    return cal.to_ical()


def export_markdown_report(
    audit: AuditReport, candidate: ScheduleCandidate | None = None
) -> str:
    """生成结构化的 Markdown 毕业学分核查与排课建议报告。"""
    lines: list[str] = [
        f"# CoursePilot SJTU 学业核查与选课建议报告",
        "",
        f"- **适用培养方案**：{audit.plan_name}",
        f"- **总毕业进度**：已修 **{audit.total_completed_credits}** / 要求 **{audit.total_required_credits}** 学分（剩余 **{audit.total_remaining_credits}** 学分）",
        "",
        "## 一、 培养方案各模块学分缺口",
        "",
        "| 模块名称 | 要求学分 | 已修学分 | 缺口学分 | 完成进度 | 缺失必修课 |",
        "| :--- | :---: | :---: | :---: | :---: | :--- |",
    ]

    for m in audit.module_items:
        missing_str = ", ".join(m.missing_mandatory_courses) if m.missing_mandatory_courses else "无"
        lines.append(
            f"| {m.module_name} | {m.required_credits} | {m.completed_credits} | **{m.remaining_credits}** | {m.completion_pct}% | {missing_str} |"
        )

    lines.append("")
    lines.append("## 二、 本学期先修课拦截预警")
    lines.append("")
    if not audit.blocked_courses:
        lines.append("- 当前开课表中暂无因先修课不足而被拦截的课程。")
    else:
        for b in audit.blocked_courses:
            lines.append(
                f"- **{b.course_code} {b.course_name}**：缺少先修课 `{', '.join(b.missing_prereqs)}`"
            )

    if candidate is not None:
        lines.extend(
            [
                "",
                f"## 三、 推荐无冲突课表（{candidate.strategy_name}）",
                "",
                f"- **策略说明**：{candidate.strategy_desc}",
                f"- **本学期总学分**：{candidate.total_credits} 学分",
                f"- **方案亮点**：{'；'.join(candidate.highlights)}",
                "",
                "| 课程代码 | 课程名称 | 学分 | 模块 | 教师 | 地点 | 上课时间 |",
                "| :--- | :--- | :---: | :--- | :--- | :--- | :--- |",
            ]
        )
        for sec in candidate.sections:
            t_str = "；".join(
                f"{_DAY_CN[s.day]} {s.start_period}-{s.end_period}节"
                + ("" if s.week_type == "all" else ("(单)" if s.week_type == "odd" else "(双)"))
                for s in sec.time_slots
            )
            lines.append(
                f"| {sec.course_code} | {sec.course_name} | {sec.credits} | {sec.module} | {sec.teacher} | {sec.building} | {t_str} |"
            )

        if candidate.cross_zone_warnings:
            lines.append("")
            lines.append("### 跨区连堂提示")
            for w in candidate.cross_zone_warnings:
                lines.append(f"- {w}")

    return "\n".join(lines)

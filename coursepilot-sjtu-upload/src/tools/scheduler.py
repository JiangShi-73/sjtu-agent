"""多约束智能排课求解器（硬约束零冲突 + 软约束偏好优化 + 局部重排 + 无解诊断）。"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from src.core.schemas import (
    AuditReport,
    CourseOffering,
    CurriculumPlan,
    FreeBlock,
    ScheduleCandidate,
    SchedulePreferences,
    ScheduleResult,
    TimeSlot,
)
from src.tools.auditor import check_prereqs_for_course


# 交大校园片区移动距离权重（值越大说明相邻节次赶课越吃力）
_ZONE_DISTANCE: dict[tuple[str, str], float] = {
    ("闵行东区", "闵行西区"): 1.0,
    ("闵行东区", "电院群楼"): 0.3,
    ("闵行西区", "电院群楼"): 0.8,
    ("闵行东区", "徐汇校区"): 3.0,
    ("闵行西区", "徐汇校区"): 3.0,
    ("电院群楼", "徐汇校区"): 3.0,
}

_DAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def sections_conflict(a: CourseOffering, b: CourseOffering) -> bool:
    """判断两个教学班是否存在时间冲突（同一课程代码也视为冲突，不可重复选）。"""
    if a.course_code == b.course_code:
        return True
    for sa in a.time_slots:
        for sb in b.time_slots:
            if sa.conflicts_with(sb):
                return True
    return False


def _zone_dist(z1: str, z2: str) -> float:
    if z1 == z2:
        return 0.0
    return _ZONE_DISTANCE.get((z1, z2), _ZONE_DISTANCE.get((z2, z1), 0.5))


def _slot_hits_free_block(slot: TimeSlot, fb: FreeBlock) -> bool:
    if slot.day != fb.day:
        return False
    if fb.half_day == "morning":
        return not (slot.end_period < 1 or slot.start_period > 4)
    if fb.half_day == "afternoon":
        return not (slot.end_period < 6 or slot.start_period > 10)
    if fb.half_day == "evening":
        return not (slot.end_period < 11 or slot.start_period > 13)
    return False


@dataclass(frozen=True)
class _StrategyProfile:
    name: str
    desc: str
    deficit_weight: float
    mandatory_weight: float
    early_8am_penalty: float
    evening_penalty: float
    free_block_penalty: float
    travel_penalty: float


def _evaluate_schedule(
    sections: list[CourseOffering],
    audit: AuditReport,
    prefs: SchedulePreferences,
    profile: _StrategyProfile,
    penalize_section_ids: set[str] | None = None,
) -> tuple[float, int, int, list[str], dict[str, float], list[str]]:
    """对单套无冲突课表计算综合得分与可解释亮点。"""
    deficit_map = {m.module_name: m.remaining_credits for m in audit.module_items}
    missing_mandatory_set = {
        c for m in audit.module_items for c in m.missing_mandatory_courses
    }

    score = 0.0
    early_8am = 0
    evening = 0
    free_violations = 0
    covered_modules: dict[str, float] = defaultdict(float)
    mandatory_hit = 0
    locked_set = set(prefs.locked_section_ids)

    for sec in sections:
        covered_modules[sec.module] += sec.credits
        # 多样性软惩罚：鼓励不同策略生成有区分度的方案
        if penalize_section_ids and sec.section_id in penalize_section_ids and sec.section_id not in locked_set:
            score -= 5.0
        # 1. 缺口模块加分（超过缺口部分衰减）
        rem = deficit_map.get(sec.module, 0.0)
        useful_credits = min(sec.credits, max(0.0, rem))
        overflow_credits = max(0.0, sec.credits - useful_credits)
        score += useful_credits * profile.deficit_weight + overflow_credits * 1.0

        # 2. 用户明确指定的优先模块加分
        if sec.module in prefs.prioritize_modules:
            score += sec.credits * 6.0

        # 3. 缺失必修课强加分
        if sec.course_code in missing_mandatory_set:
            score += profile.mandatory_weight
            mandatory_hit += 1

        # 4. 节次与偏好惩罚
        for slot in sec.time_slots:
            if slot.start_period <= 2:
                early_8am += 1
                if prefs.avoid_early_8am:
                    score -= profile.early_8am_penalty
            if slot.end_period >= 11:
                evening += 1
                if prefs.avoid_evening:
                    score -= profile.evening_penalty
            for fb in prefs.free_blocks:
                if _slot_hits_free_block(slot, fb):
                    free_violations += 1
                    score -= profile.free_block_penalty

    # 5. 相邻节次跨片区赶课检测
    cross_zone_warnings: list[str] = []
    by_day: dict[int, list[tuple[TimeSlot, CourseOffering]]] = defaultdict(list)
    for sec in sections:
        for slot in sec.time_slots:
            by_day[slot.day].append((slot, sec))

    for day, items in by_day.items():
        items.sort(key=lambda x: x[0].start_period)
        for i in range(len(items) - 1):
            s1, c1 = items[i]
            s2, c2 = items[i + 1]
            # 紧邻节次（间隔 <= 1 节，例如 1-2 节接 3-4 节，或 6-7 节接 8-9 节）
            if s2.start_period - s1.end_period <= 1:
                dist = _zone_dist(c1.zone, c2.zone)
                if dist >= 0.8:
                    if prefs.minimize_building_travel:
                        score -= dist * profile.travel_penalty
                    cross_zone_warnings.append(
                        f"{_DAY_NAMES[day]}第{s1.end_period}→{s2.start_period}节：{c1.course_name}({c1.building}) → {c2.course_name}({c2.building}) 跨区赶课"
                    )

    highlights: list[str] = []
    if mandatory_hit > 0:
        highlights.append(f"覆盖 {mandatory_hit} 门未修专业/培养方案必修课")
    if early_8am == 0:
        highlights.append("全程无早八（1-2节）")
    else:
        highlights.append(f"含 {early_8am} 次早八")
    if not cross_zone_warnings:
        highlights.append("无跨片区连堂赶课")
    else:
        highlights.append(f"有 {len(cross_zone_warnings)} 处跨区连堂提示")
    if prefs.free_blocks and free_violations == 0:
        highlights.append("完全满足指定留空时段")

    return (
        round(score, 2),
        early_8am,
        evening,
        cross_zone_warnings,
        {k: round(v, 1) for k, v in covered_modules.items()},
        highlights,
    )


def _solve_single_profile(
    grouped_offerings: list[list[CourseOffering]],
    locked_sections: list[CourseOffering],
    audit: AuditReport,
    prefs: SchedulePreferences,
    profile: _StrategyProfile,
    time_budget_sec: float = 0.25,
    penalize_section_ids: set[str] | None = None,
) -> ScheduleCandidate | None:
    """使用带剪枝的回溯分支定界算法，在毫秒级时间内寻找高分无冲突课表。"""
    start_ts = time.perf_counter()
    locked_credits = sum(s.credits for s in locked_sections)
    deficit_map = {m.module_name: m.remaining_credits for m in audit.module_items}
    missing_mandatory_set = {
        c for m in audit.module_items for c in m.missing_mandatory_courses
    }

    # 按单课潜在价值降序排列课程组，加快找到优质解
    def _group_priority(group: list[CourseOffering]) -> float:
        sample = group[0]
        val = 0.0
        if sample.course_code in missing_mandatory_set:
            val += 30.0
        if sample.module in prefs.prioritize_modules:
            val += 15.0
        val += min(sample.credits, deficit_map.get(sample.module, 0.0)) * 5.0
        return val

    groups = sorted(grouped_offerings, key=_group_priority, reverse=True)
    # 每组内部按软约束友好度排序（少早八/少撞留空优先）
    for g in groups:
        g.sort(
            key=lambda s: (
                sum(1 for sl in s.time_slots if sl.start_period <= 2) if prefs.avoid_early_8am else 0,
                sum(
                    1
                    for sl in s.time_slots
                    for fb in prefs.free_blocks
                    if _slot_hits_free_block(sl, fb)
                ),
            )
        )

    # 后缀最大剩余可选学分（用于下界剪枝）
    n = len(groups)
    suffix_max_credits = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_max_credits[i] = suffix_max_credits[i + 1] + groups[i][0].credits

    best_score = float("-inf")
    best_sections: list[CourseOffering] | None = None
    nodes_visited = 0
    timed_out = False

    chosen: list[CourseOffering] = list(locked_sections)

    def dfs(idx: int, cur_credits: float) -> None:
        nonlocal best_score, best_sections, nodes_visited, timed_out
        if timed_out:
            return
        nodes_visited += 1
        if nodes_visited % 256 == 0 and (time.perf_counter() - start_ts) > time_budget_sec:
            timed_out = True
            return

        # 剪枝 1：即使把后面全选上也不够最低学分
        if cur_credits + suffix_max_credits[idx] < prefs.min_credits:
            return

        # 如果当前学分已经满足范围，评估一次完整方案
        if prefs.min_credits <= cur_credits <= prefs.max_credits:
            sc, *_ = _evaluate_schedule(
                chosen, audit, prefs, profile, penalize_section_ids=penalize_section_ids
            )
            if sc > best_score:
                best_score = sc
                best_sections = list(chosen)

        if idx == n:
            return

        group = groups[idx]
        course_credits = group[0].credits

        # 分支 A：尝试从该课程中选一个不冲突的教学班
        if cur_credits + course_credits <= prefs.max_credits:
            for sec in group:
                if not any(sections_conflict(sec, existing) for existing in chosen):
                    chosen.append(sec)
                    dfs(idx + 1, cur_credits + course_credits)
                    chosen.pop()

        # 分支 B：不选该课程
        dfs(idx + 1, cur_credits)

    dfs(0, locked_credits)

    if best_sections is None:
        return None

    score, early_8am, evening, warnings, covered, highlights = _evaluate_schedule(
        best_sections, audit, prefs, profile
    )
    return ScheduleCandidate(
        strategy_name=profile.name,
        strategy_desc=profile.desc,
        sections=sorted(
            best_sections,
            key=lambda s: (
                min(sl.day for sl in s.time_slots),
                min(sl.start_period for sl in s.time_slots),
            ),
        ),
        total_credits=round(sum(s.credits for s in best_sections), 1),
        score=score,
        early_8am_count=early_8am,
        evening_count=evening,
        cross_zone_warnings=warnings,
        covered_modules=covered,
        highlights=highlights,
    )


def generate_schedules(
    offerings: list[CourseOffering],
    audit: AuditReport,
    plan: CurriculumPlan,
    prefs: SchedulePreferences,
) -> ScheduleResult:
    """生成 Top 3 差异化无冲突课表方案；若无解则返回具体原因诊断。"""
    diagnostics: list[str] = []
    passed_set = set(audit.passed_course_codes)
    excluded_codes = {c.upper() for c in prefs.excluded_course_codes}
    excluded_sec_ids = set(prefs.excluded_section_ids)

    # 1. 过滤合规可选教学班
    eligible: list[CourseOffering] = []
    offering_by_id = {o.section_id: o for o in offerings}

    for off in offerings:
        if off.course_code in passed_set:
            continue
        if off.course_code in excluded_codes or off.section_id in excluded_sec_ids:
            continue
        missing_pre = check_prereqs_for_course(
            off.course_code, off.prerequisites, passed_set, plan
        )
        if missing_pre:
            continue
        eligible.append(off)

    # 2. 校验强制锁定教学班
    locked_sections: list[CourseOffering] = []
    locked_codes: set[str] = set()
    for sid in prefs.locked_section_ids:
        sec = offering_by_id.get(sid)
        if sec is None:
            diagnostics.append(f"锁定的教学班 {sid} 不在当前开课表中。")
            continue
        if sec.course_code in passed_set:
            diagnostics.append(f"锁定的课程 {sec.course_name} ({sec.course_code}) 已通过，无需重复修读。")
            continue
        missing_pre = check_prereqs_for_course(
            sec.course_code, sec.prerequisites, passed_set, plan
        )
        if missing_pre:
            diagnostics.append(
                f"锁定的课程 {sec.course_name} 缺少先修课：{', '.join(missing_pre)}。"
            )
            return ScheduleResult(
                success=False, diagnostics=diagnostics, eligible_count=len(eligible)
            )
        for prev in locked_sections:
            if sections_conflict(sec, prev):
                diagnostics.append(
                    f"锁定的两门课程存在时间或课程冲突：{prev.course_name}({prev.section_id}) 与 {sec.course_name}({sec.section_id})，请取消其中一个锁定。"
                )
                return ScheduleResult(
                    success=False, diagnostics=diagnostics, eligible_count=len(eligible)
                )
        locked_sections.append(sec)
        locked_codes.add(sec.course_code)

    locked_credits = sum(s.credits for s in locked_sections)
    if locked_credits > prefs.max_credits:
        diagnostics.append(
            f"已锁定课程总学分（{locked_credits}）已超过设定的学分上限（{prefs.max_credits}），请调高学分上限或减少锁定课程。"
        )
        return ScheduleResult(
            success=False, diagnostics=diagnostics, eligible_count=len(eligible)
        )

    # 3. 按课程代码分组（排除已锁定的课程代码）
    by_course: dict[str, list[CourseOffering]] = defaultdict(list)
    for off in eligible:
        if off.course_code not in locked_codes:
            by_course[off.course_code].append(off)
    grouped = list(by_course.values())

    max_possible_credits = locked_credits + sum(g[0].credits for g in grouped)
    if max_possible_credits < prefs.min_credits:
        diagnostics.append(
            f"当前满足先修条件且未修的课程总学分上限仅为 {max_possible_credits:.1f} 学分，低于你设置的最低学分（{prefs.min_credits:.1f}）。建议适当调低最低学分要求或减少排除课程。"
        )
        return ScheduleResult(
            success=False, diagnostics=diagnostics, eligible_count=len(eligible)
        )

    # 4. 三种差异化策略求解
    profiles = [
        _StrategyProfile(
            name="方案一：综合最优方案",
            desc="综合平衡毕业缺口补齐、先修链推进与你的作息偏好",
            deficit_weight=5.0,
            mandatory_weight=24.0,
            early_8am_penalty=8.0,
            evening_penalty=6.0,
            free_block_penalty=14.0,
            travel_penalty=7.0,
        ),
        _StrategyProfile(
            name="方案二：毕业进度优先方案",
            desc="优先抢修缺口最大的必修与通识核心模块，最快推进毕业进度",
            deficit_weight=9.5,
            mandatory_weight=38.0,
            early_8am_penalty=2.0,
            evening_penalty=2.0,
            free_block_penalty=5.0,
            travel_penalty=3.0,
        ),
        _StrategyProfile(
            name="方案三：作息舒适方案",
            desc="强力避开早八、晚课与跨区赶课，优先满足留空时段，学习节奏更轻松",
            deficit_weight=3.5,
            mandatory_weight=16.0,
            early_8am_penalty=18.0,
            evening_penalty=14.0,
            free_block_penalty=22.0,
            travel_penalty=15.0,
        ),
    ]

    candidates: list[ScheduleCandidate] = []
    seen_sets: set[frozenset[str]] = set()
    used_section_ids: set[str] = set()

    for prof in profiles:
        cand = _solve_single_profile(
            grouped_offerings=grouped,
            locked_sections=locked_sections,
            audit=audit,
            prefs=prefs,
            profile=prof,
            penalize_section_ids=used_section_ids if used_section_ids else None,
        )
        if cand is not None:
            sig = frozenset(s.section_id for s in cand.sections)
            if sig not in seen_sets:
                seen_sets.add(sig)
                used_section_ids.update(sig)
                candidates.append(cand)
            else:
                # 尝试排除一个非锁定教学班来构造有区分度的备选方案
                unlocked = [
                    s for s in cand.sections if s.section_id not in prefs.locked_section_ids
                ]
                for drop_sec in reversed(unlocked):
                    alt_groups = [
                        [s for s in g if s.section_id != drop_sec.section_id]
                        for g in grouped
                    ]
                    alt_groups = [g for g in alt_groups if g]
                    alt_cand = _solve_single_profile(
                        grouped_offerings=alt_groups,
                        locked_sections=locked_sections,
                        audit=audit,
                        prefs=prefs,
                        profile=prof,
                        penalize_section_ids=used_section_ids,
                    )
                    if alt_cand is not None:
                        alt_sig = frozenset(s.section_id for s in alt_cand.sections)
                        if alt_sig not in seen_sets:
                            seen_sets.add(alt_sig)
                            used_section_ids.update(alt_sig)
                            candidates.append(alt_cand)
                            break

    if not candidates:
        diagnostics.append(
            f"在学分区间 [{prefs.min_credits}, {prefs.max_credits}] 内无法找到完全无时间冲突的组合。可能原因：高学分必修课之间时间密集重叠。建议适当放宽最低学分下限（如降至 {max(0.0, prefs.min_credits - 4):.1f}）或减少锁定课程。"
        )
        return ScheduleResult(
            success=False, diagnostics=diagnostics, eligible_count=len(eligible)
        )

    return ScheduleResult(
        success=True,
        candidates=candidates,
        diagnostics=diagnostics,
        eligible_count=len(eligible),
    )

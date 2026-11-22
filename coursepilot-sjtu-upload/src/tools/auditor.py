"""毕业学分缺口与先修课拓扑审计引擎（100% 确定性 Python 规则计算）。"""

from __future__ import annotations

from src.core.schemas import (
    AuditReport,
    CourseOffering,
    CourseRecord,
    CurriculumPlan,
    ModuleAuditItem,
    PrereqBlockedItem,
)


def _expand_passed_codes(
    records: list[CourseRecord], plan: CurriculumPlan
) -> set[str]:
    """计算已通过课程集合，并应用培养方案中的课程替代关系。"""
    passed = {r.course_code.upper() for r in records if r.passed}
    # 如果用户修过了可替代课程，则视作目标课程也已满足
    for target_code, eq_list in plan.equivalents.items():
        if any(eq.upper() in passed for eq in eq_list):
            passed.add(target_code.upper())
    return passed


def check_prereqs_for_course(
    course_code: str,
    offering_prereqs: list[str],
    passed_codes: set[str],
    plan: CurriculumPlan,
) -> list[str]:
    """返回某门课当前缺失的先修课程代码列表。"""
    required = set(p.upper() for p in offering_prereqs)
    required.update(p.upper() for p in plan.prerequisites.get(course_code.upper(), []))
    missing = sorted(p for p in required if p not in passed_codes)
    return missing


def classify_unmapped_courses_with_llm(
    records: list[CourseRecord],
    plan: CurriculumPlan,
    llm_client,
) -> dict[str, str]:
    """当培养方案文档未逐一列出课程代码时，调用交大官方大模型将已修课程归类到对应模块。"""
    valid_modules = [m.module_name for m in plan.modules]
    if not valid_modules or not getattr(llm_client, "is_configured", False):
        return {}

    unmapped = []
    seen = set()
    for r in records:
        code = r.course_code.upper()
        if code in seen:
            continue
        seen.add(code)
        mapped = plan.course_module_map.get(code, r.module)
        if mapped not in valid_modules:
            unmapped.append(f"{code} {r.course_name} ({r.credits}学分)")

    if not unmapped:
        return {}

    mod_desc = "\n".join(
        f"- {m.module_name}（要求{m.required_credits}学分）：{m.description}"
        for m in plan.modules
    )
    system_prompt = (
        "你是上海交通大学教务课程归类助手。请将下方每门已修课程归类到培养方案中最匹配的一个模块名称。\n"
        "严格输出 JSON 对象，格式为 {\"课程代码\": \"模块名称\"}，模块名称必须完全取自给定列表，不要多余解释。"
    )
    user_prompt = (
        f"培养方案：{plan.school_name} {plan.major_name}\n"
        f"可选模块列表：\n{mod_desc}\n\n"
        f"待归类课程：\n" + "\n".join(unmapped[:80])
    )
    data = llm_client.chat_json(system_prompt, user_prompt, temperature=0.1)
    if not isinstance(data, dict):
        return {}
    return {
        str(k).strip().upper(): str(v).strip()
        for k, v in data.items()
        if str(v).strip() in valid_modules
    }


def audit_graduation_progress(
    records: list[CourseRecord],
    plan: CurriculumPlan,
    offerings: list[CourseOffering] | None = None,
) -> AuditReport:
    """对照培养方案核查各模块学分缺口、缺失必修课及本学期受先修限制的课程。"""
    passed_records = [r for r in records if r.passed]
    passed_codes = _expand_passed_codes(records, plan)

    # 按培养方案的 course_module_map 校准课程模块归属（支持形势与政策等多学期累加）
    module_credits: dict[str, float] = {m.module_name: 0.0 for m in plan.modules}
    seen_keys: set[tuple[str, str]] = set()

    for idx, rec in enumerate(passed_records):
        code = rec.course_code.upper()
        dedup_key = (code, rec.term or str(idx)) if code in {"MARX1205", "PSY1202"} else (code, "once")
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        mapped_module = plan.course_module_map.get(code, rec.module)
        if mapped_module in module_credits:
            module_credits[mapped_module] += rec.credits
        else:
            # 兜底归类：尝试按常见模块关键字匹配（兼容“个性化教育课程”或“个性化模块”）
            fallback_mod = next(
                (m for m in module_credits if "个性化" in m),
                next(iter(module_credits), None),
            )
            if fallback_mod:
                module_credits[fallback_mod] += rec.credits

    module_items: list[ModuleAuditItem] = []
    total_completed = 0.0

    for mod in plan.modules:
        completed = round(module_credits.get(mod.module_name, 0.0), 1)
        remaining = round(max(0.0, mod.required_credits - completed), 1)
        pct = (
            100.0
            if mod.required_credits <= 0
            else round(min(100.0, completed / mod.required_credits * 100.0), 1)
        )
        missing_mandatory = [
            c.upper() for c in mod.mandatory_courses if c.upper() not in passed_codes
        ]
        module_items.append(
            ModuleAuditItem(
                module_name=mod.module_name,
                required_credits=mod.required_credits,
                completed_credits=completed,
                remaining_credits=remaining,
                completion_pct=pct,
                missing_mandatory_courses=missing_mandatory,
            )
        )
        total_completed += completed

    total_completed = round(total_completed, 1)
    total_remaining = round(max(0.0, plan.total_required_credits - total_completed), 1)

    # 检查开课表中因先修课不足而被拦截的课程
    blocked_items: list[PrereqBlockedItem] = []
    if offerings:
        checked_courses: set[str] = set()
        for off in offerings:
            code = off.course_code.upper()
            if code in checked_courses or code in passed_codes:
                continue
            checked_courses.add(code)
            missing = check_prereqs_for_course(
                code, off.prerequisites, passed_codes, plan
            )
            if missing:
                blocked_items.append(
                    PrereqBlockedItem(
                        course_code=code,
                        course_name=off.course_name,
                        missing_prereqs=missing,
                    )
                )

    return AuditReport(
        plan_name=f"{plan.school_name} - {plan.major_name} ({plan.grade_year})",
        total_required_credits=plan.total_required_credits,
        total_completed_credits=total_completed,
        total_remaining_credits=total_remaining,
        module_items=module_items,
        blocked_courses=blocked_items,
        passed_course_codes=sorted(passed_codes),
    )

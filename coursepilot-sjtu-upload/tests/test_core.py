"""CoursePilot SJTU 核心引擎单元测试（自包含测试夹具，无外部样例文件依赖）。"""

from __future__ import annotations

from datetime import date

from icalendar import Calendar

from src.core.privacy import mask_sensitive_text
from src.core.schemas import (
    CourseOffering,
    CourseRecord,
    CurriculumPlan,
    FreeBlock,
    ModuleRequirement,
    SchedulePreferences,
    TimeSlot,
)
from src.tools.auditor import audit_graduation_progress
from src.tools.exporter import export_markdown_report, export_schedule_to_ics
from src.tools.intent_parser import rule_parse_preferences
from src.tools.parser import parse_transcript_pasted_text
from src.tools.scheduler import generate_schedules, sections_conflict


def _make_fixture():
    plan = CurriculumPlan(
        plan_id="test-plan",
        school_name="测试学院",
        major_name="测试专业",
        grade_year="2024级",
        total_required_credits=30.0,
        modules=[
            ModuleRequirement(
                module_name="专业必修",
                required_credits=16.0,
                mandatory_courses=["C101", "C201", "C202", "C301"],
            ),
            ModuleRequirement(module_name="通识核心-人文", required_credits=6.0),
            ModuleRequirement(module_name="专业选修", required_credits=8.0),
        ],
        prerequisites={"C201": ["C101"], "C301": ["C201"]},
        course_module_map={
            "C101": "专业必修",
            "C201": "专业必修",
            "C202": "专业必修",
            "C301": "专业必修",
            "H101": "通识核心-人文",
            "H102": "通识核心-人文",
            "E201": "专业选修",
            "E202": "专业选修",
        },
    )
    records = [
        CourseRecord(course_code="C101", course_name="导论", credits=4.0, module="专业必修", passed=True),
        CourseRecord(course_code="H101", course_name="人文一", credits=2.0, module="通识核心-人文", passed=True),
    ]
    offerings = [
        CourseOffering(
            section_id="C201-01",
            course_code="C201",
            course_name="核心课A",
            credits=4.0,
            module="专业必修",
            teacher="张老师",
            building="东中院101",
            zone="闵行东区",
            time_slots=[TimeSlot(day=1, start_period=1, end_period=2)],
            prerequisites=["C101"],
        ),
        CourseOffering(
            section_id="C201-02",
            course_code="C201",
            course_name="核心课A",
            credits=4.0,
            module="专业必修",
            teacher="张老师",
            building="东中院102",
            zone="闵行东区",
            time_slots=[TimeSlot(day=1, start_period=3, end_period=4)],
            prerequisites=["C101"],
        ),
        CourseOffering(
            section_id="C202-01",
            course_code="C202",
            course_name="核心课B",
            credits=4.0,
            module="专业必修",
            teacher="李老师",
            building="东上院201",
            zone="闵行东区",
            time_slots=[TimeSlot(day=2, start_period=3, end_period=4)],
        ),
        CourseOffering(
            section_id="C301-01",
            course_code="C301",
            course_name="高阶课C",
            credits=4.0,
            module="专业必修",
            teacher="王老师",
            building="电院3-100",
            zone="电院群楼",
            time_slots=[TimeSlot(day=4, start_period=3, end_period=4)],
            prerequisites=["C201"],
        ),
        CourseOffering(
            section_id="H102-01",
            course_code="H102",
            course_name="人文二",
            credits=2.0,
            module="通识核心-人文",
            teacher="周老师",
            building="东上院100",
            zone="闵行东区",
            time_slots=[TimeSlot(day=4, start_period=7, end_period=8)],
        ),
        CourseOffering(
            section_id="E201-01",
            course_code="E201",
            course_name="选修一",
            credits=4.0,
            module="专业选修",
            teacher="陈老师",
            building="中院105",
            zone="闵行西区",
            time_slots=[TimeSlot(day=5, start_period=3, end_period=4)],
        ),
        CourseOffering(
            section_id="E202-01",
            course_code="E202",
            course_name="选修二",
            credits=4.0,
            module="专业选修",
            teacher="刘老师",
            building="东下院105",
            zone="闵行东区",
            time_slots=[TimeSlot(day=3, start_period=7, end_period=8)],
        ),
    ]
    return plan, records, offerings


def test_privacy_masking_and_pasted_transcript():
    raw = "姓名：李四 学号：524031910999 手机 13987654321 邮箱 lisi@sjtu.edu.cn GPA: 3.85\nC101 导论 4.0 92\nH101 人文一 2.0 通过"
    masked, log = mask_sensitive_text(raw)
    assert "李四" not in masked
    assert "524031910999" not in masked
    assert "13987654321" not in masked
    assert "lisi@sjtu.edu.cn" not in masked
    assert "3.85" not in masked
    assert log.total_masked() >= 4

    recs, _ = parse_transcript_pasted_text(raw)
    assert len(recs) == 2
    assert recs[0].course_code == "C101"


def test_audit_and_prereq_blocking():
    plan, records, offerings = _make_fixture()
    report = audit_graduation_progress(records, plan, offerings)
    assert report.total_completed_credits == 6.0
    assert report.total_remaining_credits == 24.0
    blocked_map = {b.course_code: b.missing_prereqs for b in report.blocked_courses}
    assert "C301" in blocked_map
    assert "C201" in blocked_map["C301"]
    assert "C201" not in blocked_map


def test_scheduler_zero_conflicts_and_export():
    plan, records, offerings = _make_fixture()
    audit = audit_graduation_progress(records, plan, offerings)
    prefs = SchedulePreferences(
        min_credits=10.0,
        max_credits=14.0,
        avoid_early_8am=True,
        free_blocks=[FreeBlock(day=3, half_day="afternoon")],
    )
    res = generate_schedules(offerings, audit, plan, prefs)
    assert res.success is True
    assert len(res.candidates) >= 1

    for cand in res.candidates:
        assert 10.0 <= cand.total_credits <= 14.0
        for i in range(len(cand.sections)):
            assert cand.sections[i].course_code != "C301"
            for j in range(i + 1, len(cand.sections)):
                assert not sections_conflict(cand.sections[i], cand.sections[j])

    ics = export_schedule_to_ics(res.candidates[0], semester_start_monday=date(2026, 9, 14))
    cal = Calendar.from_ical(ics)
    assert len([c for c in cal.walk() if c.name == "VEVENT"]) >= 1
    md = export_markdown_report(audit, res.candidates[0])
    assert "CoursePilot SJTU" in md


def test_rule_parse_preferences():
    prefs = rule_parse_preferences(
        "这学期想选16到20学分，尽量避开早八，不要晚课，周三下午留空，优先补人文通识"
    )
    assert prefs.min_credits == 16.0
    assert prefs.max_credits == 20.0
    assert prefs.avoid_early_8am is True
    assert prefs.avoid_evening is True
    assert "通识核心-人文" in prefs.prioritize_modules
    assert any(fb.day == 3 and fb.half_day == "afternoon" for fb in prefs.free_blocks)

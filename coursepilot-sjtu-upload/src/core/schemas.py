"""核心数据结构定义（基于 Pydantic 强类型校验）。"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator


WeekType = Literal["all", "odd", "even"]
HalfDay = Literal["morning", "afternoon", "evening"]


class TimeSlot(BaseModel):
    """单次上课时间段（交大 1-13 节制）。"""

    day: int = Field(..., ge=1, le=7, description="星期 1-7（1=周一）")
    start_period: int = Field(..., ge=1, le=13, description="起始节次")
    end_period: int = Field(..., ge=1, le=13, description="结束节次")
    week_type: WeekType = Field(default="all", description="全周/单周/双周")

    @field_validator("end_period")
    @classmethod
    def check_period_order(cls, v: int, info) -> int:
        start = info.data.get("start_period")
        if start is not None and v < start:
            raise ValueError("结束节次不能小于起始节次")
        return v

    def conflicts_with(self, other: "TimeSlot") -> bool:
        if self.day != other.day:
            return False
        if (
            self.week_type != "all"
            and other.week_type != "all"
            and self.week_type != other.week_type
        ):
            return False
        return not (
            self.end_period < other.start_period
            or other.end_period < self.start_period
        )


class CourseRecord(BaseModel):
    """已修/在修课程记录（已脱敏，不含姓名、学号与具体分数）。"""

    course_code: str
    course_name: str
    credits: float = Field(..., gt=0)
    module: str = Field(default="未分类")
    passed: bool = True
    term: str = Field(default="")


class ModuleRequirement(BaseModel):
    """培养方案单个模块的学分要求。"""

    module_name: str
    required_credits: float = Field(..., ge=0)
    mandatory_courses: list[str] = Field(
        default_factory=list, description="该模块内必须修读的课程代码"
    )
    description: str = ""


class CurriculumPlan(BaseModel):
    """学院培养方案结构化表示。"""

    plan_id: str
    school_name: str
    major_name: str
    grade_year: str
    total_required_credits: float
    modules: list[ModuleRequirement]
    prerequisites: dict[str, list[str]] = Field(
        default_factory=dict, description="课程代码 -> 先修课程代码列表"
    )
    course_module_map: dict[str, str] = Field(
        default_factory=dict, description="课程代码 -> 归属培养方案模块"
    )
    equivalents: dict[str, list[str]] = Field(
        default_factory=dict, description="目标课程代码 -> 可替代课程代码列表"
    )


class CourseOffering(BaseModel):
    """本学期开课教学班信息。"""

    section_id: str
    course_code: str
    course_name: str
    credits: float = Field(..., gt=0)
    module: str
    teacher: str
    building: str
    zone: str = Field(default="闵行东区", description="教学楼片区，用于跨区连堂评估")
    time_slots: list[TimeSlot]
    prerequisites: list[str] = Field(default_factory=list)
    capacity_note: str = Field(default="常规选课")


class FreeBlock(BaseModel):
    """希望留空的时间块。"""

    day: int = Field(..., ge=1, le=7)
    half_day: HalfDay


class SchedulePreferences(BaseModel):
    """用户自然语言解析后的排课约束偏好。"""

    min_credits: float = Field(default=14.0, ge=0, le=35)
    max_credits: float = Field(default=22.0, ge=1, le=35)
    avoid_early_8am: bool = Field(default=True, description="尽量避开 1-2 节早八")
    avoid_evening: bool = Field(default=False, description="尽量避开 11-13 节晚课")
    minimize_building_travel: bool = Field(
        default=True, description="尽量减少相邻节次跨片区连堂"
    )
    prioritize_modules: list[str] = Field(
        default_factory=list, description="优先想补齐的模块名称"
    )
    free_blocks: list[FreeBlock] = Field(
        default_factory=list, description="希望留空的半天时段"
    )
    locked_section_ids: list[str] = Field(
        default_factory=list, description="强制锁定的教学班 ID"
    )
    excluded_course_codes: list[str] = Field(
        default_factory=list, description="明确不选的课程代码"
    )
    excluded_section_ids: list[str] = Field(
        default_factory=list, description="明确排除的教学班 ID"
    )
    raw_query: str = ""


class ModuleAuditItem(BaseModel):
    module_name: str
    required_credits: float
    completed_credits: float
    remaining_credits: float
    completion_pct: float
    missing_mandatory_courses: list[str] = Field(default_factory=list)


class PrereqBlockedItem(BaseModel):
    course_code: str
    course_name: str
    missing_prereqs: list[str]


class AuditReport(BaseModel):
    """毕业学分缺口与先修课核查报告。"""

    plan_name: str
    total_required_credits: float
    total_completed_credits: float
    total_remaining_credits: float
    module_items: list[ModuleAuditItem]
    blocked_courses: list[PrereqBlockedItem]
    passed_course_codes: list[str]


class ScheduleCandidate(BaseModel):
    """单套无冲突推荐课表方案。"""

    strategy_name: str
    strategy_desc: str
    sections: list[CourseOffering]
    total_credits: float
    score: float
    early_8am_count: int
    evening_count: int
    cross_zone_warnings: list[str] = Field(default_factory=list)
    covered_modules: dict[str, float] = Field(default_factory=dict)
    highlights: list[str] = Field(default_factory=list)


class ScheduleResult(BaseModel):
    """排课引擎返回结果（含多方案或无解诊断）。"""

    success: bool
    candidates: list[ScheduleCandidate] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    eligible_count: int = 0

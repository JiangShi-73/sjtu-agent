"""CoursePilot SJTU —— 交大培养方案核查与智能排课智能体."""

from __future__ import annotations

import hashlib
import html
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src.core.config import get_config
from src.core.llm_client import LLMClient
from src.core.schemas import (
    CourseOffering,
    CurriculumPlan,
    FreeBlock,
    ScheduleCandidate,
    SchedulePreferences,
)
from src.tools.auditor import (
    audit_graduation_progress,
    classify_unmapped_courses_with_llm,
)
from src.tools.exporter import export_markdown_report, export_schedule_to_ics
from src.tools.intent_parser import parse_user_preferences
from src.tools.parser import (
    load_curriculum_json,
    load_offerings_csv,
    parse_curriculum_docx_with_llm,
    parse_curriculum_pdf_with_llm,
    parse_offerings_pdf_with_llm,
    parse_transcript_csv_or_excel,
    parse_transcript_docx,
    parse_transcript_pasted_text,
    parse_transcript_pdf,
)
from src.tools.scheduler import generate_schedules


st.set_page_config(
    page_title="交大智排 | 培养方案核查与智能排课助手",
    page_icon="🎓",
    layout="wide",
)

_MODULE_COLORS: dict[str, tuple[str, str]] = {
    "专业必修": ("#dbeafe", "#1d4ed8"),
    "专业选修": ("#ede9fe", "#6d28d9"),
    "实践环节": ("#fef3c7", "#b45309"),
    "通识核心-人文": ("#fce7f3", "#be185d"),
    "通识核心-社科": ("#dcfce7", "#15803d"),
    "通识核心-自然": ("#cffafe", "#0e7490"),
    "公共基础": ("#f1f5f9", "#475569"),
    "个性化模块": ("#ffedd5", "#c2410c"),
}

_DAY_LABELS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五"}

_PERIOD_TIMES = {
    1: "08:00",
    2: "08:55",
    3: "10:00",
    4: "10:55",
    5: "12:00",
    6: "12:55",
    7: "14:00",
    8: "14:55",
    9: "16:00",
    10: "16:55",
    11: "18:00",
    12: "18:55",
    13: "19:50",
}

_BLANK_OFFERINGS_CSV = (
    "section_id,course_code,course_name,credits,module,teacher,building,zone,time_slots,prerequisites,capacity_note\n"
    "COURSE-01,CS2304,课程名称,3.0,专业必修,授课教师,东中院1-201,闵行东区,周一 3-4节;周三 3-4节,,常规选课\n"
)

_BLANK_TRANSCRIPT_CSV = (
    "course_code,course_name,credits,module,passed,term\n"
    "MATH1201,高等数学(A)1,6.0,公共基础,true,2024-2025-1\n"
)


def _render_timetable_html(candidate: ScheduleCandidate) -> str:
    """使用 CSS Grid 渲染周一至周五 × 1-13 节可视化周课表（支持多节撑满与单双周共存）。"""
    grouped_blocks: dict[tuple[int, int, int], list[tuple[CourseOffering, str]]] = defaultdict(list)
    for sec in candidate.sections:
        for slot in sec.time_slots:
            if slot.day > 5:
                continue
            grouped_blocks[(slot.day, slot.start_period, slot.end_period)].append(
                (sec, slot.week_type)
            )

    parts: list[str] = [
        """
        <style>
        .cp-grid-wrap {
            width: 100%;
            overflow-x: auto;
            border-radius: 10px;
            border: 1px solid rgba(128, 128, 128, 0.25);
            background: rgba(255, 255, 255, 0.96);
            padding: 2px;
        }
        .cp-grid {
            display: grid;
            grid-template-columns: 66px repeat(5, minmax(120px, 1fr));
            grid-template-rows: 36px repeat(13, minmax(44px, auto));
            gap: 2px;
            background: #e5e7eb;
            min-width: 680px;
        }
        .cp-hdr {
            background: #8b1d2c;
            color: #ffffff;
            font-weight: 700;
            font-size: 13px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .cp-period {
            background: #f8fafc;
            color: #475569;
            font-size: 11px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            line-height: 1.25;
        }
        .cp-empty { background: #ffffff; }
        .cp-slot-group {
            display: flex;
            flex-direction: column;
            gap: 3px;
            padding: 3px;
            background: #ffffff;
            z-index: 2;
            box-sizing: border-box;
        }
        .cp-card {
            flex: 1 1 auto;
            border-radius: 6px;
            padding: 6px 8px;
            font-size: 12px;
            line-height: 1.35;
            color: #111827;
            display: flex;
            flex-direction: column;
            justify-content: center;
            box-sizing: border-box;
            overflow-wrap: anywhere;
        }
        .cp-card-title { font-weight: 700; font-size: 12.5px; margin-bottom: 2px; }
        .cp-card-meta { color: #374151; font-size: 11px; }
        </style>
        <div class="cp-grid-wrap"><div class="cp-grid">
        """,
        "<div class='cp-hdr' style='grid-column:1;grid-row:1;'>节次</div>",
    ]

    for d in range(1, 6):
        parts.append(
            f"<div class='cp-hdr' style='grid-column:{d + 1};grid-row:1;'>{_DAY_LABELS[d]}</div>"
        )

    for p in range(1, 14):
        row_idx = p + 1
        parts.append(
            f"<div class='cp-period' style='grid-column:1;grid-row:{row_idx};'>"
            f"<b>第{p}节</b><span>{_PERIOD_TIMES[p]}</span></div>"
        )
        for d in range(1, 6):
            parts.append(
                f"<div class='cp-empty' style='grid-column:{d + 1};grid-row:{row_idx};'></div>"
            )

    for (day, start_p, end_p), items in grouped_blocks.items():
        span = end_p - start_p + 1
        col_idx = day + 1
        row_start = start_p + 1
        cards_html: list[str] = []
        for sec, wt in items:
            bg, border = _MODULE_COLORS.get(sec.module, ("#f3f4f6", "#6b7280"))
            wt_tag = "" if wt == "all" else ("【单周】" if wt == "odd" else "【双周】")
            cards_html.append(
                f"<div class='cp-card' style='background:{bg};border-left:4px solid {border};'>"
                f"<div class='cp-card-title'>{html.escape(sec.course_name)} {wt_tag}</div>"
                f"<div class='cp-card-meta'>{html.escape(sec.course_code)} · {sec.credits}学分 · {html.escape(sec.teacher)}</div>"
                f"<div class='cp-card-meta'>📍 {html.escape(sec.building)} ({html.escape(sec.zone)})</div>"
                f"</div>"
            )
        parts.append(
            f"<div class='cp-slot-group' style='grid-column:{col_idx};grid-row:{row_start} / span {span};'>"
            + "".join(cards_html)
            + "</div>"
        )

    parts.append("</div></div>")
    return "".join(parts)


def _format_slots(sec: CourseOffering) -> str:
    day_cn = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
    parts = []
    for s in sec.time_slots:
        wt = "" if s.week_type == "all" else ("(单)" if s.week_type == "odd" else "(双)")
        parts.append(f"{day_cn[s.day]} {s.start_period}-{s.end_period}节{wt}")
    return "；".join(parts)


def _init_session_state() -> None:
    defaults = {
        "locked_ids": [],
        "excluded_codes": [],
        "active_candidate": None,
        "pref_credits": (16.0, 20.0),
        "pref_avoid_8am": True,
        "pref_avoid_eve": False,
        "pref_min_travel": True,
        "pref_free_wed": False,
        "pref_prioritize_mods": [],
        "pref_extra_free_blocks": [],
        "pref_parse_source": "当前快捷配置",
        "parsed_pdf_plan_cache": {},
        "parsed_pdf_offerings_cache": {},
        "course_module_classify_cache": {},
        "ai_schedule_advice": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def main() -> None:
    _init_session_state()
    cfg = get_config()

    # 防止浏览器自动翻译篡改 React DOM 引发 removeChild 报错
    st.markdown(
        '<div translate="no" class="notranslate" style="display:none;"></div>',
        unsafe_allow_html=True,
    )
    st.title("🎓 交大智排 · 培养方案核查与排课助手")
    st.caption(
        "上海交通大学“培养方案核查与智能排课”智能体 · 交大官方大模型驱动 · 本地隐私脱敏 · 确定性无冲突排课"
    )

    # ---------------- 左侧边栏：官方模型选择 + 用户真实数据导入 ----------------
    with st.sidebar:
        st.header("🤖 交大官方大模型")
        model_options = ["deepseek-chat", "qwen", "deepseek-reasoner"]
        default_idx = (
            model_options.index(cfg.llm_model)
            if cfg.llm_model in model_options
            else 0
        )
        selected_model = st.selectbox(
            "选择推理模型（models.sjtu.edu.cn）",
            options=model_options,
            index=default_idx,
            help="DeepSeek V4 Flash (deepseek-chat / deepseek-reasoner) 或 Qwen3.8-27B (qwen)",
        )
        llm_client = LLMClient(config=cfg, model_override=selected_model)
        if llm_client.is_configured:
            st.success(f"官方 API 已就绪 · `{selected_model}`")
        else:
            st.error("未检测到有效 LLM_API_KEY，请在服务器 .env 中配置交大官方 API Key。")

        st.divider()
        st.header("📂 导入你的学业数据")

        transcript_mode = st.radio(
            "1. 个人已修课程（成绩单）",
            options=["直接粘贴成绩页文字", "上传文件 (CSV / Excel / PDF / DOCX)"],
            index=0,
        )
        pasted_text = ""
        transcript_file = None
        if transcript_mode == "直接粘贴成绩页文字":
            pasted_text = st.text_area(
                "从教学信息服务网复制已修课程文字粘贴到这里：",
                height=130,
                placeholder="每行含课程代码、课程名、学分，例如：\nCS1501 程序设计思想与方法 4.0 通过\nMATH1201 高等数学(A)1 6.0 90",
            )
        else:
            transcript_file = st.file_uploader(
                "上传成绩单文件", type=["csv", "xlsx", "xls", "pdf", "docx"]
            )

        curriculum_file = st.file_uploader(
            "2. 上传学院培养计划 (DOCX / PDF / JSON)",
            type=["docx", "pdf", "json"],
            help="直接上传从教务系统下载的培养计划 Word (.docx) 或 PDF，交大官方大模型将自动提取表格中的模块学分与先修关系。",
        )

        offerings_file = st.file_uploader(
            "3. 上传本学期课表/开课表 (PDF / CSV / Excel，选填)",
            type=["pdf", "csv", "xlsx", "xls"],
            help="支持直接上传从教务系统下载的课表 PDF（由交大官方大模型自动解析）或 CSV/Excel；仅做毕业学分核查时可不传。",
        )

        with st.expander("📄 下载标准 CSV 模板"):
            st.download_button(
                "下载成绩单 CSV 模板",
                data=_BLANK_TRANSCRIPT_CSV.encode("utf-8-sig"),
                file_name="transcript_template.csv",
                mime="text/csv",
                use_container_width=True,
            )
            st.download_button(
                "下载开课表 CSV 模板",
                data=_BLANK_OFFERINGS_CSV.encode("utf-8-sig"),
                file_name="offerings_template.csv",
                mime="text/csv",
                use_container_width=True,
            )

    # ---------------- 检查必要输入，未提供时展示清晰引导页 ----------------
    has_transcript = (
        bool(pasted_text.strip())
        if transcript_mode == "直接粘贴成绩页文字"
        else transcript_file is not None
    )
    if not has_transcript or curriculum_file is None:
        st.info("请在左侧边栏导入你的**个人已修课程**和**学院培养方案**，即可开始核查与排课。")
        g1, g2, g3 = st.columns(3)
        with g1:
            with st.container(border=True):
                st.markdown("#### ① 导入已修课程")
                st.write(
                    "支持直接从教学信息服务网复制粘贴成绩页文字，或上传 CSV / Excel / PDF。"
                    "系统会在本地自动剥离姓名、学号、手机号与具体分数。"
                )
        with g2:
            with st.container(border=True):
                st.markdown("#### ② 上传学院培养计划")
                st.write(
                    "直接上传从教务网下载的培养计划 **Word (.docx)**、**PDF**（由交大官方大模型自动解析表格与先修课）"
                    "或标准 **JSON** 文件。"
                )
        with g3:
            with st.container(border=True):
                st.markdown("#### ③ 上传课表并排课")
                st.write(
                    "直接上传从教务网下载的课表 **PDF**（或 CSV/Excel）后，即可输入自然语言偏好，"
                    "由确定性算法生成严格零冲突的周课表并导出 `.ics` 日历。"
                )
        return

    # ---------------- 解析成绩单（先本地脱敏）、培养方案与开课表 ----------------
    try:
        if transcript_mode == "直接粘贴成绩页文字":
            records, privacy_log = parse_transcript_pasted_text(pasted_text)
            if not records:
                st.warning(
                    "未从粘贴文本中识别出标准课程行（需要包含课程代码、课程名与学分，例如 `CS2301 数据结构 4.0`），请检查粘贴内容。"
                )
                return
        else:
            fname_low = transcript_file.name.lower()
            if fname_low.endswith(".pdf"):
                records, privacy_log = parse_transcript_pdf(transcript_file.read())
            elif fname_low.endswith(".docx"):
                records, privacy_log = parse_transcript_docx(transcript_file.read())
            else:
                records, privacy_log = parse_transcript_csv_or_excel(
                    transcript_file, filename=transcript_file.name
                )

        curr_low = curriculum_file.name.lower()
        if curr_low.endswith((".docx", ".pdf")):
            raw_bytes = curriculum_file.read()
            file_hash = hashlib.md5(raw_bytes).hexdigest()
            cache = st.session_state["parsed_pdf_plan_cache"]
            if file_hash in cache:
                plan = CurriculumPlan.model_validate(cache[file_hash])
            else:
                file_label = "Word (.docx)" if curr_low.endswith(".docx") else "PDF"
                with st.spinner(
                    f"正在调用交大官方大模型（{selected_model}）解析培养计划 {file_label}..."
                ):
                    if curr_low.endswith(".docx"):
                        plan = parse_curriculum_docx_with_llm(raw_bytes, llm_client)
                    else:
                        plan = parse_curriculum_pdf_with_llm(raw_bytes, llm_client)
                    cache[file_hash] = plan.model_dump()
        else:
            plan = load_curriculum_json(curriculum_file)

        offerings: list[CourseOffering] = []
        if offerings_file is not None:
            if offerings_file.name.lower().endswith(".pdf"):
                off_bytes = offerings_file.read()
                off_hash = hashlib.md5(off_bytes).hexdigest()
                off_cache = st.session_state["parsed_pdf_offerings_cache"]
                if off_hash in off_cache:
                    offerings = [CourseOffering.model_validate(x) for x in off_cache[off_hash]]
                else:
                    with st.spinner(
                        f"正在调用交大官方大模型（{selected_model}）解析课表 PDF..."
                    ):
                        offerings = parse_offerings_pdf_with_llm(off_bytes, llm_client)
                        off_cache[off_hash] = [o.model_dump() for o in offerings]
            else:
                offerings = load_offerings_csv(offerings_file, filename=offerings_file.name)
    except Exception as exc:
        st.error(f"数据解析失败：{exc}")
        return

    with st.sidebar:
        st.divider()
        st.subheader("🛡️ 本地隐私脱敏已开启")
        st.success(
            f"已剥离敏感列：{', '.join(privacy_log.dropped_columns) if privacy_log.dropped_columns else '无'}\n\n"
            f"正则遮蔽实体：{sum(privacy_log.masked_counts.values())} 处"
        )

    with st.expander("🛡️ 隐私保护审计：点击查看本地脱敏前后对比（敏感信息绝不外传）"):
        p_col1, p_col2 = st.columns(2)
        with p_col1:
            st.caption("脱敏前（仅本地内存读取）")
            st.code(privacy_log.raw_preview, language="text")
        with p_col2:
            st.caption("脱敏后（已清除身份与分数）")
            st.code(privacy_log.masked_preview, language="text")

    # 若培养方案文档未列出全部课程代码映射，自动调用交大官方大模型智能归类一次（会话内缓存）
    cls_key = hashlib.md5(
        f"{plan.plan_id}|{','.join(sorted({r.course_code for r in records}))}".encode()
    ).hexdigest()
    cls_cache = st.session_state["course_module_classify_cache"]
    if cls_key not in cls_cache:
        with st.spinner(f"正在调用 {selected_model} 将已修课程归类至培养方案各模块..."):
            cls_cache[cls_key] = classify_unmapped_courses_with_llm(
                records, plan, llm_client
            )
    if cls_cache[cls_key]:
        plan = plan.model_copy(
            update={
                "course_module_map": {**cls_cache[cls_key], **plan.course_module_map}
            }
        )

    audit = audit_graduation_progress(records, plan, offerings or None)

    tab1, tab2, tab3 = st.tabs(
        [
            "📊 学分缺口",
            "🗓️ 智能排课",
            "📥 导出报告",
        ]
    )

    # ==================== Tab 1: 毕业学分缺口看板 ====================
    with tab1:
        st.subheader(f"培养方案核查：{audit.plan_name}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("毕业总要求学分", f"{audit.total_required_credits:.1f}")
        c2.metric("已修通过学分", f"{audit.total_completed_credits:.1f}")
        c3.metric("剩余学分缺口", f"{audit.total_remaining_credits:.1f}")
        c4.metric("已通过课程数", f"{len(audit.passed_course_codes)} 门")

        st.markdown("#### 各模块学分完成进度")
        cols = st.columns(2)
        for idx, item in enumerate(audit.module_items):
            with cols[idx % 2]:
                st.markdown(
                    f"**{item.module_name}** — 已修 {item.completed_credits} / {item.required_credits} 学分（缺口 **{item.remaining_credits}**）"
                )
                st.progress(min(1.0, item.completion_pct / 100.0))
                if item.missing_mandatory_courses:
                    st.caption(
                        f"⚠️ 尚未修读必修课：{', '.join(item.missing_mandatory_courses)}"
                    )

        if offerings:
            st.divider()
            st.markdown("#### 🚧 本学期先修课拦截预警")
            if not audit.blocked_courses:
                st.info("本学期开课表中没有因先修课不足而被拦截的课程。")
            else:
                for b in audit.blocked_courses:
                    st.warning(
                        f"**{b.course_code} {b.course_name}** 暂不可选 —— 缺少先修课：`{', '.join(b.missing_prereqs)}`"
                    )

    # ==================== Tab 2: 对话式智能排课 ====================
    with tab2:
        st.subheader("自然语言偏好 + 确定性无冲突排课")
        if not offerings:
            st.info("👈 请先在左侧上传「本学期开课表 (CSV / Excel)」（可先下载左侧空白模板查看表头格式），即可启用智能排课。")
        else:
            nl_col1, nl_col2 = st.columns([4, 1], vertical_alignment="bottom")
            with nl_col1:
                nl_query = st.text_input(
                    "用自然语言描述你的选课偏好（点击右侧按钮由交大官方大模型解析）：",
                    value="",
                    placeholder="例如：这学期想选16到20学分，避开早八，周三下午留空，优先补人文通识...",
                )
            with nl_col2:
                apply_nl = st.button("✨ 解析并应用", use_container_width=True)

            if apply_nl and nl_query.strip():
                with st.spinner(f"正在调用 {selected_model} 解析你的选课偏好..."):
                    parsed, src_label = parse_user_preferences(
                        nl_query, llm_client=llm_client
                    )
                st.session_state["pref_credits"] = (
                    max(6.0, min(30.0, parsed.min_credits)),
                    max(6.0, min(30.0, parsed.max_credits)),
                )
                st.session_state["pref_avoid_8am"] = parsed.avoid_early_8am
                st.session_state["pref_avoid_eve"] = parsed.avoid_evening
                st.session_state["pref_min_travel"] = parsed.minimize_building_travel
                st.session_state["pref_prioritize_mods"] = [
                    m for m in parsed.prioritize_modules if m in {x.module_name for x in plan.modules}
                ]
                st.session_state["pref_free_wed"] = any(
                    fb.day == 3 and fb.half_day == "afternoon" for fb in parsed.free_blocks
                )
                st.session_state["pref_extra_free_blocks"] = [
                    fb
                    for fb in parsed.free_blocks
                    if not (fb.day == 3 and fb.half_day == "afternoon")
                ]
                st.session_state["pref_parse_source"] = src_label
                st.toast(f"已通过「{src_label}」更新排课条件！")

            col_a, col_b, col_c = st.columns([2, 2, 2])
            with col_a:
                credit_range = st.slider(
                    "学期学分范围", 6.0, 30.0, step=1.0, key="pref_credits"
                )
            with col_b:
                avoid_8am_ui = st.checkbox("避开早八（1-2节）", key="pref_avoid_8am")
                avoid_eve_ui = st.checkbox("避开晚课（11-13节）", key="pref_avoid_eve")
            with col_c:
                min_travel_ui = st.checkbox("减少跨片区连堂赶课", key="pref_min_travel")
                free_wed_pm = st.checkbox("周三下午留空", key="pref_free_wed")

            all_mod_names = [m.module_name for m in plan.modules]
            st.session_state["pref_prioritize_mods"] = [
                m for m in st.session_state["pref_prioritize_mods"] if m in all_mod_names
            ]
            prioritize_mods = st.multiselect(
                "优先补齐的培养方案模块",
                options=all_mod_names,
                key="pref_prioritize_mods",
            )

            eligible_offerings = [
                o
                for o in offerings
                if o.course_code not in set(audit.passed_course_codes)
                and o.course_code not in {b.course_code for b in audit.blocked_courses}
            ]
            offering_labels = {
                o.section_id: f"{o.section_id} | {o.course_name} ({o.teacher}, {_format_slots(o)})"
                for o in eligible_offerings
            }
            course_labels = {
                o.course_code: f"{o.course_code} {o.course_name}" for o in eligible_offerings
            }

            with st.expander("🔧 局部微调：锁定必选教学班 / 排除不想上的课"):
                locked_selected = st.multiselect(
                    "锁定必选教学班（重排时固定保留）",
                    options=list(offering_labels.keys()),
                    default=[
                        sid for sid in st.session_state.locked_ids if sid in offering_labels
                    ],
                    format_func=lambda sid: offering_labels[sid],
                )
                excluded_selected = st.multiselect(
                    "排除不想选的课程",
                    options=list(course_labels.keys()),
                    default=[
                        c for c in st.session_state.excluded_codes if c in course_labels
                    ],
                    format_func=lambda code: course_labels[code],
                )
                st.session_state.locked_ids = locked_selected
                st.session_state.excluded_codes = excluded_selected

            free_blocks: list[FreeBlock] = list(st.session_state["pref_extra_free_blocks"])
            if free_wed_pm:
                free_blocks.append(FreeBlock(day=3, half_day="afternoon"))

            prefs = SchedulePreferences(
                min_credits=credit_range[0],
                max_credits=credit_range[1],
                avoid_early_8am=avoid_8am_ui,
                avoid_evening=avoid_eve_ui,
                minimize_building_travel=min_travel_ui,
                prioritize_modules=prioritize_mods,
                free_blocks=free_blocks,
                locked_section_ids=st.session_state.locked_ids,
                excluded_course_codes=st.session_state.excluded_codes,
                raw_query=nl_query,
            )
            st.caption(
                f"当前生效条件（来源：{st.session_state['pref_parse_source']}）｜"
                f"学分 [{prefs.min_credits:.0f}, {prefs.max_credits:.0f}] ｜"
                f"优先模块：{', '.join(prefs.prioritize_modules) or '按缺口自动排序'}"
            )

            result = generate_schedules(offerings, audit, plan, prefs)

            if not result.success:
                st.error("当前约束下无法找到完全无冲突课表：")
                for msg in result.diagnostics:
                    st.write(f"- {msg}")
            else:
                strategy_names = [c.strategy_name for c in result.candidates]
                chosen_name = st.radio(
                    "切换对比推荐方案（全部严格零时间冲突）：",
                    options=strategy_names,
                    horizontal=True,
                )
                active_cand = next(
                    c for c in result.candidates if c.strategy_name == chosen_name
                )
                st.session_state.active_candidate = active_cand

                st.info(
                    f"**{active_cand.strategy_desc}**\n\n"
                    f"总学分：**{active_cand.total_credits}** ｜ "
                    f"亮点：{' · '.join(active_cand.highlights)}"
                )

                if active_cand.cross_zone_warnings:
                    for w in active_cand.cross_zone_warnings:
                        st.warning(f"🚶 跨区赶课提醒：{w}")

                st.markdown(_render_timetable_html(active_cand), unsafe_allow_html=True)

                st.markdown("")
                if st.button("🤖 让交大官方大模型点评当前课表并给选课策略"):
                    with st.spinner(f"正在调用 {selected_model} 生成选课策略..."):
                        deficit_summary = "；".join(
                            f"{m.module_name}还缺{m.remaining_credits}学分"
                            for m in audit.module_items
                            if m.remaining_credits > 0
                        )
                        courses_summary = "\n".join(
                            f"- {s.course_code} {s.course_name} ({s.credits}学分, {s.module}, {s.teacher}, {_format_slots(s)}, {s.building})"
                            for s in active_cand.sections
                        )
                        blocked_summary = (
                            "、".join(
                                f"{b.course_code}({b.course_name}缺{','.join(b.missing_prereqs)})"
                                for b in audit.blocked_courses
                            )
                            or "无"
                        )
                        ai_advice = llm_client.chat_text(
                            system_prompt=(
                                "你是上海交通大学学业规划与选课顾问。请基于已脱敏的学分缺口与确定性算法生成的无冲突课表，"
                                "用简洁、接地气的口吻给出 3 条实用建议（不超过 220 字）："
                                "1) 本学期选课节奏与学业负荷评估；2) 抢课优先级与备选提醒；3) 下学期先修链衔接提示。"
                            ),
                            user_prompt=(
                                f"当前学分缺口：{deficit_summary}\n"
                                f"本学期被先修课拦截的课程：{blocked_summary}\n"
                                f"推荐方案（{active_cand.strategy_name}，共 {active_cand.total_credits} 学分）：\n{courses_summary}"
                            ),
                        )
                        if ai_advice:
                            st.session_state["ai_schedule_advice"] = ai_advice
                        else:
                            st.warning("调用交大官方大模型接口超时或返回异常，请稍后重试。")

                if st.session_state.get("ai_schedule_advice"):
                    with st.container(border=True):
                        st.markdown(f"**🤖 AI 选课顾问点评（{selected_model}）**")
                        st.markdown(st.session_state["ai_schedule_advice"])

                st.markdown("#### 本方案课程明细")
                detail_rows = [
                    {
                        "课程": f"{s.course_code} {s.course_name}",
                        "学分": s.credits,
                        "模块": s.module,
                        "教师": s.teacher,
                        "上课时间": _format_slots(s),
                        "地点": f"{s.building} ({s.zone})",
                    }
                    for s in active_cand.sections
                ]
                st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)

    # ==================== Tab 3: 一键导出 ====================
    with tab3:
        st.subheader("导出标准日历 (.ics) 与学业诊断报告")
        active_cand = st.session_state.active_candidate

        today = date.today()
        default_monday = today + timedelta(days=((7 - today.weekday()) % 7) or 7)
        c_d1, c_d2 = st.columns(2)
        with c_d1:
            start_monday = st.date_input("第 1 教学周周一日期", value=default_monday)
        with c_d2:
            total_weeks = st.number_input("总教学周数", min_value=8, max_value=20, value=16)

        c_exp1, c_exp2 = st.columns(2)
        with c_exp1:
            if active_cand is not None:
                ics_bytes = export_schedule_to_ics(
                    active_cand,
                    semester_start_monday=start_monday,
                    total_weeks=int(total_weeks),
                )
                st.download_button(
                    label="📅 下载 .ics 日历文件",
                    data=ics_bytes,
                    file_name="coursepilot_sjtu_schedule.ics",
                    mime="text/calendar",
                    use_container_width=True,
                )
                st.caption(f"当前导出方案：{active_cand.strategy_name}")
            else:
                st.info("请先在「智能排课」中生成课表方案。")

        with c_exp2:
            md_report = export_markdown_report(audit, active_cand)
            st.download_button(
                label="📝 下载 Markdown 报告",
                data=md_report.encode("utf-8"),
                file_name="coursepilot_sjtu_report.md",
                mime="text/markdown",
                use_container_width=True,
            )

        with st.expander("预览 Markdown 报告内容"):
            st.markdown(md_report)


if __name__ == "__main__":
    main()

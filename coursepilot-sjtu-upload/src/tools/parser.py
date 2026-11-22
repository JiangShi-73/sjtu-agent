"""文档与数据解析模块：解析成绩单、培养方案与开课表，统一输出 Pydantic 模型。"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from src.core.privacy import (
    PrivacyAuditLog,
    mask_sensitive_text,
    sanitize_transcript_dataframe,
)
from src.core.schemas import CourseOffering, CourseRecord, CurriculumPlan, TimeSlot


_DAY_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
}


def parse_time_slots_str(raw: str) -> list[TimeSlot]:
    """解析常见时间段格式，例如：
    - '周一 3-4节;周三 3-4节(单)'
    - '1:3-4:all;3:3-4:odd'
    """
    slots: list[TimeSlot] = []
    if not raw or pd.isna(raw):
        return slots

    parts = [p.strip() for p in re.split(r"[;；|]", str(raw)) if p.strip()]
    for part in parts:
        # 格式 1: 1:3-4:all
        m_compact = re.match(
            r"^([1-7])\s*:\s*(\d{1,2})\s*-\s*(\d{1,2})(?:\s*:\s*(all|odd|even))?$",
            part,
            flags=re.IGNORECASE,
        )
        if m_compact:
            day = int(m_compact.group(1))
            start_p = int(m_compact.group(2))
            end_p = int(m_compact.group(3))
            wt = (m_compact.group(4) or "all").lower()
            slots.append(
                TimeSlot(day=day, start_period=start_p, end_period=end_p, week_type=wt)
            )
            continue

        # 格式 2: 周一 3-4节(单) / 星期三 第6-8节
        m_cn = re.search(
            r"(?:周|星期)\s*([一二三四五六日天1-7])\s*第?\s*(\d{1,2})\s*[-~至]\s*(\d{1,2})\s*节?",
            part,
        )
        if m_cn:
            day = _DAY_MAP[m_cn.group(1)]
            start_p = int(m_cn.group(2))
            end_p = int(m_cn.group(3))
            wt = "all"
            if "单" in part:
                wt = "odd"
            elif "双" in part:
                wt = "even"
            slots.append(
                TimeSlot(day=day, start_period=start_p, end_period=end_p, week_type=wt)
            )
    return slots


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {c.strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def parse_transcript_csv_or_excel(
    source: Path | str | BinaryIO, filename: str = "transcript.csv"
) -> tuple[list[CourseRecord], PrivacyAuditLog]:
    """解析 CSV/Excel 成绩单并强制执行本地隐私脱敏。"""
    if isinstance(source, (str, Path)):
        path = Path(source)
        filename = path.name
        if path.suffix.lower() in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path)
    else:
        if filename.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(source)
        else:
            df = pd.read_csv(source)

    clean_df, audit = sanitize_transcript_dataframe(df)

    code_col = _pick_col(clean_df, ["course_code", "课程代码", "课号", "代码"])
    name_col = _pick_col(clean_df, ["course_name", "课程名称", "课程名", "名称"])
    credit_col = _pick_col(clean_df, ["credits", "学分"])
    module_col = _pick_col(clean_df, ["module", "模块", "课程类别", "课程性质"])
    passed_col = _pick_col(clean_df, ["passed", "是否通过", "通过"])
    term_col = _pick_col(clean_df, ["term", "学期"])

    if not code_col or not name_col or not credit_col:
        raise ValueError("成绩单缺少必要列（需要包含：课程代码、课程名称、学分）")

    records: list[CourseRecord] = []
    for _, row in clean_df.iterrows():
        code = str(row[code_col]).strip().upper()
        name = str(row[name_col]).strip()
        if not code or code == "NAN":
            continue
        credits = float(row[credit_col])
        module = str(row[module_col]).strip() if module_col and pd.notna(row[module_col]) else "未分类"
        passed_val = row[passed_col] if passed_col else True
        if isinstance(passed_val, str):
            passed = passed_val.strip().lower() not in {"false", "0", "否", "未通过", "不通过", "fail"}
        else:
            passed = bool(passed_val)
        term = str(row[term_col]).strip() if term_col and pd.notna(row[term_col]) else ""
        records.append(
            CourseRecord(
                course_code=code,
                course_name=name,
                credits=credits,
                module=module,
                passed=passed,
                term=term,
            )
        )
    return records, audit


def parse_transcript_pasted_text(raw_text: str) -> tuple[list[CourseRecord], PrivacyAuditLog]:
    """解析成绩页文本或交大双栏成绩单 PDF 提取文本（支持换行课程名与多学期重复修读如形势与政策）。"""
    masked_text, audit = mask_sensitive_text(raw_text)
    records: list[CourseRecord] = []

    # 先将换行课程名尝试合并：如 "MARX1219 习近平...\n思想概论 3 85 1 主修"
    merged_lines: list[str] = []
    for raw_line in masked_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if merged_lines and not re.match(r"^[A-Z]{1,6}\d{3,4}[A-Z]?\b", line) and re.match(
            r"^[A-Z]{1,6}\d{3,4}[A-Z]?\s+[\u4e00-\u9fa5A-Za-z]+$", merged_lines[-1]
        ):
            merged_lines[-1] = merged_lines[-1] + line
        else:
            merged_lines.append(line)

    # 支持同一行出现左右两栏课程（如交大双栏成绩单 PDF）
    pattern = re.compile(
        r"\b([A-Z]{1,6}\d{3,4}[A-Z]?)\s+([\u4e00-\u9fa5A-Za-z0-9（）()\-—·:：+]+?)\s+(\d+(?:\.\d{1,2})?)(?=\s|$)"
    )
    seen_key: set[tuple[str, str]] = set()
    for idx, line in enumerate(merged_lines):
        for m in pattern.finditer(line):
            code = m.group(1).upper()
            name = m.group(2).strip()
            credits = float(m.group(3))
            # 过滤明显不是学分的异常数字（单门课学分一般在 0.5 ~ 16 之间）
            if credits <= 0 or credits > 20:
                continue
            tail = line[m.end() : m.end() + 25]
            passed = not any(w in tail for w in ("不通过", "未通过", "不及格", "FAIL", "△", " W"))
            # 允许形势与政策等分学期修读课程按行保留，常规重复行去重
            term_m = re.search(r"\b([123])\s+主修", tail)
            term_tag = term_m.group(1) if term_m else str(idx)
            dedup_key = (code, term_tag) if code in {"MARX1205", "PSY1202"} else (code, "once")
            if dedup_key in seen_key:
                continue
            seen_key.add(dedup_key)
            records.append(
                CourseRecord(
                    course_code=code,
                    course_name=name,
                    credits=credits,
                    module="未分类",
                    passed=passed,
                    term=term_tag if term_m else "",
                )
            )
    return records, audit


def _extract_docx_text(file_bytes: bytes) -> str:
    """提取 Word (.docx) 中的正文段落与全部表格内容（适配交大表格型培养方案）。"""
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("解析 .docx 文件需要安装 python-docx") from exc

    doc = Document(io.BytesIO(file_bytes))
    chunks: list[str] = []
    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            chunks.append(t)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                chunks.append(" | ".join(cells))
    return "\n".join(chunks)


def parse_transcript_docx(file_bytes: bytes) -> tuple[list[CourseRecord], PrivacyAuditLog]:
    """解析 Word (.docx) 格式成绩单（先提取文本与表格，再本地脱敏）。"""
    raw_text = _extract_docx_text(file_bytes)
    return parse_transcript_pasted_text(raw_text)


def parse_transcript_pdf(file_bytes: bytes) -> tuple[list[CourseRecord], PrivacyAuditLog]:
    """解析 PDF 格式成绩单（先提取文本与表格，再本地脱敏）。"""
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("解析 PDF 需要安装 pdfplumber") from exc

    full_text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            full_text_parts.append(txt)

    raw_text = "\n".join(full_text_parts)
    return parse_transcript_pasted_text(raw_text)


def load_curriculum_json(source: Path | str | BinaryIO) -> CurriculumPlan:
    """加载标准化 JSON 培养方案。"""
    if isinstance(source, (str, Path)):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        raw = source.read()
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(text)
    return CurriculumPlan.model_validate(data)


def _parse_curriculum_text_with_llm(raw_text: str, llm_client) -> CurriculumPlan:
    """调用交大官方大模型将培养方案纯文本/表格抽取为标准 CurriculumPlan。"""
    text_slice = raw_text.strip()[:14000]
    if not text_slice:
        raise ValueError("未能从培养方案文件中提取到有效文字。")

    system_prompt = """你是上海交通大学教务培养方案结构化解析引擎。
请从用户提供的培养方案文本（含表格行）中抽取关键信息，严格输出如下格式的合法 JSON（不要多余文字）：
{
  "plan_id": "custom-sjtu-plan",
  "school_name": "学院名称",
  "major_name": "专业名称",
  "grade_year": "年级",
  "total_required_credits": 145.0,
  "modules": [
    {"module_name": "公共基础", "required_credits": 40.0, "mandatory_courses": ["MATH1201"], "description": ""}
  ],
  "prerequisites": {"CS3308": ["CS2301"]},
  "course_module_map": {"MATH1201": "公共基础"},
  "equivalents": {}
}
要求：尽量准确提取各模块所需学分、课程代码与模块归属、先修关系；缺失字段用空列表或空字典，不要编造不存在的课程代码。"""

    data = llm_client.chat_json(system_prompt, text_slice, temperature=0.1)
    if not isinstance(data, dict):
        raise ValueError("交大官方大模型未能将该培养方案解析为标准结构，请检查文件内容或重试。")
    return CurriculumPlan.model_validate(data)


def parse_curriculum_docx_with_llm(
    file_bytes: bytes, llm_client
) -> CurriculumPlan:
    """提取 Word (.docx) 培养方案文本与表格，并调用交大官方大模型抽取标准结构。"""
    raw_text = _extract_docx_text(file_bytes)
    return _parse_curriculum_text_with_llm(raw_text, llm_client)


def parse_curriculum_pdf_with_llm(
    file_bytes: bytes, llm_client
) -> CurriculumPlan:
    """使用 pdfplumber 提取培养方案 PDF 文本，并调用交大官方大模型抽取标准化培养方案结构。"""
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("解析培养方案 PDF 需要安装 pdfplumber") from exc

    pages_text: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages[:15]:
            txt = page.extract_text() or ""
            if txt.strip():
                pages_text.append(txt)

    return _parse_curriculum_text_with_llm("\n".join(pages_text), llm_client)


def parse_offerings_pdf_with_llm(
    file_bytes: bytes, llm_client
) -> list[CourseOffering]:
    """提取开课表/课表 PDF 的文本与表格，并调用交大官方大模型抽取为标准 CourseOffering 列表。"""
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("解析开课表 PDF 需要安装 pdfplumber") from exc

    chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages[:15]:
            tables = page.extract_tables() or []
            if tables:
                for tbl in tables:
                    for row in tbl:
                        cells = [str(c or "").strip().replace("\n", " ") for c in row]
                        if any(cells):
                            chunks.append(" | ".join(cells))
            else:
                txt = page.extract_text() or ""
                if txt.strip():
                    chunks.append(txt)

    raw_text = "\n".join(chunks).strip()[:14000]
    if not raw_text:
        raise ValueError("未能从该课表 PDF 中提取到有效文字。")

    system_prompt = """你是上海交通大学开课表/课表结构化解析引擎。
请从用户提供的课表 PDF 文本（含表格行）中提取所有课程教学班，严格输出如下格式的合法 JSON 对象（不要多余文字）：
{
  "offerings": [
    {
      "section_id": "CS2304-01",
      "course_code": "CS2304",
      "course_name": "计算机组成",
      "credits": 4.0,
      "module": "专业必修",
      "teacher": "张老师",
      "building": "东中院1-201",
      "zone": "闵行东区",
      "time_slots": "周一 3-4节;周三 3-4节",
      "prerequisites": []
    }
  ]
}
要求：
- time_slots 必须格式化为“周一 3-4节;周三 3-4节(单)”这种标准中文节次格式；
- zone 按教学楼推断为“闵行东区 / 闵行西区 / 电院群楼 / 徐汇校区”之一；
- module 若原表未写明，填“专业选修”或根据课程名合理归类；
- 严禁编造原表中不存在的课程。"""

    data = llm_client.chat_json(system_prompt, raw_text, temperature=0.1)
    if not isinstance(data, dict) or not isinstance(data.get("offerings"), list):
        raise ValueError("交大官方大模型未能将该课表 PDF 解析为标准开课表结构，请检查 PDF 内容或重试。")

    offerings: list[CourseOffering] = []
    for idx, item in enumerate(data["offerings"]):
        try:
            slots = parse_time_slots_str(str(item.get("time_slots", "")))
            if not slots:
                continue
            code = str(item.get("course_code", f"COURSE{idx+1}")).strip().upper()
            sid = str(item.get("section_id", f"{code}-{idx+1:02d}")).strip()
            prereqs_raw = item.get("prerequisites", [])
            prereqs = (
                [str(p).strip().upper() for p in prereqs_raw if str(p).strip()]
                if isinstance(prereqs_raw, list)
                else []
            )
            offerings.append(
                CourseOffering(
                    section_id=sid,
                    course_code=code,
                    course_name=str(item.get("course_name", code)).strip(),
                    credits=float(item.get("credits", 2.0)),
                    module=str(item.get("module", "专业选修")).strip(),
                    teacher=str(item.get("teacher", "未注明")).strip(),
                    building=str(item.get("building", "未注明")).strip(),
                    zone=str(item.get("zone", "闵行东区")).strip(),
                    time_slots=slots,
                    prerequisites=prereqs,
                )
            )
        except Exception:
            continue

    if not offerings:
        raise ValueError("未能从该课表 PDF 中提取出包含有效上课时间的课程。")
    return offerings


def load_offerings_csv(
    source: Path | str | BinaryIO, filename: str = "offerings.csv"
) -> list[CourseOffering]:
    """加载本学期开课表 CSV/Excel。"""
    if isinstance(source, (str, Path)):
        path = Path(source)
        df = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)
    else:
        df = pd.read_excel(source) if filename.lower().endswith((".xlsx", ".xls")) else pd.read_csv(source)

    offerings: list[CourseOffering] = []
    for _, row in df.iterrows():
        prereq_raw = str(row.get("prerequisites", "")).strip()
        prereqs = (
            [p.strip().upper() for p in re.split(r"[,，;；|]", prereq_raw) if p.strip()]
            if prereq_raw and prereq_raw.lower() != "nan"
            else []
        )
        slots = parse_time_slots_str(str(row.get("time_slots", "")))
        if not slots:
            continue
        offerings.append(
            CourseOffering(
                section_id=str(row["section_id"]).strip(),
                course_code=str(row["course_code"]).strip().upper(),
                course_name=str(row["course_name"]).strip(),
                credits=float(row["credits"]),
                module=str(row["module"]).strip(),
                teacher=str(row["teacher"]).strip(),
                building=str(row["building"]).strip(),
                zone=str(row.get("zone", "闵行东区")).strip(),
                time_slots=slots,
                prerequisites=prereqs,
                capacity_note=str(row.get("capacity_note", "常规选课")).strip(),
            )
        )
    return offerings

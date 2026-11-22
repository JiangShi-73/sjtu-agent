"""本地隐私脱敏模块：在文本或成绩单送往大模型/日志前自动遮蔽敏感信息。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
import pandas as pd


SENSITIVE_COLUMNS = {
    "姓名",
    "学生姓名",
    "name",
    "student_name",
    "学号",
    "student_id",
    "id",
    "身份证号",
    "id_card",
    "手机号",
    "电话",
    "phone",
    "mobile",
    "邮箱",
    "email",
    "成绩",
    "分数",
    "score",
    "grade",
    "gpa",
    "绩点",
}


@dataclass
class PrivacyAuditLog:
    """记录本次脱敏操作隐藏了哪些字段/实体。"""

    dropped_columns: list[str] = field(default_factory=list)
    masked_counts: dict[str, int] = field(default_factory=dict)
    raw_preview: str = ""
    masked_preview: str = ""

    def total_masked(self) -> int:
        return len(self.dropped_columns) + sum(self.masked_counts.values())


# 常见隐私正则模式（交大学号通常为 12 位数字，如 523031910xxx）
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("身份证号", re.compile(r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"), "[ID_CARD]"),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[PHONE]"),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    ("学号", re.compile(r"(?<!\d)5\d{11}(?!\d)"), "[STUDENT_ID]"),
    ("姓名标签", re.compile(r"(姓名\s*[:：]\s*)([\u4e00-\u9fa5]{2,4})"), r"\1[NAME]"),
    ("学号标签", re.compile(r"(学号\s*[:：]\s*)([A-Za-z0-9]{6,16})"), r"\1[STUDENT_ID]"),
    ("GPA/绩点", re.compile(r"((?:GPA|绩点|平均学分绩)\s*[:：=]?\s*)(\d\.\d{1,3}|\d{2}(?:\.\d{1,2})?)"), r"\1[REDACTED_GPA]"),
]


def mask_sensitive_text(text: str) -> tuple[str, PrivacyAuditLog]:
    """对任意非结构化文本（如 PDF 成绩单提取文本或对话输入）执行本地脱敏。"""
    masked = text
    counts: dict[str, int] = {}

    for label, pattern, replacement in _PATTERNS:
        masked, n = pattern.subn(replacement, masked)
        if n > 0:
            counts[label] = counts.get(label, 0) + n

    audit = PrivacyAuditLog(
        masked_counts=counts,
        raw_preview=text[:300],
        masked_preview=masked[:300],
    )
    return masked, audit


def sanitize_transcript_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, PrivacyAuditLog]:
    """剥离成绩单 DataFrame 中的身份与具体分数列，仅保留通过状态与学分核查所需列。"""
    clean_df = df.copy()
    dropped: list[str] = []
    counts: dict[str, int] = {}

    raw_preview = df.head(3).to_string(index=False)

    # 先基于分数/成绩推断 passed（如果尚未提供 passed 列）
    if "passed" not in [c.lower() for c in clean_df.columns] and "是否通过" not in clean_df.columns:
        score_col = None
        for col in clean_df.columns:
            if col.strip().lower() in {"成绩", "分数", "score", "grade"}:
                score_col = col
                break
        if score_col is not None:
            def _is_passed(val: Any) -> bool:
                s = str(val).strip().upper()
                if s in {"P", "PASS", "通过", "合格", "A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D"}:
                    return True
                if s in {"F", "FAIL", "不通过", "不合格", "W", "退课"}:
                    return False
                try:
                    return float(s) >= 60.0
                except ValueError:
                    return True

            clean_df["passed"] = clean_df[score_col].apply(_is_passed)

    # 剥离所有敏感列
    cols_to_drop = [
        col for col in clean_df.columns if col.strip().lower() in SENSITIVE_COLUMNS
    ]
    if cols_to_drop:
        clean_df = clean_df.drop(columns=cols_to_drop)
        dropped.extend(cols_to_drop)

    # 对残留字符串列做一次正则兜底脱敏
    for col in clean_df.select_dtypes(include=["object", "string"]).columns:
        new_vals = []
        for val in clean_df[col].fillna(""):
            m_val, sub_log = mask_sensitive_text(str(val))
            for k, v in sub_log.masked_counts.items():
                counts[k] = counts.get(k, 0) + v
            new_vals.append(m_val)
        clean_df[col] = new_vals

    masked_preview = clean_df.head(3).to_string(index=False)
    audit = PrivacyAuditLog(
        dropped_columns=dropped,
        masked_counts=counts,
        raw_preview=raw_preview,
        masked_preview=masked_preview,
    )
    return clean_df, audit

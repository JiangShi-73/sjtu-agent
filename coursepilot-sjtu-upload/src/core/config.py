"""全局配置管理：从 .env 读取交大官方大模型 API 配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class AppConfig:
    llm_base_url: str
    llm_api_key: str
    llm_model: str


def get_config() -> AppConfig:
    return AppConfig(
        llm_base_url=os.getenv(
            "LLM_BASE_URL", "https://models.sjtu.edu.cn/api/v1"
        ).strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat").strip(),
    )

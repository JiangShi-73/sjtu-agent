"""上海交通大学官方大模型 API 客户端（https://models.sjtu.edu.cn/api/v1）。"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from src.core.config import AppConfig, get_config


class LLMClient:
    """封装交大官方 OpenAI 兼容大模型调用。"""

    def __init__(
        self,
        config: AppConfig | None = None,
        model_override: str | None = None,
    ):
        self.config = config or get_config()
        self.model_name = (model_override or self.config.llm_model).strip()
        self.is_configured = bool(
            self.config.llm_api_key
            and self.config.llm_api_key != "your_api_key_here"
        )
        self._client: OpenAI | None = None

        if self.is_configured:
            self._client = OpenAI(
                api_key=self.config.llm_api_key,
                base_url=self.config.llm_base_url,
                timeout=30.0,
            )

    def chat_json(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.1
    ) -> dict[str, Any] | None:
        """请求交大官方大模型返回 JSON 结构化对象。"""
        if self._client is None:
            return None

        try:
            resp = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(
                    lines[1:-1] if lines[-1].startswith("```") else lines[1:]
                )
            return json.loads(content)
        except Exception:
            return None

    def chat_text(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.3
    ) -> str | None:
        """请求交大官方大模型返回自然语言文本。"""
        if self._client is None:
            return None

        try:
            resp = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            return None

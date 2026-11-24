"""模型供应商目录。

用户不该再去翻文档抄接口地址。这里放一张表：选哪个供应商，
前端就自动带出 base_url 和常用模型，用户只填 API Key。

约定：
- base_url 一律是 OpenAI 兼容的 /v1 根地址，AstrBot 那边用
  `openai_chat_completion` 直连；
- needs_key=False 表示这个供应商可以不带 Key（本地 Ollama、自定义转发）；
- docs 是申请 Key / 看文档的地址，前端做成「去官网」链接，没有就留空。

只放常量和小工具函数，不 import 本项目的其它模块，方便单测。
"""

from __future__ import annotations

# id 是稳定标识：前端下拉、配置回显、日志里都用它，别随便改。
# 顺序 = 前端下拉里的顺序，内置的 opencode 排第一（也是历史默认）。
PROVIDERS: list[dict] = [
    {
        "id": "opencode",
        "name": "OpenCode（内置，默认）",
        "base_url": "https://opencode.ai/inference/go/v1",
        "models": [
            "deepseek-v4.1-flash",
            "deepseek-v4-pro",
            "glm-5.3",
            "glm-5.3-flash",
            "grok-4.7",
            "gpt-5.6-luna",
            "gpt-6-luna",
            "claude-haiku-5-5",
        ],
        "needs_key": True,
        "docs": "https://opencode.ai",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o4-mini"],
        "needs_key": True,
        "docs": "https://platform.openai.com/api-keys",
    },
    {
        "id": "anthropic",
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
        "needs_key": True,
        "docs": "https://console.anthropic.com/settings/keys",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek（深度求索）",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "needs_key": True,
        "docs": "https://platform.deepseek.com/api_keys",
    },
    {
        "id": "moonshot",
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
            "kimi-k2",
        ],
        "needs_key": True,
        "docs": "https://platform.moonshot.cn/console/api-keys",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-air"],
        "needs_key": True,
        "docs": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    {
        "id": "dashscope",
        "name": "阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long"],
        "needs_key": True,
        "docs": "https://bailian.console.aliyun.com/",
    },
    {
        "id": "volcengine",
        "name": "火山方舟（豆包）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-pro", "doubao-pro-32k", "doubao-1.5-pro-32k"],
        "needs_key": True,
        "docs": "https://console.volcengine.com/ark",
    },
    {
        "id": "siliconflow",
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct",
        ],
        "needs_key": True,
        "docs": "https://cloud.siliconflow.cn/account/ak",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter（聚合）",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            "openai/gpt-4o",
            "anthropic/claude-sonnet-4.5",
            "deepseek/deepseek-chat",
            "google/gemini-2.5-flash",
            "meta-llama/llama-3.3-70b-instruct",
        ],
        "needs_key": True,
        "docs": "https://openrouter.ai/keys",
    },
    {
        "id": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "needs_key": True,
        "docs": "https://console.groq.com/keys",
    },
    {
        "id": "ollama",
        "name": "Ollama（本地）",
        "base_url": "http://127.0.0.1:11434/v1",
        "models": ["qwen2.5", "llama3", "llama3.2", "qwen2.5-coder", "deepseek-r1"],
        "needs_key": False,
        "docs": "https://ollama.com/download",
    },
    {
        "id": "custom",
        "name": "自定义（手填）",
        "base_url": "",
        "models": [],
        "needs_key": False,
        "docs": "",
    },
]

PROVIDER_MAP: dict[str, dict] = {p["id"]: p for p in PROVIDERS}

# 兼容别名：老配置里可能写的是这些名字
ALIASES: dict[str, str] = {
    "aliyun": "dashscope",
    "qwen": "dashscope",
    "kimi": "moonshot",
    "glm": "zhipu",
    "doubao": "volcengine",
    "ark": "volcengine",
    "silicon": "siliconflow",
    "openai-compatible": "custom",
}


def list_providers() -> list[dict]:
    """给接口用的完整目录（深拷贝，防止调用方改到常量表）。"""
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "base_url": p["base_url"],
            "models": list(p["models"]),
            "needs_key": p["needs_key"],
            "docs": p["docs"],
        }
        for p in PROVIDERS
    ]


def get_provider(provider_id: str | None) -> dict | None:
    """按 id 找供应商，认别名。"""
    key = (provider_id or "").strip().lower()
    if key in PROVIDER_MAP:
        return PROVIDER_MAP[key]
    key = ALIASES.get(key, "")
    return PROVIDER_MAP.get(key)


def normalize_base(base_url: str | None) -> str:
    """比对地址时忽略首尾空白和结尾斜杠。"""
    return (base_url or "").strip().rstrip("/").lower()


def match_base(base_url: str | None) -> dict | None:
    """根据用户已配的接口地址反查是哪个供应商。

    老配置（比如 opencode 的地址）也能认出来，用来做「当前配置」回显。
    """
    target = normalize_base(base_url)
    if not target:
        return None
    for provider in PROVIDERS:
        if provider["base_url"] and normalize_base(provider["base_url"]) == target:
            return provider
    return None


def default_model(provider: dict | None) -> str:
    """供应商的默认模型（列表第一个）。"""
    models = (provider or {}).get("models") or []
    return models[0] if models else ""


def needs_key(base_url: str | None) -> bool:
    """这个地址是否必须带 Key。

    目录外的地址（自定义）按「不必填」处理——是不是要 Key 由对方服务决定，
    信鸽不拦；目录里的按 needs_key 走。
    """
    provider = match_base(base_url)
    return bool(provider["needs_key"]) if provider else False


def mask_key(key: str | None) -> str:
    """Key 的脱敏形式，只用来在界面上回显「配没配」。"""
    value = (key or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return value[:3] + "…" + value[-4:]

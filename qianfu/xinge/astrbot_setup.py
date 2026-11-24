"""把信鸽的配置同步成 AstrBot 的配置。

用户只需要在信鸽里填 QQ 机器人的 AppID / Secret，
AstrBot 那边的 provider、platform、插件全部由这里生成。

AstrBot 的配置文件结构（v4.30 起）：

    provider_sources  —— 连哪儿（base_url / key / 超时）
    provider          —— 用哪个模型（model + provider_source_id）
    platform          —— 接哪个 IM（qq_official 的 appid / secret）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 已经自己装过 AstrBot 的，设这个环境变量指过去就行，不用再下一份
ASTRBOT_DIR = Path(os.environ.get("XINGE_ASTRBOT_DIR") or (ROOT / "astrbot")).resolve()
PLUGIN_SRC = ROOT / "integrations" / "astrbot_plugin_xinge"
PLUGIN_DST = ASTRBOT_DIR / "data" / "plugins" / "xinge"
CONFIG_PATH = ASTRBOT_DIR / "data" / "cmd_config.json"

ASTRBOT_REPO = "https://github.com/AstrBotDevs/AstrBot.git"
SOURCE_ID = "opencode-go_source"
PROVIDER_ID = "opencode-go"


def venv_python() -> Path:
    if os.name == "nt":
        return ASTRBOT_DIR / ".venv" / "Scripts" / "python.exe"
    return ASTRBOT_DIR / ".venv" / "bin" / "python"


def portable() -> bool:
    """便携版：依赖装在 packages/ 里，用嵌入式 Python，没有 venv。

    除了 XINGE_PORTABLE=1，也看目录结构：早期版本生成的 启动.bat 漏了这行
    （用户手上的 zip 就是），但 packages/ 和内置 python/ 都在，一样是便携版，
    不能再去建 venv（嵌入式 Python 没有 venv 模块，建了必炸）。
    """
    if not (ROOT / "packages").exists():
        return False
    if os.environ.get("XINGE_PORTABLE") == "1":
        return True
    return (os.name == "nt" and (ROOT / "python" / "python.exe").exists()) or (
        os.name != "nt" and (ROOT / "python" / "bin" / "python3").exists()
    )


def python_for_astrbot() -> Path:
    """跑 AstrBot 用哪个解释器。

    便携版直接用当前解释器（嵌入式 Python + PYTHONPATH），
    开发版用 astrbot/.venv 里那个。
    """
    if portable():
        return Path(sys.executable)
    return venv_python()


def installed() -> bool:
    if not (ASTRBOT_DIR / "main.py").exists():
        return False
    # 便携版 / 安装版：依赖内置在 packages/ 里，不缺解释器环境
    if (ROOT / "packages").exists():
        return True
    # 开发版：看 venv 建没建
    return venv_python().exists()


def ensure_installed(log=print) -> None:
    """没有就 clone + 建 venv + 装依赖。第一次比较久。

    便携版依赖已经在 packages/ 里，不需要装任何东西。
    """
    if portable():
        log("便携版：AstrBot 和依赖都已内置")
        return

    if (ASTRBOT_DIR / "main.py").exists():
        pass
    else:
        log("没找到 AstrBot，开始下载（约 40 MB）…")
        ASTRBOT_DIR.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "clone", "--depth", "1", ASTRBOT_REPO, str(ASTRBOT_DIR)])
        if result.returncode != 0:
            raise RuntimeError("下载 AstrBot 失败，检查网络")

    py = venv_python()
    if not py.exists():
        log("创建 AstrBot 虚拟环境…")
        subprocess.run([sys.executable, "-m", "venv", str(ASTRBOT_DIR / ".venv")], check=True)

    marker = ASTRBOT_DIR / ".venv" / ".deps-ok"
    if not marker.exists():
        log("安装 AstrBot 依赖（第一次约 10 分钟）…")
        subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
        result = subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(ASTRBOT_DIR / "requirements.txt")]
        )
        if result.returncode != 0:
            raise RuntimeError("AstrBot 依赖安装失败")
        marker.write_text("ok", encoding="utf-8")


def sync_plugin() -> None:
    if not PLUGIN_SRC.exists():
        return
    PLUGIN_DST.mkdir(parents=True, exist_ok=True)
    for item in PLUGIN_SRC.iterdir():
        target = PLUGIN_DST / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def read_astrbot_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_astrbot_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8-sig"
    )


def sync(cfg: dict) -> dict:
    """把信鸽配置写进 AstrBot 配置，返回这次改了什么。

    Args:
        cfg: 信鸽的配置字典（~/.xinge/config.json 的内容）。

    Returns:
        一份摘要，说明模型和 QQ 平台各自有没有配上。
    """
    data = read_astrbot_config()
    if not data:
        return {"ok": False, "reason": "AstrBot 还没初始化过，先启动一次"}

    changed: list[str] = []

    # ---- 模型 ----
    base = (cfg.get("llm_base") or "https://opencode.ai/inference/go/v1").strip()
    key = (cfg.get("llm_key") or "").strip()
    model = (cfg.get("llm_model") or "deepseek-v4.1-flash").strip()

    if key:
        sources = [s for s in (data.get("provider_sources") or []) if s.get("id") != SOURCE_ID]
        sources.insert(
            0,
            {
                "id": SOURCE_ID,
                "provider": "openai",
                "type": "openai_chat_completion",
                "provider_type": "chat_completion",
                # AstrBot v4.30 要求这里是列表；写成字符串会在
                # text_chat 里报 'str' object has no attribute 'copy'
                "key": [key] if isinstance(key, str) else list(key),
                "api_base": base,
                "timeout": 120,
                "proxy": "",
                "custom_headers": {"x-opencode-session": "astrbot"},
            },
        )
        data["provider_sources"] = sources

        providers = [p for p in (data.get("provider") or []) if p.get("id") != PROVIDER_ID]
        providers.insert(
            0,
            {
                "id": PROVIDER_ID,
                "enable": True,
                "model": model,
                "provider_source_id": SOURCE_ID,
                "modalities": [],
                "custom_extra_body": {},
            },
        )
        data["provider"] = providers
        changed.append("model")

    # ---- QQ 平台 ----
    appid = str(cfg.get("qq_appid") or "").strip()
    secret = (cfg.get("qq_secret") or "").strip()

    if appid and secret:
        platform_id = f"default_{appid}"
        platforms = [p for p in (data.get("platform") or []) if p.get("type") != "qq_official"]
        platforms.insert(
            0,
            {
                "id": platform_id,
                "type": "qq_official",
                "enable": True,
                "appid": appid,
                "secret": secret,
                "enable_group_c2c": True,
                "enable_guild_direct_message": True,
                "use_markdown": True,
            },
        )
        data["platform"] = platforms
        changed.append("qq")

    if changed:
        write_astrbot_config(data)

    return {
        "ok": True,
        "changed": changed,
        "model": model if key else None,
        "qq_appid": appid or None,
    }


def status(cfg: dict) -> dict:
    """给前端看的配置状态。"""
    data = read_astrbot_config()
    sources = data.get("provider_sources") or []
    providers = data.get("provider") or []
    platforms = [p for p in (data.get("platform") or []) if p.get("type") == "qq_official"]

    return {
        "installed": installed(),
        "model_ready": bool(sources and providers),
        "model": (providers[0].get("model") if providers else None),
        "qq_ready": bool(platforms and platforms[0].get("appid")),
        "qq_appid": (platforms[0].get("appid") if platforms else None),
        "plugin_ready": (PLUGIN_DST / "main.py").exists(),
    }

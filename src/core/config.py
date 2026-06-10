"""统一配置管理 —— 从项目根目录的 config.json 读取所有配置项。

支持通过环境变量 HIKARI_CONFIG_PATH 指定其他配置文件路径。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("hikari.core.config")

# ============================================================================
# 定位配置文件
# ============================================================================

# 优先使用环境变量指定的路径，否则从当前文件向上 3 级（src/core/config.py → 项目根目录）
_CONFIG_PATH = os.getenv("HIKARI_CONFIG_PATH")
if _CONFIG_PATH:
    _CONFIG_FILE = Path(_CONFIG_PATH)
else:
    _CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "config.json"

logger.info(f"加载配置: {_CONFIG_FILE}")

# ============================================================================
# 加载 JSON
# ============================================================================

if not _CONFIG_FILE.exists():
    raise FileNotFoundError(
        f"配置文件不存在: {_CONFIG_FILE}\n"
        f"请在项目根目录创建 config.json，或设置 HIKARI_CONFIG_PATH 环境变量。"
    )

try:
    _raw = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
except json.JSONDecodeError as e:
    raise ValueError(f"配置文件 JSON 解析失败: {_CONFIG_FILE}\n{e}") from e


def _get(key: str, default=None):
    """从 JSON 配置中安全取值（支持点号分隔的嵌套键）。"""
    keys = key.split(".")
    value = _raw
    for k in keys:
        if isinstance(value, dict):
            value = value.get(k)
        else:
            return default
        if value is None:
            return default
    return value


# ============================================================================
# NoneBot2 配置（供 bot.py 设置环境变量使用）
# ============================================================================

# 注意：这些值不作为模块级常量提供给插件，插件不应依赖它们。
# bot.py 会在 nonebot.init() 之前读取这些值并注入 os.environ。

NONEBOT_DRIVER: str = _get("nonebot.driver", "~websockets")
NONEBOT_WS_URLS: list[str] = _get("nonebot.onebot_v11_ws_urls", [])
NONEBOT_ACCESS_TOKEN: str = _get("nonebot.access_token", "")
NONEBOT_LOG_LEVEL: str = _get("nonebot.log_level", "DEBUG")

# ============================================================================
# 超级管理员（硬编码）
# ============================================================================

SUPER_ADMIN: int = 3433559280

# ============================================================================
# DeepSeek API
# ============================================================================

DEEPSEEK_API_KEY: str = _get("deepseek.api_key", "")
DEEPSEEK_BASE_URL: str = _get("deepseek.base_url", "https://api.deepseek.com")
DEEPSEEK_MODEL: str = _get("deepseek.model", "deepseek-chat")
# 项目根目录（用于解析相对路径）
_ROOT = _CONFIG_FILE.parent

# 系统提示词文件路径（相对于项目根目录）
PROMPT_FILE: str = _get("prompt.file", "prompts/hikari.txt")

# 兜底系统提示词（prompt 文件不存在时使用）
_FALLBACK_SYSTEM_PROMPT = "你是一个可爱的QQ机器人，名叫HIKARI。请用中文回复，语气活泼可爱。"


def get_system_prompt() -> str:
    """读取系统提示词（从 prompt 文件读取，支持 UTF-8 编码的 .txt/.md 文件）。

    若文件不存在则返回兜底提示词。
    """
    prompt_path = _ROOT / PROMPT_FILE
    try:
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"无法读取系统提示词文件 {prompt_path}: {e}")
    logger.warning(f"系统提示词文件不存在: {prompt_path}，使用兜底提示词")
    return _FALLBACK_SYSTEM_PROMPT

# ============================================================================
# AI 记忆
# ============================================================================

MAX_MEMORY_MESSAGES: int = int(_get("ai_memory.max_messages", 40))
AI_MEMORY_DIR: str = _get("ai_memory.dir", "data/ai_memory")

# ============================================================================
# 管理员 / 白名单
# ============================================================================

WHITELIST_FILE: str = _get("whitelist.file", "data/admin/whitelist.json")

# ============================================================================
# Cobalt 视频解析
# ============================================================================

COBALT_API: str = _get("cobalt.api", "http://127.0.0.1:9000/")

# ============================================================================
# 版本信息（version.json）
# ============================================================================

# 从项目根目录的 version.json 读取版本号和构建号
_VERSION_PATH = _ROOT / "version.json"


def get_version() -> str:
    """返回版本字符串，如 'v0.1.0 (build 42)'。"""
    try:
        if _VERSION_PATH.exists():
            v = json.loads(_VERSION_PATH.read_text(encoding="utf-8"))
            ver = v.get("version", "0.0.0")
            build = v.get("build", 0)
            return f"v{ver} (build {build})"
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning(f"无法读取版本信息: {e}")
    return "v0.0.0 (unknown)"


def bump_build() -> int:
    """递增 version.json 中的 build 号，返回新 build 号。"""
    try:
        if _VERSION_PATH.exists():
            v = json.loads(_VERSION_PATH.read_text(encoding="utf-8"))
        else:
            v = {"version": "0.1.0", "build": 0}
        v["build"] = v.get("build", 0) + 1
        _VERSION_PATH.write_text(
            json.dumps(v, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )
        logger.info(f"版本号已更新: v{v['version']} (build {v['build']})")
        return v["build"]
    except (OSError, ValueError) as e:
        logger.error(f"无法更新版本号: {e}")
        raise

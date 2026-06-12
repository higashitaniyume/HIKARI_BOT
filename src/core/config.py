"""统一配置管理 —— 从项目根目录的 config.json 读取所有配置项。

支持通过环境变量 HIKARI_CONFIG_PATH 指定其他配置文件路径。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

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
# 技能（Skill）系统 —— 可切换的人物提示词
# ============================================================================

SKILLS_DIR: str = _get("skills.dir", "skills")

# 技能定义缓存
_skill_cache: dict[str, dict] = {}
_skill_cache_ts: float = 0.0
_skill_cache_ttl: float = 30.0

# 用户技能状态（内存缓存）
_user_skill_state: dict[str, str] = {}
_USER_STATE_PATH: Optional[Path] = None


def _get_user_state_path() -> Path:
    """获取用户技能状态文件路径。"""
    global _USER_STATE_PATH
    if _USER_STATE_PATH is None:
        skill_dir = SKILLS_DIR
        if not Path(skill_dir).is_absolute():
            skill_dir = str(_ROOT / skill_dir)
        _USER_STATE_PATH = Path(skill_dir) / "user_state.json"
    return _USER_STATE_PATH


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_md_skill(md_path: Path) -> dict | None:
    """解析 Markdown 技能文件（Claude Code SKILL.md 格式）。

    提取 YAML frontmatter 作为元数据，正文作为提示词内容。
    不依赖 PyYAML，用手写解析器处理简单键值对。
    """
    try:
        text = md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"无法读取 Markdown 技能文件 {md_path}: {e}")
        return None

    m = _FRONTMATTER_RE.match(text)
    if not m:
        # 没有 frontmatter，整个文件作为提示词
        body = text.strip()
        if not body:
            return None
        return {
            "name": md_path.stem,
            "display_name": md_path.stem,
            "description": "",
            "prompt_file": "",
            "_prompt_content": body,
        }

    frontmatter = m.group(1)
    body = text[m.end():].strip()
    if not body:
        return None

    # 简单解析 YAML-like 键值对
    meta: dict[str, str] = {}
    current_key: str | None = None
    current_value: list[str] = []

    for line in frontmatter.split("\n"):
        if not line.strip() or line.strip().startswith("#"):
            continue
        kv_match = re.match(r"^(\w[\w-]*)\s*:\s*(.*)", line)
        if kv_match:
            if current_key:
                meta[current_key] = "\n".join(current_value).strip()
            current_key = kv_match.group(1)
            val = kv_match.group(2).strip()
            # 处理 YAML block scalar indicators (|, >, |-, >-, etc.)
            if val in ("|", "|+", "|-", ">", ">+", ">-"):
                current_value = []
            else:
                current_value = [val] if val else []
        else:
            if current_key:
                stripped = line.strip()
                current_value.append(stripped)

    if current_key:
        meta[current_key] = "\n".join(current_value).strip()

    name = meta.get("name", md_path.stem)
    display_name = meta.get("display_name", name)
    description = meta.get("description", "")
    is_default = meta.get("default", "").strip().lower() in ("true", "yes", "1")

    return {
        "name": name,
        "display_name": display_name,
        "description": description,
        "prompt_file": "",
        "_prompt_content": body,
        "default": is_default,
    }


def _load_skill_definitions() -> dict[str, dict]:
    """加载所有技能定义文件（带缓存）。

    扫描格式（Claude Code SKILL.md）：
        1. <skill_dir>/<name>/SKILL.md  —— 子目录中的 SKILL.md（推荐）
        2. <skill_dir>/<name>.md         —— 平铺的 .md 文件

    自动发现目录：
        - skills/ （主目录，由 skills.dir 配置）
        - .claude/skills/ （nuwa-skill 输出目录）
    """
    global _skill_cache, _skill_cache_ts
    now = time.monotonic()
    if _skill_cache and now - _skill_cache_ts < _skill_cache_ttl:
        return _skill_cache

    skill_dir = SKILLS_DIR
    if not Path(skill_dir).is_absolute():
        skill_dir = str(_ROOT / skill_dir)
    skill_path = Path(skill_dir)

    skills: dict[str, dict] = {}
    if skill_path.exists():
        # ── 格式 1: <dir>/SKILL.md（推荐）────────────────
        for d in sorted(skill_path.iterdir()):
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            data = _parse_md_skill(skill_md)
            if data:
                data["name"] = d.name
                skills[d.name] = data

        # ── 格式 2: *.md（平铺）──────────────────────────
        for f in sorted(skill_path.glob("*.md")):
            data = _parse_md_skill(f)
            if data and data["name"] not in skills:
                skills[data["name"]] = data

    # ── 自动发现: .claude/skills/（nuwa-skill 输出）─────
    for extra_dir in (".claude/skills",):
        _scan_extra_skills_dir(_ROOT / extra_dir, skills)

    _skill_cache = skills
    _skill_cache_ts = now
    logger.debug(f"已加载 {len(skills)} 个技能定义")
    return skills


def _scan_extra_skills_dir(root: Path, skills: dict[str, dict]) -> None:
    """扫描额外目录中的 skill 文件，不覆盖已有同名 skill。"""
    if not root.exists() or not root.is_dir():
        return

    # 平铺 .md 文件
    for f in sorted(root.glob("*.md")):
        data = _parse_md_skill(f)
        if data and data["name"] not in skills:
            skills[data["name"]] = data

    # 子目录中的 SKILL.md
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            continue
        data = _parse_md_skill(skill_md)
        if data and d.name not in skills:
            data["name"] = d.name
            skills[d.name] = data


def list_skills() -> list[dict]:
    """返回所有可用技能的列表（按 display_name 排序）。"""
    skills = _load_skill_definitions()
    return sorted(skills.values(), key=lambda s: (not s.get("default"), s.get("display_name", "")))


def get_default_skill() -> str | None:
    """返回默认技能名称，无默认则返回 None。"""
    skills = _load_skill_definitions()
    for name, data in skills.items():
        if data.get("default"):
            return name
    # 第一个作为兜底
    if skills:
        return next(iter(skills.keys()))
    return None


def get_skill_prompt(skill_name: str | None) -> str:
    """获取指定技能的系统提示词。

    Args:
        skill_name: 技能名称，None 时使用默认技能，无默认则回退到 prompts/hikari.txt

    Returns:
        系统提示词文本
    """
    # 未指定 → 使用默认技能
    if not skill_name:
        default = get_default_skill()
        if default:
            skill_name = default
        else:
            return get_system_prompt()

    skills = _load_skill_definitions()
    skill = skills.get(skill_name)
    if not skill:
        logger.warning(f"技能 '{skill_name}' 不存在，使用默认提示词")
        return get_system_prompt()

    # SKILL.md 正文即提示词
    inline = skill.get("_prompt_content", "")
    if inline:
        return inline

    logger.warning(f"技能 '{skill_name}' 无提示词内容，使用默认提示词")
    return get_system_prompt()


def get_skill_model(skill_name: str | None) -> str | None:
    """获取技能指定的模型覆盖，None 表示使用默认模型。"""
    if not skill_name:
        return None
    skills = _load_skill_definitions()
    skill = skills.get(skill_name)
    return skill.get("model") if skill else None


def get_skill_temperature(skill_name: str | None) -> float | None:
    """获取技能指定的温度覆盖，None 表示使用默认温度。"""
    if not skill_name:
        return None
    skills = _load_skill_definitions()
    skill = skills.get(skill_name)
    t = skill.get("temperature") if skill else None
    return float(t) if t is not None else None


# ── 用户技能状态管理 ──────────────────────────────────


def _load_user_state() -> dict[str, str]:
    """从磁盘加载用户技能状态。"""
    path = _get_user_state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # 确保所有值都是字符串
            return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"用户技能状态 JSON 损坏: {path} — {e}")
    return {}


def _save_user_state(state: dict[str, str]) -> None:
    """将用户技能状态写入磁盘。"""
    path = _get_user_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_user_skill(user_id: int) -> str | None:
    """获取用户的活跃技能名称。

    Returns:
        技能名称，未设置时返回 None（表示使用默认）
    """
    global _user_skill_state
    uid = str(user_id)
    if uid in _user_skill_state:
        return _user_skill_state[uid]
    # 从磁盘加载
    state = _load_user_state()
    _user_skill_state = state
    return state.get(uid)


def set_user_skill(user_id: int, skill_name: str | None) -> None:
    """设置用户的活跃技能。

    Args:
        user_id: QQ 号
        skill_name: 技能名称，None 表示清除（使用默认）
    """
    global _user_skill_state
    uid = str(user_id)

    # 重新从磁盘加载以确保最新
    state = _load_user_state()

    if skill_name:
        # 验证技能存在
        skills = _load_skill_definitions()
        if skill_name not in skills:
            raise ValueError(f"技能 '{skill_name}' 不存在")
        state[uid] = skill_name
    else:
        state.pop(uid, None)

    _save_user_state(state)
    _user_skill_state = state
    logger.info(f"用户 {user_id} 的技能已{'设置为 ' + skill_name if skill_name else '清除（使用默认）'}")


# ============================================================================
# AI 记忆
# ============================================================================

MAX_MEMORY_MESSAGES: int = int(_get("ai_memory.max_messages", 40))
AI_MEMORY_DIR: str = _get("ai_memory.dir", "data/memory")

# ============================================================================
# 管理员 / 白名单
# ============================================================================

WHITELIST_FILE: str = _get("whitelist.file", "data/admin/whitelist.json")

# ============================================================================
# Cobalt 视频解析
# ============================================================================

COBALT_API: str = _get("cobalt.api", "http://127.0.0.1:9000/")

# ============================================================================
# SearXNG 网页搜索
# ============================================================================

SEARXNG_API: str = _get("searxng.api", "http://127.0.0.1:54259/")

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

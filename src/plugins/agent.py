"""AI Agent 插件 —— 统一消息入口，通过 function calling 让 AI 自己路由。

替代了原来多个 matcher 各自匹配规则的架构。
所有白名单内的消息先发给 AI，AI 判断意图后调用对应函数：

    聊天 → 直接文本回复
    媒体 URL → parse_media_url()
    白名单管理 → manage_whitelist()
    记忆管理 → manage_memory()
    帮助 → show_help()
    文件/媒体发送 → send_file() / send_media()
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.rule import Rule
from openai import AsyncOpenAI

from src.core.config import (
    AI_MEMORY_DIR,
    COBALT_API,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    MAX_MEMORY_MESSAGES,
    SUPER_ADMIN,
    WHITELIST_FILE,
    get_system_prompt,
)
from src.plugins.admin import get_whitelist
from src.plugins.file_sender import get_file_sender

logger = logging.getLogger("hikari.plugins.agent")

# ============================================================================
# 常量
# ============================================================================

_MAX_CONCURRENT_API = 3
_MIN_CHAT_INTERVAL = 5.0
_MEMORY_CACHE_TTL = 60.0
_MAX_API_RETRIES = 3
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_QQ_UPLOAD_SIZE = 8 * 1024 * 1024    # 8 MB
MAX_QQ_VIDEO_SIZE = 15 * 1024 * 1024    # 15 MB

# 支持解析的媒体域名
SUPPORTED_DOMAINS: dict[str, str] = {
    "x.com": "X/Twitter",
    "twitter.com": "X/Twitter",
}

# ============================================================================
# OpenAI 工具定义（function calling）
# ============================================================================

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "向用户发送一条聊天回复。"
                "当用户只是想聊天、提问、吐槽、闲聊时使用此函数。"
                "这是最常用的函数。"
                "回复风格请遵循系统提示词中定义的角色设定。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要发送给用户的回复内容",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_media_url",
            "description": (
                "解析媒体 URL 并下载/发送媒体文件。"
                "当用户发送了 X/Twitter 链接时自动调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要解析的媒体 URL",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_whitelist",
            "description": (
                "管理机器人的白名单。"
                "支持的操作：add_user(添加用户), add_group(添加群), "
                "remove_user(移除用户), remove_group(移除群), list(查看白名单), status(查看当前状态)。"
                "只有超级管理员才能修改白名单。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "add_user", "add_group",
                            "remove_user", "remove_group",
                            "list", "status",
                        ],
                        "description": "要执行的白名单操作",
                    },
                    "target_id": {
                        "type": "integer",
                        "description": "目标 QQ 号（add_user/remove_user）或群号（add_group/remove_group）",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_memory",
            "description": (
                "管理当前用户的 AI 对话记忆。"
                "支持的操作：clear(清除记忆), view(查看记忆条数)。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["clear", "view"],
                        "description": "记忆操作类型",
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_help",
            "description": "显示机器人的帮助信息和可用功能列表。当用户说'帮助'、'help'、'命令'、'菜单'、'功能'时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_media_or_file",
            "description": (
                "发送图片、视频、语音或文件给指定目标。"
                "当用户要求发送某个文件或媒体时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path_or_url": {
                        "type": "string",
                        "description": "本地文件路径或 URL",
                    },
                    "target": {
                        "type": "string",
                        "description": "目标：QQ号（私聊）或 'group:群号'（群聊）",
                    },
                },
                "required": ["path_or_url", "target"],
            },
        },
    },
]

# ============================================================================
# Token 估算（同 ai_chat.py）
# ============================================================================

_RE_CHINESE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
_RE_ENGLISH_WORD = re.compile(r"[a-zA-Z]+")
_RE_ENGLISH_CHAR = re.compile(r"[a-zA-Z]")


def _estimate_tokens(text: str) -> int:
    chinese = len(_RE_CHINESE.findall(text))
    english_words = len(_RE_ENGLISH_WORD.findall(text))
    english_chars = len(_RE_ENGLISH_CHAR.findall(text))
    other = len(text) - chinese - english_chars
    return max(0, int(chinese * 1.5 + english_words * 1.3 + other * 0.25))


def _estimate_total_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        total += _estimate_tokens(msg.get("content", "")) + 4
    return total


# ============================================================================
# DeepSeek 客户端 & 并发控制
# ============================================================================

_client: Optional[AsyncOpenAI] = None
_api_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_API)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            logger.warning("DEEPSEEK_API_KEY 未设置！Agent 将无法工作。")
        _client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY or "sk-placeholder",
            base_url=DEEPSEEK_BASE_URL,
            timeout=60.0,
        )
    return _client


# ============================================================================
# 频率限制
# ============================================================================

_cooldowns: dict[str, float] = {}


def _cooldown_key(user_id: int, group_id: Optional[int]) -> str:
    return f"{group_id or 'private'}:{user_id}"


def _check_cooldown(user_id: int, group_id: Optional[int]) -> tuple[bool, float]:
    key = _cooldown_key(user_id, group_id)
    now = time.monotonic()
    if key in _cooldowns and now < _cooldowns[key]:
        return False, _cooldowns[key] - now
    return True, 0.0


def _set_cooldown(user_id: int, group_id: Optional[int]) -> None:
    key = _cooldown_key(user_id, group_id)
    _cooldowns[key] = time.monotonic() + _MIN_CHAT_INTERVAL
    if len(_cooldowns) > 1000:
        now = time.monotonic()
        expired = [k for k, v in _cooldowns.items() if v <= now]
        for k in expired:
            del _cooldowns[k]


def _should_retry(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is not None:
        if status == 429:
            return True
        if 500 <= status < 600:
            return True
        if status < 500:
            return False
    exc_name = type(exc).__name__
    if "Connection" in exc_name or "Timeout" in exc_name:
        return True
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    return False


# ============================================================================
# 记忆管理（复用 ai_chat.py 的 MemoryManager 结构）
# ============================================================================


class MemoryManager:
    """Per-user AI 对话记忆管理器。"""

    def __init__(self, base_dir: str = AI_MEMORY_DIR):
        self._base = Path(base_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    def _cache_key(self, user_id: int, group_id: Optional[int]) -> str:
        return f"{group_id or 'private'}:{user_id}"

    def _get_lock(self, user_id: int, group_id: Optional[int]) -> asyncio.Lock:
        key = self._cache_key(user_id, group_id)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _file_path(self, user_id: int, group_id: Optional[int]) -> Path:
        if group_id is not None:
            return self._base / "group" / str(group_id) / f"{user_id}.json"
        return self._base / "private" / f"{user_id}.json"

    def _load(self, file_path: Path, cache_key: str) -> list[dict]:
        if cache_key in self._cache:
            ts, mem = self._cache[cache_key]
            if time.monotonic() - ts < _MEMORY_CACHE_TTL:
                return mem
        if not file_path.exists():
            return []
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._cache[cache_key] = (time.monotonic(), data)
                return data
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"记忆 JSON 损坏: {file_path} — {e}")
        return []

    def _trim_by_tokens(self, memory: list[dict]) -> list[dict]:
        max_tokens = max(2000, MAX_MEMORY_MESSAGES * 50)
        system_tokens = _estimate_tokens(get_system_prompt()) + 4
        budget = max(200, max_tokens - system_tokens)
        while memory:
            if _estimate_total_tokens(memory) <= budget:
                break
            memory = memory[2:]
        return memory

    def _save(self, file_path: Path, memory: list[dict], cache_key: str) -> None:
        memory = self._trim_by_tokens(memory)
        self._cache[cache_key] = (time.monotonic(), memory)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(memory, ensure_ascii=False),
            encoding="utf-8",
        )

    async def get_memory(
        self, user_id: int, group_id: Optional[int] = None
    ) -> list[dict]:
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            return self._load(self._file_path(user_id, group_id), cache_key)

    async def append(
        self, user_id: int, user_msg: str, assistant_msg: str,
        group_id: Optional[int] = None,
    ) -> None:
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            path = self._file_path(user_id, group_id)
            memory = self._load(path, cache_key)
            memory.append({"role": "user", "content": user_msg})
            memory.append({"role": "assistant", "content": assistant_msg})
            self._save(path, memory, cache_key)

    async def clear(self, user_id: int, group_id: Optional[int] = None) -> None:
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            self._cache.pop(cache_key, None)
            path = self._file_path(user_id, group_id)
            if path.exists():
                path.unlink()
                logger.info(f"记忆已清除: {path}")

    async def count(self, user_id: int, group_id: Optional[int] = None) -> int:
        mem = await self.get_memory(user_id, group_id)
        return len(mem)


_memory_manager: Optional[MemoryManager] = None


def _get_memory() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


# ============================================================================
# AI 调用（function calling 循环）
# ============================================================================


async def _call_ai(
    messages: list[dict],
    tools: list[dict] | None = None,
) -> dict:
    """调用 DeepSeek API（支持 function calling）。

    Returns:
        API 响应的 choice 字典（含 message 和可选的 tool_calls）
    """
    if not DEEPSEEK_API_KEY:
        return {"message": {"content": "❌ AI 未配置，请联系管理员。", "tool_calls": None}}

    client = _get_client()
    last_error: Optional[Exception] = None

    for attempt in range(_MAX_API_RETRIES):
        try:
            async with _api_semaphore:
                start = time.monotonic()
                kwargs: dict[str, Any] = {
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "max_tokens": 1024,
                    "temperature": 0.8,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                response = await client.chat.completions.create(**kwargs)
                elapsed = time.monotonic() - start
                choice = response.choices[0]
                info_parts = [
                    f"模型={DEEPSEEK_MODEL}",
                    f"耗时={elapsed:.1f}s",
                    f"tokens={response.usage.total_tokens if response.usage else '?'}",
                ]
                if attempt > 0:
                    info_parts.append(f"重试#{attempt}")
                if choice.message.tool_calls:
                    names = [tc.function.name for tc in choice.message.tool_calls]
                    info_parts.append(f"工具调用={names}")
                logger.info(f"AI 响应 ({', '.join(info_parts)})")
                return {
                    "message": {
                        "role": choice.message.role,
                        "content": choice.message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in (choice.message.tool_calls or [])
                        ] if choice.message.tool_calls else None,
                    },
                }

        except Exception as e:
            last_error = e
            if attempt < _MAX_API_RETRIES - 1 and _should_retry(e):
                delay = 2 ** attempt
                logger.warning(
                    f"AI API 调用失败 (attempt {attempt + 1}/{_MAX_API_RETRIES}), "
                    f"{delay}s 后重试: {type(e).__name__}: {e}"
                )
                await asyncio.sleep(delay)
            else:
                break

    logger.error(
        f"AI API 调用最终失败: {type(last_error).__name__}: {last_error}"
    )
    return {"message": {"content": "❌ AI 暂时不可用，请稍后再试~", "tool_calls": None}}


# ============================================================================
# 工具实现
# ============================================================================


async def _tool_send_message(
    bot: Bot, event: Event, text: str,
    user_id: int, group_id: Optional[int],
) -> str:
    """发送聊天回复。"""
    if isinstance(event, GroupMessageEvent):
        await bot.send_group_msg(
            group_id=event.group_id,
            message=MessageSegment.at(user_id) + MessageSegment.text("\n" + text),
        )
    else:
        await bot.send_private_msg(user_id=user_id, message=text)
    return "消息已发送"


async def _tool_parse_media_url(
    bot: Bot, event: Event, url: str,
) -> str:
    """解析媒体 URL 并发送。"""
    if not COBALT_API:
        return "❌ 视频解析服务未配置"

    is_private = isinstance(event, PrivateMessageEvent)
    target = (
        event.user_id if is_private
        else f"group:{event.group_id}"  # type: ignore[attr-defined]
    )

    # 调用 Cobalt API
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            resp = await client.post(
                COBALT_API,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"url": url},
            )
            result = resp.json()
    except Exception as e:
        logger.error(f"Cobalt API 调用失败: {e}")
        return f"❌ 解析失败: {e}"

    status = result.get("status")
    if status == "error":
        err = result.get("error", {})
        return f"❌ 解析失败: [{err.get('code', 'unknown')}]"

    if status in ("redirect", "tunnel"):
        file_url = result.get("url", "")
        filename = result.get("filename", "media")
        if not file_url:
            return "❌ 未获取到下载链接"

        # HEAD 探测大小
        content_length = 0
        try:
            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                head = await client.head(file_url)
                content_length = int(head.headers.get("Content-Length", 0))
        except Exception:
            pass

        if content_length > MAX_DOWNLOAD_SIZE:
            size_mb = content_length / (1024 * 1024)
            return f"📥 {filename}\n文件过大 ({size_mb:.1f} MB)\n下载链接: {file_url}"

        # 超过 QQ 限制 → 发文件
        ext = Path(filename).suffix.lower()
        is_video = ext in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
        qq_limit = MAX_QQ_VIDEO_SIZE if is_video else MAX_QQ_UPLOAD_SIZE

        if content_length > qq_limit or (content_length == 0 and is_video):
            f_sender = get_file_sender()
            ok = await f_sender.send_file(bot, target=target,
                                          file_path_or_url=file_url,
                                          filename=filename)
            if ok:
                return ""  # 已发送，不需要额外文字
            return f"📥 {filename}\n下载链接: {file_url}"

        # 下载并发
        try:
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                dl = await client.get(file_url)
                dl.raise_for_status()
                data = dl.read()
        except Exception as e:
            return f"❌ 下载失败: {e}"

        # 发送媒体
        from src.plugins.media_sender import get_media_sender
        sender = get_media_sender()
        try:
            await sender.send_bytes(bot, target=target, data=data, filename=filename)
            return ""  # 已发送
        except Exception as e:
            # 降级发链接
            return f"📥 {filename}\n下载链接: {file_url}"

    if status == "picker":
        items = result.get("picker", [])
        sent = 0
        for i, item in enumerate(items):
            item_url = item.get("url", "")
            item_type = item.get("type", "photo")
            if not item_url:
                continue
            ext = ".jpg" if item_type == "photo" else ".mp4"
            fname = f"media_{i+1}_{item_type}{ext}"
            try:
                if item_type == "video":
                    f_sender = get_file_sender()
                    ok = await f_sender.send_file(bot, target=target,
                                                  file_path_or_url=item_url,
                                                  filename=fname)
                    if ok:
                        sent += 1
                    continue
                async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                    dl = await client.get(item_url)
                    dl.raise_for_status()
                    data = dl.read()
                from src.plugins.media_sender import get_media_sender
                await get_media_sender().send_bytes(bot, target=target, data=data, filename=fname)
                sent += 1
            except Exception as e:
                logger.error(f"picker #{i+1} 失败: {e}")

        if sent == 0:
            return "❌ 所有媒体下载失败"
        return "" if sent == len(items) else f"📥 {sent}/{len(items)} 个媒体已发送"

    return f"⚠️ 未知返回状态: {status}"


async def _tool_manage_whitelist(
    event: Event, action: str, target_id: int | None,
) -> str:
    """管理白名单。"""
    wl = get_whitelist()
    user_id = event.user_id

    if action in ("add_user", "add_group", "remove_user", "remove_group"):
        if user_id != SUPER_ADMIN:
            return "❌ 只有超级管理员才能修改白名单"

    if action == "add_user":
        if target_id is None:
            return "❌ 请指定要添加的用户 QQ 号"
        await wl.add_user(target_id)
        return f"✅ 已添加用户 {target_id} 到白名单"

    elif action == "add_group":
        if target_id is None:
            return "❌ 请指定要添加的群号"
        await wl.add_group(target_id)
        return f"✅ 已添加群 {target_id} 到白名单"

    elif action == "remove_user":
        if target_id is None:
            return "❌ 请指定要移除的用户 QQ 号"
        if target_id == SUPER_ADMIN:
            return "❌ 不能移除超级管理员"
        await wl.remove_user(target_id)
        return f"✅ 已从白名单移除用户 {target_id}"

    elif action == "remove_group":
        if target_id is None:
            return "❌ 请指定要移除的群号"
        await wl.remove_group(target_id)
        return f"✅ 已从白名单移除群 {target_id}"

    elif action == "list":
        data = await wl.get_list()
        users = ", ".join(str(u) for u in data["users"])
        groups = ", ".join(str(g) for g in data["groups"])
        return (
            f"=== 白名单 ===\n"
            f"用户 ({len(data['users'])}): {users or '无'}\n"
            f"群   ({len(data['groups'])}): {groups or '无'}"
        )

    elif action == "status":
        if isinstance(event, GroupMessageEvent):
            g_ok = wl.is_group_allowed(event.group_id)
            u_ok = wl.is_user_allowed(user_id)
            return (
                f"当前群 {event.group_id}: {'✅ 已授权' if g_ok else '❌ 未授权'}\n"
                f"当前用户 {user_id}: {'✅ 已授权' if u_ok else '❌ 未授权'}"
            )
        else:
            u_ok = wl.is_user_allowed(user_id)
            return f"当前用户 {user_id}: {'✅ 已授权' if u_ok else '❌ 未授权'}"

    return f"❌ 未知操作: {action}"


async def _tool_manage_memory(
    user_id: int, group_id: Optional[int], action: str,
) -> str:
    """管理 AI 记忆。"""
    mem = _get_memory()
    scope = f"群{group_id}" if group_id else "私聊"

    if action == "clear":
        await mem.clear(user_id, group_id)
        return f"✅ 已清除你在 {scope} 的 AI 记忆"

    elif action == "view":
        count = await mem.count(user_id, group_id)
        return f"📝 你在 {scope} 的 AI 记忆: {count} 条（{count // 2} 轮对话）"

    return f"❌ 未知操作: {action}"


HELP_TEXT = """╔════════════════════════════╗
║   HIKARI_BOT 功能列表     ║
╚════════════════════════════╝

💬 聊天
・群内 @我 + 消息 → AI 对话
・私聊任意消息 → AI 对话

📥 媒体解析
・发送 X/Twitter 链接 → 自动下载

👥 白名单管理（管理员）
・添加/移除用户/群
・查看白名单/状态

🧠 记忆
・说"清除记忆" → 清空对话历史
・说"查看记忆" → 查看记忆条数

📎 文件/媒体
・说"发送文件/图片 <路径> to <目标>"

💡 提示
・目标格式: QQ号=私聊, group:群号=群聊"""


async def _tool_show_help() -> str:
    return HELP_TEXT


async def _tool_send_media_or_file(
    bot: Bot, path_or_url: str, target: str,
) -> str:
    """发送媒体或文件。"""
    from src.plugins.media_sender import get_media_sender

    # 判断是本地文件还是 URL
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        f_sender = get_file_sender()
        from urllib.parse import urlparse
        url_name = Path(urlparse(path_or_url).path).name or "file"
        ok = await f_sender.send_file(bot, target=target,
                                      file_path_or_url=path_or_url,
                                      filename=url_name)
        if ok:
            return f"✅ 文件已发送: {url_name} → {target}"
        return f"❌ 文件发送失败: {url_name}"

    # 本地文件
    path = Path(path_or_url)
    if not path.exists():
        return f"❌ 文件不存在: {path_or_url}"

    sender = get_media_sender()
    try:
        await sender.send_bytes(bot, target=target,
                                data=path.read_bytes(),
                                filename=path.name)
        return f"✅ 已发送: {path.name} → {target}"
    except Exception as e:
        # 降级到文件发送
        f_sender = get_file_sender()
        ok = await f_sender.send_file(bot, target=target,
                                      file_path_or_url=str(path),
                                      filename=path.name)
        if ok:
            return f"✅ 文件已发送: {path.name} → {target}"
        return f"❌ 发送失败: {e}"


# ============================================================================
# 工具执行调度
# ============================================================================


async def _execute_tool(
    bot: Bot,
    event: Event,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: int,
    group_id: Optional[int],
) -> str:
    """执行工具函数并返回结果文本。"""
    logger.info(f"执行工具: {tool_name}({arguments})")

    if tool_name == "send_message":
        return await _tool_send_message(
            bot, event, arguments.get("text", ""), user_id, group_id
        )

    elif tool_name == "parse_media_url":
        return await _tool_parse_media_url(
            bot, event, arguments.get("url", "")
        )

    elif tool_name == "manage_whitelist":
        return await _tool_manage_whitelist(
            event, arguments.get("action", ""), arguments.get("target_id"),
        )

    elif tool_name == "manage_memory":
        return await _tool_manage_memory(
            user_id, group_id, arguments.get("action", "")
        )

    elif tool_name == "show_help":
        return await _tool_show_help()

    elif tool_name == "send_media_or_file":
        return await _tool_send_media_or_file(
            bot,
            arguments.get("path_or_url", ""),
            arguments.get("target", str(user_id)),
        )

    return f"❌ 未知工具: {tool_name}"


# ============================================================================
# Agent 主循环
# ============================================================================


async def _agent_loop(
    bot: Bot,
    event: Event,
    user_id: int,
    user_msg: str,
    group_id: Optional[int],
) -> None:
    """Agent 主循环：调用 AI → 执行工具 → 可能再调 AI → 最终回复。

    支持最多 3 轮 function calling 循环（防止无限递归）。
    """
    mem = _get_memory()
    history = await mem.get_memory(user_id, group_id)

    # 构建初始消息
    messages: list[dict] = [{"role": "system", "content": get_system_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    # Function calling 循环（最多 3 轮）
    for _round in range(3):
        response = await _call_ai(messages, tools=TOOLS)
        msg = response["message"]
        tool_calls = msg.get("tool_calls")

        if not tool_calls:
            # 纯文本回复
            reply = msg.get("content", "").strip()
            if reply:
                if isinstance(event, GroupMessageEvent):
                    await bot.send_group_msg(
                        group_id=event.group_id,
                        message=MessageSegment.at(user_id)
                        + MessageSegment.text("\n" + reply),
                    )
                else:
                    await bot.send_private_msg(user_id=user_id, message=reply)
                # 保存记忆
                await mem.append(user_id, user_msg, reply, group_id)
            return

        # AI 调用了工具
        # 将 AI 的工具调用消息加入对话
        messages.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            func_name = tc["function"]["name"]
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                func_args = {}

            result = await _execute_tool(
                bot, event, func_name, func_args, user_id, group_id,
            )

            # 将工具结果加入对话
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        # 继续循环，让 AI 根据工具结果生成最终回复

    # 超过最大轮数，强制生成一次回复
    logger.warning(f"Agent 达到最大 function calling 轮数，强制生成回复")
    final = await _call_ai(messages, tools=None)
    reply = final["message"].get("content", "").strip()
    if reply:
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.at(user_id)
                + MessageSegment.text("\n" + reply),
            )
        else:
            await bot.send_private_msg(user_id=user_id, message=reply)
        await mem.append(user_id, user_msg, reply, group_id)


# ============================================================================
# 消息入口
# ============================================================================


# 白名单 Rule
async def _whitelist_check(event: Event) -> bool:
    return get_whitelist().is_allowed(event)


WHITELIST = Rule(_whitelist_check)

# 统一消息入口（优先级 50，block=True，拦截所有白名单消息）
agent_handler = on_message(rule=WHITELIST, priority=3, block=True)


@agent_handler.handle()
async def handle_agent(bot: Bot, event: Event):
    """Agent 统一入口。"""
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return

    pure_text = event.get_plaintext().strip()
    if not pure_text:
        return

    user_id = event.user_id
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None

    location = f"群{group_id}" if group_id else f"私聊"
    logger.info(f"[Agent] {location} {user_id}: {pure_text[:100]}")

    # 频率限制
    allowed, remaining = _check_cooldown(user_id, group_id)
    if not allowed:
        msg = f"⏳ 冷却中，请 {remaining:.0f} 秒后再试~"
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.at(user_id) + MessageSegment.text("\n" + msg),
            )
        else:
            await bot.send_private_msg(user_id=user_id, message=msg)
        return

    _set_cooldown(user_id, group_id)

    try:
        await _agent_loop(bot, event, user_id, pure_text, group_id)
    except Exception as e:
        logger.exception(f"Agent 处理异常: {e}")
        error_msg = "❌ 出了点问题，请稍后再试~"
        try:
            if isinstance(event, GroupMessageEvent):
                await bot.send_group_msg(
                    group_id=event.group_id,
                    message=MessageSegment.at(user_id)
                    + MessageSegment.text("\n" + error_msg),
                )
            else:
                await bot.send_private_msg(user_id=user_id, message=error_msg)
        except Exception:
            pass

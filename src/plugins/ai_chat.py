"""AI 聊天模块 —— 接入 DeepSeek API，支持群内每人独立记忆。

触发方式：
    /chat <消息>    — 显式命令（私聊 + 群聊）
    群内 @机器人     — 触发 AI 回复
    /clearmemory     — 清除当前用户记忆（私聊）
    /memory          — 查看记忆条数（私聊）

记忆结构：
    data/ai_memory/
    ├── private/{user_id}.json
    └── group/{group_id}/{user_id}.json

每条记忆是 OpenAI 格式的 {"role": "user/assistant", "content": "..."}
系统提示词由 config 注入，不存储在记忆文件中。

优化要点：
    - Per-user 异步锁（不同用户不互相阻塞）
    - 内存缓存（60s TTL，减少磁盘 I/O）
    - 基于 token 数的智能裁剪（而非固定条数）
    - 频率限制（同一用户/上下文 5s 冷却）
    - API 并发上限（最多 3 个同时请求）
    - 自动重试（429/5xx/超时，指数退避）
    - 错误信息脱敏（用户侧不暴露内部细节）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.params import CommandArg
from nonebot.rule import to_me
from openai import AsyncOpenAI

from src.core.config import (
    AI_MEMORY_DIR,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    MAX_MEMORY_MESSAGES,
    get_skill_prompt,
    get_system_prompt,
    get_user_skill,
)
from src.plugins.admin import WHITELIST
from src.plugins.video_parser import has_media_url

logger = logging.getLogger("hikari.plugins.ai_chat")

# ============================================================================
# 常量
# ============================================================================

# 并发控制：最多同时进行的 API 调用数
_MAX_CONCURRENT_API = 3

# 频率限制：同一用户同一上下文的最小间隔（秒）
_MIN_CHAT_INTERVAL = 5.0

# 记忆缓存 TTL（秒）
_MEMORY_CACHE_TTL = 60.0

# 最大重试次数（含首次）
_MAX_API_RETRIES = 3

# ============================================================================
# Token 估算
# ============================================================================

# 用于粗略估算 token 数的正则
_RE_CHINESE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
_RE_ENGLISH_WORD = re.compile(r"[a-zA-Z]+")
_RE_ENGLISH_CHAR = re.compile(r"[a-zA-Z]")


def _estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。

    DeepSeek 使用 BPE tokenizer，与 OpenAI cl100k_base 接近。
    中文 ≈ 1.5 token/字，英文 ≈ 1.3 token/词，其余 ≈ 0.25 token/符。
    误差通常在 ±20% 以内，足够用于记忆裁剪。
    """
    chinese = len(_RE_CHINESE.findall(text))
    english_words = len(_RE_ENGLISH_WORD.findall(text))
    english_chars = len(_RE_ENGLISH_CHAR.findall(text))
    other = len(text) - chinese - english_chars
    return max(0, int(chinese * 1.5 + english_words * 1.3 + other * 0.25))


def _estimate_total_tokens(messages: list[dict]) -> int:
    """估算消息列表的总 token 数（含角色标记开销 ≈ 4 token/条）。"""
    total = 0
    for msg in messages:
        total += _estimate_tokens(msg.get("content", "")) + 4
    return total


# ============================================================================
# DeepSeek 客户端
# ============================================================================

_client: Optional[AsyncOpenAI] = None
_api_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_API)


def get_client() -> AsyncOpenAI:
    """获取全局唯一的 AsyncOpenAI 客户端。"""
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            logger.warning("DEEPSEEK_API_KEY 未设置！AI 聊天将无法工作。")
        _client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY or "sk-placeholder",
            base_url=DEEPSEEK_BASE_URL,
            timeout=60.0,
        )
    return _client


# ============================================================================
# 频率限制
# ============================================================================

_cooldowns: dict[str, float] = {}  # context_key -> next_allowed_timestamp


def _cooldown_key(user_id: int, group_id: Optional[int]) -> str:
    """生成冷却键。"""
    return f"{group_id or 'private'}:{user_id}"


def _check_cooldown(user_id: int, group_id: Optional[int]) -> tuple[bool, float]:
    """检查用户是否在冷却期内。

    Returns:
        (是否允许, 剩余冷却秒数)
    """
    key = _cooldown_key(user_id, group_id)
    now = time.monotonic()
    if key in _cooldowns and now < _cooldowns[key]:
        return False, _cooldowns[key] - now
    return True, 0.0


def _set_cooldown(user_id: int, group_id: Optional[int]) -> None:
    """更新用户冷却时间。"""
    key = _cooldown_key(user_id, group_id)
    _cooldowns[key] = time.monotonic() + _MIN_CHAT_INTERVAL

    # 惰性清理：超过阈值时清理过期条目
    if len(_cooldowns) > 1000:
        now = time.monotonic()
        expired = [k for k, v in _cooldowns.items() if v <= now]
        for k in expired:
            del _cooldowns[k]


def _should_retry(exc: Exception) -> bool:
    """判断 API 异常是否应该重试。

    重试条件：HTTP 429、5xx、连接/超时错误。
    不重试：4xx（除 429 外）客户端错误。
    """
    status = getattr(exc, "status_code", None)
    if status is not None:
        if status == 429:  # Rate Limit
            return True
        if 500 <= status < 600:  # Server Error
            return True
        if status < 500:  # Client Error（含 4xx 除 429）
            return False

    # 无 status_code 的连接/超时类错误
    exc_name = type(exc).__name__
    if "Connection" in exc_name or "Timeout" in exc_name:
        return True
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True

    return False


# ============================================================================
# 记忆管理
# ============================================================================


class MemoryManager:
    """用户记忆管理器 —— 每个用户/群组维护独立的对话历史。

    特性：
        - Per-user 异步锁（不同用户不互相阻塞）
        - 内存缓存（减少重复磁盘 I/O）
        - 基于 token 数的智能裁剪
    """

    def __init__(self, base_dir: str = AI_MEMORY_DIR):
        self._base = Path(base_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    # ── 内部辅助 ────────────────────────────────────────

    def _cache_key(self, user_id: int, group_id: Optional[int]) -> str:
        return f"{group_id or 'private'}:{user_id}"

    def _get_lock(self, user_id: int, group_id: Optional[int]) -> asyncio.Lock:
        key = self._cache_key(user_id, group_id)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    # ── 路径 ────────────────────────────────────────────

    def _private_path(self, user_id: int) -> Path:
        return self._base / "private" / f"{user_id}.json"

    def _group_path(self, group_id: int, user_id: int) -> Path:
        return self._base / "group" / str(group_id) / f"{user_id}.json"

    # ── 读取 ────────────────────────────────────────────

    def _load(self, file_path: Path, cache_key: str) -> list[dict]:
        """从磁盘加载记忆（优先使用缓存）。"""
        # 查内存缓存
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
            logger.warning(f"记忆 JSON 损坏，重置: {file_path} — {e}")

        return []

    # ── 裁剪 ────────────────────────────────────────────

    def _trim_by_tokens(self, memory: list[dict]) -> list[dict]:
        """基于 token 数裁剪记忆，丢弃最旧的对话轮次。

        目标上限 = MAX_MEMORY_MESSAGES * 50 tokens（默认 20*50=1000），
        减去 system prompt 的估算开销。超过上限时从最早的
        (user, assistant) 成对丢弃。
        """
        max_tokens = max(2000, MAX_MEMORY_MESSAGES * 50)
        system_tokens = _estimate_tokens(get_system_prompt()) + 4
        budget = max(200, max_tokens - system_tokens)

        while memory:
            if _estimate_total_tokens(memory) <= budget:
                break
            # 成对丢弃最旧的一轮对话
            memory = memory[2:]

        return memory

    # ── 写入 ────────────────────────────────────────────

    def _save(self, file_path: Path, memory: list[dict], cache_key: str) -> None:
        """写入记忆到磁盘（token 裁剪 + 更新缓存）。"""
        memory = self._trim_by_tokens(memory)

        # 更新缓存
        self._cache[cache_key] = (time.monotonic(), memory)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(memory, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── 公开 API ─────────────────────────────────────────

    async def get_memory(
        self,
        user_id: int,
        group_id: Optional[int] = None,
    ) -> list[dict]:
        """获取某个用户的对话历史。

        Args:
            user_id: QQ 号
            group_id: 群号（None 表示私聊）
        """
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            if group_id is not None:
                return self._load(self._group_path(group_id, user_id), cache_key)
            else:
                return self._load(self._private_path(user_id), cache_key)

    async def append(
        self,
        user_id: int,
        user_msg: str,
        assistant_msg: str,
        group_id: Optional[int] = None,
    ) -> None:
        """追加一轮对话到记忆。

        Args:
            user_id: QQ 号
            user_msg: 用户消息
            assistant_msg: AI 回复
            group_id: 群号（None 表示私聊）
        """
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            if group_id is not None:
                path = self._group_path(group_id, user_id)
            else:
                path = self._private_path(user_id)

            memory = self._load(path, cache_key)
            memory.append({"role": "user", "content": user_msg})
            memory.append({"role": "assistant", "content": assistant_msg})
            self._save(path, memory, cache_key)

    async def clear(
        self,
        user_id: int,
        group_id: Optional[int] = None,
    ) -> None:
        """清除某个用户的记忆。"""
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            self._cache.pop(cache_key, None)
            if group_id is not None:
                path = self._group_path(group_id, user_id)
            else:
                path = self._private_path(user_id)
            if path.exists():
                path.unlink()
                logger.info(f"记忆已清除: {path}")

    async def count(
        self,
        user_id: int,
        group_id: Optional[int] = None,
    ) -> int:
        """返回记忆条数。"""
        mem = await self.get_memory(user_id, group_id)
        return len(mem)


# ============================================================================
# 全局单例
# ============================================================================

_memory_manager: Optional[MemoryManager] = None


def get_memory() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


# ============================================================================
# AI 对话逻辑
# ============================================================================


async def _call_ai(messages: list[dict]) -> str:
    """调用 DeepSeek API 进行对话（含并发控制 + 自动重试）。

    Args:
        messages: OpenAI 格式的消息列表（含 system prompt）

    Returns:
        AI 回复文本（错误信息已脱敏）
    """
    if not DEEPSEEK_API_KEY:
        return "❌ AI 未配置，请联系管理员。"

    client = get_client()
    last_error: Optional[Exception] = None

    for attempt in range(_MAX_API_RETRIES):
        try:
            async with _api_semaphore:
                start = time.monotonic()
                response = await client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=messages,
                    max_tokens=1024,
                    temperature=0.8,
                )
                elapsed = time.monotonic() - start
                reply = response.choices[0].message.content or "（AI 返回了空内容）"
                info_parts = [
                    f"模型={DEEPSEEK_MODEL}",
                    f"耗时={elapsed:.1f}s",
                    f"tokens={response.usage.total_tokens if response.usage else '?'}",
                ]
                if attempt > 0:
                    info_parts.append(f"重试#{attempt}")
                logger.info(f"AI 响应 ({', '.join(info_parts)})")
                return reply.strip()

        except Exception as e:
            last_error = e
            if attempt < _MAX_API_RETRIES - 1 and _should_retry(e):
                delay = 2**attempt  # 1s → 2s
                logger.warning(
                    f"AI API 调用失败 (attempt {attempt + 1}/{_MAX_API_RETRIES}), "
                    f"{delay}s 后重试: {type(e).__name__}: {e}"
                )
                await asyncio.sleep(delay)
            else:
                break

    # 所有重试均失败
    logger.error(
        f"AI API 调用最终失败 (attempts={_MAX_API_RETRIES}): "
        f"{type(last_error).__name__}: {last_error}"
    )
    return "❌ AI 暂时不可用，请稍后再试~"


async def _do_chat(
    user_id: int,
    message: str,
    group_id: Optional[int] = None,
) -> str:
    """执行一次完整的 AI 对话（构建上下文 → 调用 API → 更新记忆）。

    注意：调用方应自行处理频率限制检查。

    Args:
        user_id: 用户 QQ
        message: 用户消息
        group_id: 群号（私聊为 None）

    Returns:
        AI 回复
    """
    mem = get_memory()

    # 构建上下文
    history = await mem.get_memory(user_id, group_id)
    skill_name = get_user_skill(user_id)
    messages: list[dict] = [{"role": "system", "content": get_skill_prompt(skill_name)}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    # 调用 AI
    reply = await _call_ai(messages)

    # 更新记忆
    await mem.append(user_id, message, reply, group_id)

    return reply


# ============================================================================
# 命令: /chat
# ============================================================================

chat_cmd = on_command("chat", rule=WHITELIST, priority=10)


@chat_cmd.handle()
async def handle_chat(bot: Bot, event: Event, args: Message = CommandArg()):
    """处理 /chat 命令。"""
    text = str(args).strip()
    if not text:
        await chat_cmd.finish("用法: /chat <消息>\n发送 /clearmemory 清除记忆")

    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None

    # 频率限制
    allowed, remaining = _check_cooldown(event.user_id, group_id)
    if not allowed:
        await chat_cmd.finish(f"⏳ 冷却中，请 {remaining:.0f} 秒后再试~")

    _set_cooldown(event.user_id, group_id)

    reply = await _do_chat(event.user_id, text, group_id)
    await chat_cmd.finish(reply)


# ============================================================================
# 群内 @机器人 / 私聊任意消息 触发
# ============================================================================

from nonebot import on_message
from nonebot.rule import Rule


def _not_command(event: Event) -> bool:
    """排除命令消息（以 / 开头）。"""
    return not event.get_plaintext().strip().startswith("/")


def _not_media_url(event: Event) -> bool:
    """排除包含媒体 URL 的消息（交给 video_parser 处理）。"""
    return not has_media_url(event.get_plaintext().strip())


NOT_COMMAND = Rule(_not_command)
NOT_MEDIA_URL = Rule(_not_media_url)

# ── 群内 @机器人 ─────────────────────────────────────────

group_at_handler = on_message(
    rule=to_me() & WHITELIST & NOT_COMMAND & NOT_MEDIA_URL,
    priority=90,
    block=False,
)


@group_at_handler.handle()
async def handle_group_at(bot: Bot, event: Event):
    """处理群内 @机器人 的消息。"""
    if not isinstance(event, GroupMessageEvent):
        return

    text = str(event.message).strip()
    if not text:
        return

    pure_text = event.get_plaintext().strip()
    if not pure_text:
        return

    group_id = event.group_id
    user_id = event.user_id

    logger.info(f"[AI @触发] 群{group_id} 用户{user_id}: {pure_text[:50]}")

    # 频率限制
    allowed, remaining = _check_cooldown(user_id, group_id)
    if not allowed:
        at_seg = MessageSegment.at(user_id)
        await bot.send_group_msg(
            group_id=group_id,
            message=at_seg + MessageSegment.text(f"\n⏳ 冷却中，请 {remaining:.0f} 秒后再试~"),
        )
        return

    _set_cooldown(user_id, group_id)

    try:
        reply = await _do_chat(user_id, pure_text, group_id)
        at_seg = MessageSegment.at(user_id)
        await bot.send_group_msg(
            group_id=group_id,
            message=at_seg + MessageSegment.text("\n" + reply),
        )
    except Exception as e:
        logger.error(f"@AI 回复失败: {e}")
        await bot.send_group_msg(
            group_id=group_id,
            message=MessageSegment.at(user_id)
            + MessageSegment.text("\n❌ AI 暂时不可用，请稍后再试~"),
        )


# ── 私聊任意消息（带鉴权）─────────────────────────────────

private_handler = on_message(
    rule=WHITELIST & NOT_COMMAND & NOT_MEDIA_URL,
    priority=95,
    block=False,
)


@private_handler.handle()
async def handle_private_chat(bot: Bot, event: Event):
    """私聊中任意消息触发 AI 回复（命令优先匹配，此 handler 兜底）。"""
    if not isinstance(event, PrivateMessageEvent):
        return

    pure_text = event.get_plaintext().strip()
    if not pure_text:
        return

    user_id = event.user_id
    logger.info(f"[AI 私聊] {user_id}: {pure_text[:50]}")

    # 频率限制
    allowed, remaining = _check_cooldown(user_id, None)
    if not allowed:
        await bot.send_private_msg(
            user_id=user_id,
            message=f"⏳ 冷却中，请 {remaining:.0f} 秒后再试~",
        )
        return

    _set_cooldown(user_id, None)

    try:
        reply = await _do_chat(user_id, pure_text, group_id=None)
        await bot.send_private_msg(user_id=user_id, message=reply)
    except Exception as e:
        logger.error(f"私聊 AI 回复失败: {e}")
        await bot.send_private_msg(
            user_id=user_id,
            message="❌ AI 暂时不可用，请稍后再试~",
        )


# ============================================================================
# 命令: /clearmemory
# ============================================================================

clear_cmd = on_command("clearmemory", rule=WHITELIST, priority=10)


@clear_cmd.handle()
async def handle_clearmemory(event: Event):
    """清除当前用户的 AI 记忆。"""
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    scope = f"群{group_id}" if group_id else "私聊"

    mem = get_memory()
    await mem.clear(event.user_id, group_id)
    logger.info(f"用户 {event.user_id} 清除了 {scope} 的 AI 记忆")
    await clear_cmd.finish(f"✅ 已清除你在 {scope} 的 AI 记忆")


# ============================================================================
# 命令: /memory
# ============================================================================

mem_cmd = on_command("memory", rule=WHITELIST, priority=10)


@mem_cmd.handle()
async def handle_memory(event: Event):
    """查看当前用户的 AI 记忆使用情况。"""
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    scope = f"群{group_id}" if group_id else "私聊"

    mem = get_memory()
    count = await mem.count(event.user_id, group_id)
    await mem_cmd.finish(f"📝 你在 {scope} 的 AI 记忆: {count} 条（{count // 2} 轮对话）")

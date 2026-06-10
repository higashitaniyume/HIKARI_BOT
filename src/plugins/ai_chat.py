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
"""

from __future__ import annotations

import asyncio
import json
import logging
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
    DEEPSEEK_SYSTEM_PROMPT,
    MAX_MEMORY_MESSAGES,
)
from src.plugins.admin import WHITELIST

logger = logging.getLogger("hikari.plugins.ai_chat")

# ============================================================================
# DeepSeek 客户端
# ============================================================================

_client: Optional[AsyncOpenAI] = None


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
# 记忆管理
# ============================================================================

class MemoryManager:
    """用户记忆管理器。每个用户/群组维护独立的对话历史。"""

    def __init__(self, base_dir: str = AI_MEMORY_DIR):
        self._base = Path(base_dir)
        self._lock = asyncio.Lock()

    # ── 路径 ────────────────────────────────────────────

    def _private_path(self, user_id: int) -> Path:
        return self._base / "private" / f"{user_id}.json"

    def _group_path(self, group_id: int, user_id: int) -> Path:
        return self._base / "group" / str(group_id) / f"{user_id}.json"

    # ── 读取 ────────────────────────────────────────────

    def _load(self, file_path: Path) -> list[dict]:
        """从磁盘加载记忆。"""
        if not file_path.exists():
            return []
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"记忆 JSON 损坏，重置: {file_path} — {e}")
        return []

    # ── 写入 ────────────────────────────────────────────

    def _save(self, file_path: Path, memory: list[dict]) -> None:
        """写入记忆到磁盘，并裁剪到最大长度。"""
        # 裁剪
        max_len = MAX_MEMORY_MESSAGES
        if len(memory) > max_len:
            memory = memory[-max_len:]

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(memory, ensure_ascii=False, indent=2),
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
        async with self._lock:
            if group_id is not None:
                return self._load(self._group_path(group_id, user_id))
            else:
                return self._load(self._private_path(user_id))

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
        async with self._lock:
            if group_id is not None:
                path = self._group_path(group_id, user_id)
            else:
                path = self._private_path(user_id)

            memory = self._load(path)
            memory.append({"role": "user", "content": user_msg})
            memory.append({"role": "assistant", "content": assistant_msg})
            self._save(path, memory)

    async def clear(
        self,
        user_id: int,
        group_id: Optional[int] = None,
    ) -> None:
        """清除某个用户的记忆。"""
        async with self._lock:
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
    """调用 DeepSeek API 进行对话。

    Args:
        messages: OpenAI 格式的消息列表（含 system prompt）

    Returns:
        AI 回复文本
    """
    if not DEEPSEEK_API_KEY:
        return "❌ AI 未配置：请在 .env 中设置 DEEPSEEK_API_KEY"

    client = get_client()
    try:
        start = time.monotonic()
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            max_tokens=1024,
            temperature=0.8,
        )
        elapsed = time.monotonic() - start
        reply = response.choices[0].message.content or "（AI 返回了空内容）"
        logger.info(
            f"AI 响应 (模型={DEEPSEEK_MODEL}, 耗时={elapsed:.1f}s, "
            f"tokens={response.usage.total_tokens if response.usage else '?'})"
        )
        return reply.strip()

    except Exception as e:
        logger.error(f"AI API 调用失败: {e}")
        return f"❌ AI 服务异常: {e}"


async def _do_chat(
    user_id: int,
    message: str,
    group_id: Optional[int] = None,
) -> str:
    """执行一次完整的 AI 对话。

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
    messages = [{"role": "system", "content": DEEPSEEK_SYSTEM_PROMPT}]
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

    # 发送"正在思考..."提示
    await chat_cmd.send("🤔 正在思考...")
    reply = await _do_chat(event.user_id, text, group_id)
    await chat_cmd.finish(reply)


# ============================================================================
# 群内 @机器人 触发
# ============================================================================

at_chat = on_command(
    "",  # 空命令名，匹配所有命令前缀的 @消息
    rule=to_me() & WHITELIST,
    priority=90,
    block=False,
)


# 实际上 NoneBot2 的 on_command("") 不支持 to_me 下很好工作
# 改用 on_message + to_me rule 组合
from nonebot import on_message

at_handler = on_message(rule=to_me() & WHITELIST, priority=90, block=False)


@at_handler.handle()
async def handle_at_mention(bot: Bot, event: Event):
    """处理群内 @机器人 的消息。"""
    # 只处理群消息（私聊由 /chat 命令处理）
    if not isinstance(event, GroupMessageEvent):
        return

    # 跳过带命令前缀的消息（让其他命令处理器接管）
    text = str(event.message).strip()
    if not text:
        return

    # 提取纯文本（去除 @CQ 码）
    pure_text = event.get_plaintext().strip()
    if not pure_text:
        return

    group_id = event.group_id
    user_id = event.user_id

    logger.info(f"[AI @触发] 群{group_id} 用户{user_id}: {pure_text[:50]}")

    try:
        reply = await _do_chat(user_id, pure_text, group_id)
        # @ 回复
        at_seg = MessageSegment.at(user_id)
        await bot.send_group_msg(
            group_id=group_id,
            message=at_seg + MessageSegment.text("\n" + reply),
        )
    except Exception as e:
        logger.error(f"@AI 回复失败: {e}")


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

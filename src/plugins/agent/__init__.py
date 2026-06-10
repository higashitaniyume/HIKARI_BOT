"""AI Agent 插件 —— 统一消息入口，通过 function calling 让 AI 自己路由。

模块拆分：
    __init__.py  消息入口 + Agent 主循环 + 频率限制 + 群聊上下文
    tools.py     工具定义与实现
    memory.py    对话记忆管理
    client.py    DeepSeek 客户端 + Token 估算
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.rule import Rule

from src.core.config import get_system_prompt
from src.core.message_store import get_message_store
from src.plugins.admin import get_whitelist

from .client import call_ai
from .memory import get_memory
from .tools import TOOLS, execute_tool

logger = logging.getLogger("hikari.plugins.agent")

# ============================================================================
# 频率限制
# ============================================================================

_MIN_CHAT_INTERVAL = 5.0
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


# ============================================================================
# 群聊上下文 & 用户识别
# ============================================================================

# 群聊上下文消息条数
_GROUP_CONTEXT_COUNT = 10


async def _build_group_context(group_id: int, current_user_id: int, count: int = _GROUP_CONTEXT_COUNT) -> str:
    """从 message_store 读取最近消息，构建上下文和用户映射。

    Returns:
        格式化的上下文文本（最近消息 + 群成员QQ→昵称映射）
    """
    store = get_message_store()
    try:
        msgs = await store.get_group_messages(group_id)
    except Exception:
        return ""

    if not msgs:
        return ""

    # 取最近 N 条
    recent = msgs[-count:]

    # 构建 QQ→昵称 映射（去重保留最新昵称）
    name_map: dict[int, str] = {}
    lines: list[str] = []
    for m in recent:
        sender = m.get("sender", {})
        uid = sender.get("user_id", 0)
        nick = sender.get("card") or sender.get("nickname") or str(uid)
        name_map[uid] = nick
        msg_text = str(m.get("message", ""))[:120]
        lines.append(f"  {nick}(QQ{uid}): {msg_text}")

    # 构建注入文本
    parts: list[str] = []
    parts.append("[群聊最近消息]")
    parts.extend(lines)

    # 群成员映射（最近发言的人）
    if name_map:
        parts.append("\n[群成员映射（昵称→QQ，可用于at_user参数）]")
        for uid, nick in sorted(name_map.items()):
            marker = " ← 当前说话者" if uid == current_user_id else ""
            parts.append(f"  {nick} → QQ {uid}{marker}")

    return "\n".join(parts)


def _build_time_hint(user_id: int, group_id: Optional[int]) -> str:
    """构建当前时间 + 用户上下文的提示文本。"""
    now = datetime.now()
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    hint = (
        f"\n\n[系统信息]\n"
        f"当前时间: {now.year}年{now.month}月{now.day}日 "
        f"星期{weekday} {now.hour:02d}:{now.minute:02d}:{now.second:02d}\n"
        f"当前用户QQ: {user_id}"
    )
    if group_id:
        hint += f"\n当前群号: {group_id}"
    return hint


# ============================================================================
# Agent 主循环
# ============================================================================


async def _agent_loop(
    bot: Bot,
    event: Event,
    user_id: int,
    user_msg: str,
    group_id: Optional[int],
    context_count: int = _GROUP_CONTEXT_COUNT,
) -> None:
    """Agent 主循环：构建上下文 → 调用 AI → 执行工具 → 最终回复。

    群聊场景下自动注入：
        - 当前时间 + 用户/群信息
        - 最近 {_GROUP_CONTEXT_COUNT} 条消息作为上下文
        - 群成员 昵称→QQ 映射（AI 可通过昵称 @人）
    """
    mem = get_memory()
    history = await mem.get_memory(user_id, group_id)

    # ── 构建系统提示词 ──────────────────────────────
    system_prompt = get_system_prompt() + _build_time_hint(user_id, group_id)

    # ── 长期记忆（memory.md）─────────────────────────
    # 先注入群共享记忆，再注入个人记忆
    if group_id:
        group_mem = await mem.get_group_memory(group_id)
        if group_mem:
            system_prompt += "\n\n" + group_mem
    long_term = await mem.get_long_term_memory(user_id, group_id)
    if long_term:
        system_prompt += "\n\n" + long_term

    # ── 群聊上下文 ──────────────────────────────────
    group_context = ""
    if group_id:
        group_context = await _build_group_context(group_id, user_id, count=context_count)
        if group_context:
            system_prompt += "\n\n" + group_context

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    # ── Function calling 循环（最多 3 轮）────────────
    for _round in range(3):
        response = await call_ai(messages, tools=TOOLS)
        msg = response["message"]
        tool_calls = msg.get("tool_calls")

        if not tool_calls:
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
                await mem.append(user_id, user_msg, reply, group_id)
            return

        # AI 调用了工具 → 执行 → 结果加入对话
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

            result = await execute_tool(
                bot, event, func_name, func_args, user_id, group_id,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    # ── 超过最大轮数，强制生成回复 ──────────────────
    logger.warning("Agent 达到最大 function calling 轮数，强制生成回复")
    final = await call_ai(messages, tools=None)
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


async def _whitelist_check(event: Event) -> bool:
    return get_whitelist().is_allowed(event)


WHITELIST = Rule(_whitelist_check)


async def _need_me_rule(event: Event) -> bool:
    """私聊始终通过，群聊必须 @机器人。"""
    if isinstance(event, PrivateMessageEvent):
        return True
    if isinstance(event, GroupMessageEvent):
        return event.is_tome()
    return False


NEED_ME = Rule(_need_me_rule)

agent_handler = on_message(rule=WHITELIST & NEED_ME, priority=3, block=True)


@agent_handler.handle()
async def handle_agent(bot: Bot, event: Event):
    """Agent 统一入口。"""
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return

    user_id = event.user_id
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None

    pure_text = event.get_plaintext().strip()

    silent_at = False  # 标记：是否只 @ 没说话

    # 群聊中只 @机器人 但没说话 → 只看最近 2 条上下文，简要回应
    if not pure_text:
        if isinstance(event, GroupMessageEvent):
            pure_text = "（你被@了，简单看看上文，回一句就行）"
            silent_at = True
        else:
            return

    location = f"群{group_id}" if group_id else "私聊"
    logger.info(f"[Agent] {location} {user_id}: {pure_text[:100]}")

    # 频率限制（静默）
    allowed, _ = _check_cooldown(user_id, group_id)
    if not allowed:
        return

    _set_cooldown(user_id, group_id)

    try:
        ctx_count = 2 if silent_at else _GROUP_CONTEXT_COUNT
        await _agent_loop(bot, event, user_id, pure_text, group_id, context_count=ctx_count)
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

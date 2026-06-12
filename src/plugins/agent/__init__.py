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

from src.core.config import get_skill_prompt, get_user_skill
from src.core.message_store import get_message_store
from src.plugins.admin import get_whitelist

from .client import call_ai
from .memory import get_memory
from .tools import TOOLS, execute_tool

logger = logging.getLogger("hikari.plugins.agent")

# ============================================================================
# 常量
# ============================================================================

_MIN_CHAT_INTERVAL = 5.0          # 频率限制（秒）
_GROUP_CONTEXT_COUNT = 10         # 正常 @ 时的上下文条数
_SILENT_CONTEXT_FETCH = 3         # 只 @ 不说话时获取的历史消息条数

# 错误模式：匹配非自然语言的报错/异常/traceback
import re as _re
_ERROR_PATTERNS = [
    _re.compile(r"❌|⏱️|⚠️"),
    _re.compile(r"Traceback|File \".+?\", line \d+|AttributeError|TypeError|KeyError|ValueError|ImportError|ModuleNotFoundError|ConnectionError|TimeoutError|HTTPStatusError|NoneType", _re.IGNORECASE),
    _re.compile(r"'[^']*' object has no attribute|missing \d+ required|Network is unreachable|Connection refused|timed out", _re.IGNORECASE),
    _re.compile(r"❌\s*AI|❌\s*API|API调用|解析失败|下载失败|搜索失败|发送失败|查询失败", _re.IGNORECASE),
]

# 超级管理员 QQ（硬编码兜底）
_SAFE_QQ = 3433559280


def _filter_outgoing(text: str, target_user_id: int) -> str:
    """拦截非自然语言的错误消息，非超级管理员只看到友好提示。"""
    if target_user_id == _SAFE_QQ or not text:
        return text

    # 检查是否匹配错误模式
    is_error = False
    for pat in _ERROR_PATTERNS:
        if pat.search(text):
            is_error = True
            break

    if is_error:
        logger.warning(f"拦截错误输出 → 用户{target_user_id}，原文前80字: {text[:80]}")
        return "出了点小事故喵，taffy脑子有点乱，等一下再试试~"

    return text

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


# ============================================================================
# 群聊上下文 & 用户识别
# ============================================================================


async def _build_group_context(
    group_id: int, current_user_id: int,
    count: int = _GROUP_CONTEXT_COUNT,
) -> str:
    """从 message_store 读取最近消息，构建上下文和用户映射。"""
    store = get_message_store()
    try:
        msgs = await store.get_group_messages(group_id)
    except Exception:
        return ""

    if not msgs:
        return ""

    recent = msgs[-count:]
    name_map: dict[int, str] = {}
    lines: list[str] = []
    for m in recent:
        sender = m.get("sender", {})
        uid = sender.get("user_id", 0)
        nick = sender.get("card") or sender.get("nickname") or str(uid)
        name_map[uid] = nick
        msg_text = str(m.get("message", ""))[:120]
        lines.append(f"  {nick}(QQ{uid}): {msg_text}")

    parts: list[str] = []
    parts.append("[群聊最近消息]")
    parts.extend(lines)

    if name_map:
        parts.append("\n[群成员映射（昵称→QQ，可用于at_user参数）]")
        for uid, nick in sorted(name_map.items()):
            marker = " ← 当前说话者" if uid == current_user_id else ""
            parts.append(f"  {nick} → QQ {uid}{marker}")

    return "\n".join(parts)


def _build_time_hint(user_id: int, group_id: Optional[int]) -> str:
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
# 只 @ 不说话：用嵌入模型检测话题切换
# ============================================================================


async def _analyze_silent_at(bot: Bot, group_id: int) -> str:
    """获取 @ 消息前的最近消息，让 AI 根据上下文接话。

    优先用 get_group_msg_history API，失败时回退到 message_store。
    """
    texts: list[str] = []

    # ── 方案 A：OneBot API ──────────────────────────
    try:
        msgs = await bot.get_group_msg_history(
            group_id=group_id, count=_SILENT_CONTEXT_FETCH + 1,
        )
        logger.debug(f"get_group_msg_history 返回 {len(msgs)} 条")
        # msgs[0] = @机器人的那条，msgs[1..] = 前面的
        for m in msgs[1:]:
            if hasattr(m, "message"):
                t = str(m.message).strip()
                if t:
                    texts.append(t)
    except Exception as e:
        logger.debug(f"get_group_msg_history 失败: {e}，回退到 message_store")
        msgs = None

    # ── 方案 B：回退到 message_store ────────────────
    if not texts:
        try:
            store = get_message_store()
            all_msgs = await store.get_group_messages(group_id)
            if all_msgs:
                # 取最后 N 条（排除 @机器人 那条）
                for m in all_msgs[-_SILENT_CONTEXT_FETCH - 1:-1]:
                    t = str(m.get("message", "")).strip()
                    if t:
                        texts.append(t)
        except Exception as e:
            logger.warning(f"message_store 读取也失败: {e}")

    # ── 无上下文 ──────────────────────────────────
    if not texts:
        return "（你被@了但前面没人说话，打个招呼就好）"

    context = "\n".join(f"  {t[:150]}" for t in texts)
    logger.info(f"只@不说话上下文 ({len(texts)} 条): {texts[:2]}")
    return (
        f"你被@了。以下是@你之前的群聊消息：\n"
        f"{context}\n\n"
        f"请根据上面这些消息自然地接一句话，简短一点，不要提'被@了'这件事。"
    )


# ============================================================================
# Agent 主循环
# ============================================================================


def _build_reply_segments(event: Event, reply_text: str) -> list:
    """构建群聊回复消息段：引用 + @ + 文本。私聊只返回文本。"""
    if isinstance(event, GroupMessageEvent):
        msg_id = getattr(event, "message_id", None)
        segments = []
        if msg_id:
            segments.append(MessageSegment.reply(msg_id))
        segments.append(MessageSegment.at(event.user_id))
        segments.append(MessageSegment.text("\n" + reply_text))
        return segments
    else:
        return [MessageSegment.text(reply_text)]


async def _agent_loop(
    bot: Bot,
    event: Event,
    user_id: int,
    user_msg: str,
    group_id: Optional[int],
    context_count: int = _GROUP_CONTEXT_COUNT,
) -> None:
    mem = get_memory()
    history = await mem.get_memory(user_id, group_id)

    # ── 构建系统提示词 ──────────────────────────────
    skill_name = get_user_skill(user_id)
    system_prompt = get_skill_prompt(skill_name) + _build_time_hint(user_id, group_id)

    # 长期记忆
    if group_id:
        group_mem = await mem.get_group_memory(group_id)
        if group_mem:
            system_prompt += "\n\n" + group_mem
    long_term = await mem.get_long_term_memory(user_id, group_id)
    if long_term:
        system_prompt += "\n\n" + long_term

    # 群聊上下文
    if group_id:
        group_context = await _build_group_context(group_id, user_id, count=context_count)
        if group_context:
            system_prompt += "\n\n" + group_context

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    # ── Function calling 循环（最多 5 轮）────────────
    for _round in range(5):
        response = await call_ai(messages, tools=TOOLS)
        msg = response["message"]
        tool_calls = msg.get("tool_calls")

        if not tool_calls:
            reply = msg.get("content", "").strip()
            if reply:
                reply = _filter_outgoing(reply, user_id)
                segs = _build_reply_segments(event, reply)
                if isinstance(event, GroupMessageEvent):
                    await bot.send_group_msg(group_id=event.group_id, message=segs)
                else:
                    await bot.send_private_msg(user_id=user_id, message=segs[0])
                await mem.append(user_id, user_msg, reply, group_id)
            return

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

            if func_name == "send_message":
                await mem.append(user_id, user_msg, func_args.get("text", ""), group_id)
                return

    # 超过最大轮数
    logger.warning("Agent 达到最大 function calling 轮数，强制生成回复")
    final = await call_ai(messages, tools=None)
    reply = final["message"].get("content", "").strip()
    if reply:
        reply = _filter_outgoing(reply, user_id)
        segs = _build_reply_segments(event, reply)
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_msg(group_id=event.group_id, message=segs)
        else:
            await bot.send_private_msg(user_id=user_id, message=segs[0])
        await mem.append(user_id, user_msg, reply, group_id)


# ============================================================================
# 消息入口
# ============================================================================


async def _whitelist_check(event: Event) -> bool:
    return get_whitelist().is_allowed(event)


WHITELIST = Rule(_whitelist_check)


async def _need_me_rule(event: Event) -> bool:
    if isinstance(event, PrivateMessageEvent):
        return True
    if isinstance(event, GroupMessageEvent):
        return event.is_tome()
    return False


NEED_ME = Rule(_need_me_rule)

agent_handler = on_message(rule=WHITELIST & NEED_ME, priority=3, block=True)


@agent_handler.handle()
async def handle_agent(bot: Bot, event: Event):
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return

    user_id = event.user_id
    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    pure_text = event.get_plaintext().strip()

    # ── 回复检测 ──────────────────────────────────
    reply_msg = getattr(event, "reply", None)
    if reply_msg and getattr(reply_msg, "user_id", None) == event.self_id:
        replied_text = str(reply_msg.message).strip() if hasattr(reply_msg, "message") else ""
        if replied_text:
            prefix = f"（用户回复了你之前发的消息「{replied_text[:200]}」"
            if pure_text:
                prefix += f"，并说: {pure_text}）"
            else:
                prefix += "）"
            pure_text = prefix

    silent_at = False

    # ── 只 @ 不说话：嵌入模型检测话题切换 ──────────
    if not pure_text:
        if isinstance(event, GroupMessageEvent):
            pure_text = await _analyze_silent_at(bot, group_id)
            silent_at = True
        else:
            return

    location = f"群{group_id}" if group_id else "私聊"
    logger.info(f"[Agent] {location} {user_id}: {pure_text[:100]}")

    allowed, _ = _check_cooldown(user_id, group_id)
    if not allowed:
        return

    _set_cooldown(user_id, group_id)

    try:
        ctx_count = 2 if silent_at else _GROUP_CONTEXT_COUNT
        await _agent_loop(bot, event, user_id, pure_text, group_id, context_count=ctx_count)
    except Exception as e:
        logger.exception(f"Agent 处理异常: {e}")
        try:
            error_text = _filter_outgoing("❌ 出了点问题，请稍后再试~", user_id)
            if isinstance(event, GroupMessageEvent):
                await bot.send_group_msg(
                    group_id=event.group_id,
                    message=MessageSegment.at(user_id)
                    + MessageSegment.text("\n" + error_text),
                )
            else:
                await bot.send_private_msg(user_id=user_id, message=error_text)
        except Exception:
            pass

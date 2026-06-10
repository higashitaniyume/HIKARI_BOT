"""消息收集插件 —— 捕获所有消息事件并持久化存储。

监听所有消息（私聊 + 群聊），提取发送者信息和消息内容，
存入 JSON 文件的同时输出结构化日志。
"""

import logging
import time
from datetime import datetime

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
)

from src.core.message_store import get_message_store

logger = logging.getLogger("hikari.plugins.message_collector")

# 全局消息存储实例
_store = get_message_store()

# ─── 消息事件处理器 ─────────────────────────────────────────────

msg_handler = on_message(priority=80, block=False)


@msg_handler.handle()
async def handle_message(event: MessageEvent):
    """处理所有消息事件，自动区分私聊和群聊。"""
    now = datetime.now()
    timestamp = time.time()

    # 提取消息文本（NoneBot Message 对象自动转字符串）
    message_text = str(event.message)
    if not message_text.strip():
        return  # 跳过空消息

    # 提取消息 ID
    message_id = getattr(event, "message_id", None)

    if isinstance(event, GroupMessageEvent):
        await _handle_group_msg(event, now, timestamp, message_text, message_id)
    elif isinstance(event, PrivateMessageEvent):
        await _handle_private_msg(event, now, timestamp, message_text, message_id)
    else:
        # 兜底：其他消息类型，记录基本信息
        logger.debug(f"[其他消息] user={event.user_id} | {message_text[:50]}")


# ─── 群消息处理 ─────────────────────────────────────────────────

async def _handle_group_msg(
    event: GroupMessageEvent,
    now: datetime,
    timestamp: float,
    message_text: str,
    message_id: int | None,
) -> None:
    """处理并存储群消息。"""
    qq = event.user_id
    group_id = event.group_id
    nickname = event.sender.nickname or ""
    card = event.sender.card or ""  # 群名片
    role = event.sender.role or "member"

    record = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": round(timestamp, 3),
        "message_id": message_id,
        "sender": {
            "user_id": qq,
            "nickname": nickname,
            "card": card,
            "role": role,
        },
        "group_id": group_id,
        "message": message_text,
        "raw_message": event.raw_message,
    }

    await _store.save_group_msg(group_id, record)

    # 结构化日志
    display_name = card or nickname
    logger.info(
        f"[群消息] 群{group_id} | {qq}({display_name}) | {message_text[:100]}"
    )


# ─── 私聊消息处理 ─────────────────────────────────────────────────

async def _handle_private_msg(
    event: PrivateMessageEvent,
    now: datetime,
    timestamp: float,
    message_text: str,
    message_id: int | None,
) -> None:
    """处理并存储私聊消息。"""
    qq = event.user_id
    nickname = event.sender.nickname or ""

    record = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": round(timestamp, 3),
        "message_id": message_id,
        "sender": {
            "user_id": qq,
            "nickname": nickname,
        },
        "message": message_text,
        "raw_message": event.raw_message,
    }

    await _store.save_private_msg(qq, record)

    # 结构化日志
    logger.info(
        f"[私聊消息] {qq}({nickname}) | {message_text[:100]}"
    )

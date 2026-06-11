"""消息收集插件 —— 捕获所有消息事件并持久化存储 + 语义向量嵌入。

监听所有消息（私聊 + 群聊），提取发送者信息和消息内容，
存入 JSON 文件，同时异步嵌入到向量存储中以供语义搜索。
"""

import asyncio
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

from src.core.embedding import get_vector_store
from src.core.message_store import get_message_store

logger = logging.getLogger("hikari.plugins.message_collector")

_store = get_message_store()


def _embed_in_background(
    group_id: int | None, user_id: int, text: str,
    sender_name: str, sender_id: int,
    msg_time: str, msg_id: int | None,
) -> None:
    """后台异步嵌入，不影响消息处理主流程。"""
    async def _run():
        try:
            await get_vector_store().add(
                group_id=group_id, user_id=user_id, text=text,
                sender_name=sender_name, sender_id=sender_id,
                msg_time=msg_time, msg_id=msg_id,
            )
        except Exception:
            pass

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        pass


msg_handler = on_message(priority=1, block=False)


@msg_handler.handle()
async def handle_message(event: MessageEvent):
    """处理所有消息事件，自动区分私聊和群聊。"""
    now = datetime.now()
    timestamp = time.time()

    message_text = str(event.message)
    if not message_text.strip():
        return

    message_id = getattr(event, "message_id", None)

    if isinstance(event, GroupMessageEvent):
        await _handle_group_msg(event, now, timestamp, message_text, message_id)
    elif isinstance(event, PrivateMessageEvent):
        await _handle_private_msg(event, now, timestamp, message_text, message_id)
    else:
        logger.debug(f"[其他消息] user={event.user_id} | {message_text[:50]}")


async def _handle_group_msg(
    event: GroupMessageEvent,
    now: datetime,
    timestamp: float,
    message_text: str,
    message_id: int | None,
) -> None:
    qq = event.user_id
    group_id = event.group_id
    nickname = event.sender.nickname or ""
    card = event.sender.card or ""
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

    _embed_in_background(
        group_id=group_id, user_id=qq, text=message_text,
        sender_name=card or nickname, sender_id=qq,
        msg_time=record["time"], msg_id=message_id,
    )

    display_name = card or nickname
    logger.info(
        f"[群消息] 群{group_id} | {qq}({display_name}) | {message_text[:100]}"
    )


async def _handle_private_msg(
    event: PrivateMessageEvent,
    now: datetime,
    timestamp: float,
    message_text: str,
    message_id: int | None,
) -> None:
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

    _embed_in_background(
        group_id=None, user_id=qq, text=message_text,
        sender_name=nickname, sender_id=qq,
        msg_time=record["time"], msg_id=message_id,
    )

    logger.info(
        f"[私聊消息] {qq}({nickname}) | {message_text[:100]}"
    )

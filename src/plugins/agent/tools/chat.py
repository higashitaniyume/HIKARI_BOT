"""聊天 & 主动消息工具。"""

import re
from typing import Optional

from nonebot.adapters.onebot.v11 import (
    Bot, Event, GroupMessageEvent, MessageSegment, PrivateMessageEvent,
)

from . import _filter_error


async def tool_send_message(
    bot: Bot, event: Event, text: str,
    user_id: int, group_id: Optional[int],
    at_user: int | None = None,
) -> str:
    """发送聊天回复，可指定 @ 目标。"""
    # 安全阀：AI 如果把 [CQ:at,qq=xxx] 写进 text，自动提取并清理
    if isinstance(text, str):
        cq_at = re.search(r"\[CQ:at,qq=(\d+)\]", text)
        if cq_at:
            if at_user is None or at_user == 0:
                at_user = int(cq_at.group(1))
            text = re.sub(r"\[CQ:at,qq=\d+\]\s*", "", text).strip()
    text = _filter_error(text, user_id)
    if isinstance(event, GroupMessageEvent):
        if at_user == 0:
            await bot.send_group_msg(
                group_id=event.group_id, message=MessageSegment.text(text))
        elif at_user is not None and at_user > 0:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.at(at_user) + MessageSegment.text("\n" + text))
        else:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.at(user_id) + MessageSegment.text("\n" + text))
    else:
        await bot.send_private_msg(user_id=user_id, message=text)
    return "消息已发送"


async def tool_send_to(
    bot: Bot, target: str, text: str, at_user: int | None = None,
    *, event: Event | None = None,
) -> str:
    """主动向指定目标发送消息。安全限制：群聊只能发本群，私聊只能发自己。"""
    from src.core.config import SUPER_ADMIN as _SA

    if event is not None and event.user_id != _SA:
        if isinstance(event, GroupMessageEvent):
            allowed = f"group:{event.group_id}"
            if target != allowed:
                return f"❌ 安全限制：只能发送到本群 ({allowed})。"
        elif isinstance(event, PrivateMessageEvent):
            if target != str(event.user_id):
                return "❌ 安全限制：私聊中只能发送给自己。"

    is_group = target.startswith("group:")
    try:
        if is_group:
            gid = int(target[len("group:"):])
            safe_text = _filter_error(text, at_user or 0)
            msg = MessageSegment.text(safe_text)
            if at_user and at_user > 0:
                msg = MessageSegment.at(at_user) + MessageSegment.text("\n" + safe_text)
            await bot.send_group_msg(group_id=gid, message=msg)
            return f"已发送到群 {gid}"
        else:
            qq = int(target)
            safe_text = _filter_error(text, qq)
            await bot.send_private_msg(user_id=qq, message=safe_text)
            return f"已发送到 QQ {qq}"
    except Exception as e:
        return f"❌ 发送失败: {e}"

"""白名单管理工具。"""

from typing import Optional

from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent

from src.core.config import SUPER_ADMIN
from src.plugins.admin import get_whitelist


async def tool_manage_whitelist(
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

"""群成员信息工具。"""

from nonebot.adapters.onebot.v11 import Bot


async def tool_get_group_info(bot: Bot, group_id: int) -> str:
    """获取群聊成员信息。"""
    try:
        members = await bot.get_group_member_list(group_id=group_id)
    except Exception as e:
        return f"❌ 获取群成员失败: {e}"

    if not members:
        return "未获取到群成员信息"

    total = len(members)
    owner_list, admin_list, member_list = [], [], []
    for m in members:
        role = m.get("role", "member")
        if role == "owner":
            owner_list.append(m)
        elif role == "admin":
            admin_list.append(m)
        else:
            member_list.append(m)

    lines = [f"群成员共 {total} 人"]
    for m in owner_list:
        nick = m.get("card") or m.get("nickname", "?")
        lines.append(f"\n👑 群主: {nick} (QQ {m['user_id']})")

    if admin_list:
        admins = [
            f"  {m.get('card') or m.get('nickname', '?')} (QQ {m['user_id']})"
            for m in admin_list
        ]
        lines.append(f"\n🛡 管理员 ({len(admin_list)} 人):")
        lines.extend(admins)

    if total <= 30 and member_list:
        ms = [
            f"  {m.get('card') or m.get('nickname', '?')} (QQ {m['user_id']})"
            for m in member_list
        ]
        lines.append(f"\n👥 成员 ({len(member_list)} 人):")
        lines.extend(ms)
    elif total > 30:
        lines.append(f"\n👥 普通成员: {len(member_list)} 人（人数较多，仅列出管理员以上）")

    return "\n".join(lines)

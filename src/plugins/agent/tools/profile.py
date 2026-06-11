"""QQ 用户名片查询工具。"""

from nonebot.adapters.onebot.v11 import Bot


async def tool_get_user_profile(bot: Bot, user_id: int, group_id: int | None = None) -> str:
    """获取 QQ 用户的名片信息。

    优先通过 get_group_member_info（群内更详细），
    回退到 get_stranger_info（跨群/私聊也能查到）。
    """
    info = None

    # 方案 A：群内查（含群名片、角色等）
    if group_id:
        try:
            info = await bot.get_group_member_info(
                group_id=group_id, user_id=user_id, no_cache=False)
        except Exception:
            pass

    # 方案 B：陌生人信息（兜底）
    if info is None:
        try:
            info = await bot.get_stranger_info(user_id=user_id, no_cache=False)
        except Exception as e:
            return f"❌ 无法获取用户 {user_id} 的信息: {e}"

    if not info:
        return f"未找到用户 {user_id} 的信息"

    # 格式化输出
    lines = [f"QQ {user_id} 的名片信息："]

    nick = info.get("nickname", "?")
    lines.append(f"  昵称: {nick}")

    sex_map = {"male": "男", "female": "女", "unknown": "未知"}
    sex = sex_map.get(info.get("sex", "unknown"), "未知")
    lines.append(f"  性别: {sex}")

    age = info.get("age", 0)
    if age:
        lines.append(f"  年龄: {age}")

    card = info.get("card", "")
    if card:
        lines.append(f"  群名片: {card}")

    role_map = {"owner": "群主", "admin": "管理员", "member": "成员"}
    role = info.get("role", "")
    if role:
        lines.append(f"  群角色: {role_map.get(role, role)}")

    qid = info.get("qid", "")
    if qid:
        lines.append(f"  QID: {qid}")

    level = info.get("level", 0)
    if level:
        lines.append(f"  等级: {level}")

    area = info.get("area", "")
    if area:
        lines.append(f"  地区: {area}")

    return "\n".join(lines)

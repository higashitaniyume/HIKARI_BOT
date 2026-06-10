"""拍一拍插件 —— 被拍自动拍回去。"""

from nonebot import on_notice, get_bot, logger
from nonebot.adapters.onebot.v11 import Bot, PokeNotifyEvent


async def _poke_back(bot: Bot, user_id: int, group_id: int | None) -> bool:
    """尝试拍回去。私聊用 friend_poke，群聊用 group_poke。"""
    try:
        if group_id:
            await bot.call_api("group_poke", group_id=group_id, user_id=user_id)
        else:
            await bot.call_api("friend_poke", user_id=user_id)
        return True
    except Exception:
        # 部分 OneBot 实现可能不支持 poke API，静默忽略
        return False


poke_handler = on_notice(priority=10, block=False)


@poke_handler.handle()
async def handle_poke(bot: Bot, event: PokeNotifyEvent):
    """被拍时拍回去。"""
    # 确认目标是自己
    if event.target_id != event.self_id:
        return

    who = f"群{event.group_id}的{event.user_id}" if event.group_id else f"私聊{event.user_id}"
    logger.info(f"被 {who} 拍了一下，拍回去")

    ok = await _poke_back(bot, event.user_id, event.group_id)
    if ok:
        logger.info(f"已拍回 {who}")
    else:
        logger.debug(f"拍回失败（API 可能不支持）: {who}")

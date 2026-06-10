"""Hello World 插件 —— Bot 上线后向指定 QQ 号发送问候消息。"""

from nonebot import on_type, get_bot, logger
from nonebot.adapters.onebot.v11 import LifecycleMetaEvent

# 目标 QQ 号
TARGET_QQ = 3433559280

# 确保只发送一次（避免每次重连都发）
_sent = False

# 注册生命周期事件响应器
lifecycle = on_type(LifecycleMetaEvent)


@lifecycle.handle()
async def handle_lifecycle(event: LifecycleMetaEvent):
    """监听生命周期事件，在 Bot 连接成功时发送 Hello World。"""
    global _sent

    if _sent:
        return

    if event.sub_type == "connect":
        bot = get_bot()  # 获取当前连接的 Bot 实例
        logger.info(f"HIKARI_BOT 已上线！Bot self_id={event.self_id}")
        try:
            await bot.send_private_msg(
                user_id=TARGET_QQ,
                message="Hello World! HIKARI_BOT 已上线 🎉",
            )
            logger.success(f"Hello World 消息已发送至 QQ {TARGET_QQ}")
            _sent = True
        except Exception as e:
            logger.error(f"发送 Hello World 消息失败: {e}")

"""上线通知插件 —— Bot 连接成功后向指定 QQ 发送问候消息，并启动日志推送。"""

import logging

from nonebot import on_type, get_bot, logger
from nonebot.adapters.onebot.v11 import LifecycleMetaEvent

from src.core.config import get_version, SUPER_ADMIN
from src.core.log_handler import QQLogHandler

# 目标 QQ 号
TARGET_QQ = 3433559280

# 确保只发送一次（避免每次重连都发）
_sent = False

# QQ 日志推送处理器（全局单例）
_qq_log_handler: QQLogHandler | None = None

# 注册生命周期事件响应器
lifecycle = on_type(LifecycleMetaEvent)


@lifecycle.handle()
async def handle_lifecycle(event: LifecycleMetaEvent):
    """监听生命周期事件，在 Bot 连接成功时发送上线通知并启动日志推送。"""
    global _sent, _qq_log_handler

    if _sent:
        return

    if event.sub_type == "connect":
        bot = get_bot()
        logger.info(f"HIKARI_BOT 已上线！Bot self_id={event.self_id}")

        # ── 启动 QQ 日志推送 ────────────────────────
        if _qq_log_handler is None:
            _qq_log_handler = QQLogHandler(SUPER_ADMIN)
            _qq_log_handler.set_bot(bot)
            logging.getLogger().addHandler(_qq_log_handler)

        # ── 文字问候 ──────────────────────────────
        try:
            version_str = get_version()
            await bot.send_private_msg(
                user_id=TARGET_QQ,
                message=f"Hello World! HIKARI_BOT {version_str} 已上线 🎉",
            )
            logger.success(f"上线通知已发送至 QQ {TARGET_QQ} ({version_str})")
        except Exception as e:
            logger.error(f"发送上线通知失败: {e}")

        _sent = True

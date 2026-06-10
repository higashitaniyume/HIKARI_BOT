"""上线通知插件 —— Bot 连接成功后向指定 QQ 发送问候消息和测试图片。"""

import base64
from pathlib import Path

from nonebot import on_type, get_bot, logger
from nonebot.adapters.onebot.v11 import LifecycleMetaEvent, MessageSegment

# 目标 QQ 号
TARGET_QQ = 3433559280

# 启动时自动发送的图片（设为 None 则不发送）
AUTO_IMAGE_PATH = r"C:\PublicFiles\Downloads\test1111.png"

# 确保只发送一次（避免每次重连都发）
_sent = False

# 注册生命周期事件响应器
lifecycle = on_type(LifecycleMetaEvent)


@lifecycle.handle()
async def handle_lifecycle(event: LifecycleMetaEvent):
    """监听生命周期事件，在 Bot 连接成功时发送上线通知和测试图片。"""
    global _sent

    if _sent:
        return

    if event.sub_type == "connect":
        bot = get_bot()
        logger.info(f"HIKARI_BOT 已上线！Bot self_id={event.self_id}")

        # ── 发送文字问候 ──────────────────────────────
        try:
            await bot.send_private_msg(
                user_id=TARGET_QQ,
                message="Hello World! HIKARI_BOT 已上线 🎉",
            )
            logger.success(f"上线通知已发送至 QQ {TARGET_QQ}")
        except Exception as e:
            logger.error(f"发送上线通知失败: {e}")

        # ── 发送测试图片 ──────────────────────────────
        if AUTO_IMAGE_PATH:
            img_path = Path(AUTO_IMAGE_PATH)
            if img_path.exists():
                try:
                    img_data = img_path.read_bytes()
                    b64 = base64.b64encode(img_data).decode("ascii")
                    await bot.send_private_msg(
                        user_id=TARGET_QQ,
                        message=MessageSegment.image(f"base64://{b64}"),
                    )
                    logger.success(
                        f"测试图片已发送至 QQ {TARGET_QQ}: {img_path.name} "
                        f"({len(img_data)} bytes)"
                    )
                except Exception as e:
                    logger.error(f"发送测试图片失败: {e}")
            else:
                logger.warning(f"测试图片不存在，跳过: {img_path}")

        _sent = True

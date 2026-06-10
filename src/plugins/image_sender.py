"""图片发送模块 —— 发送本地图片到指定 QQ 或群。

使用 base64 编码传输，跨机器兼容（bot 与 OneBot 服务端无需共享文件系统）。

命令：
    /sendimg <路径> <目标QQ>     — 发送图片到私聊
    /sendimg <路径> group:<群号> — 发送图片到群
"""
import base64
import logging
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.params import CommandArg

logger = logging.getLogger("hikari.plugins.image_sender")

# ─── 命令定义 ─────────────────────────────────────────────────────

send_img = on_command("sendimg", priority=10)


@send_img.handle()
async def handle_sendimg(bot: Bot, args: Message = CommandArg()):
    """发送图片命令。

    用法:
        /sendimg C:\path\to\image.png 3433559280
        /sendimg C:\path\to\image.png group:123456789
    """
    text = str(args).strip()
    if not text:
        await send_img.finish("用法: /sendimg <文件路径> <QQ号|group:群号>")

    # 按空格分割，最后一个参数是目标
    parts = text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await send_img.finish("缺少目标参数\n用法: /sendimg <文件路径> <QQ号|group:群号>")

    file_path_str, target_str = parts
    file_path_str = file_path_str.strip().strip('"').strip("'")
    target_str = target_str.strip()

    # ── 解析目标 ──────────────────────────────────────────
    is_group = False
    if target_str.startswith("group:"):
        is_group = True
        target_id = int(target_str[len("group:"):])
    else:
        target_id = int(target_str)

    # ── 读取文件 ──────────────────────────────────────────
    file_path = Path(file_path_str)
    if not file_path.exists():
        await send_img.finish(f"文件不存在: {file_path_str}")

    try:
        file_data = file_path.read_bytes()
    except Exception as e:
        logger.error(f"读取文件失败: {file_path_str} - {e}")
        await send_img.finish(f"读取文件失败: {e}")

    # ── Base64 编码 & 发送 ─────────────────────────────────
    try:
        b64 = base64.b64encode(file_data).decode("ascii")
        img_seg = MessageSegment.image(f"base64://{b64}")

        if is_group:
            await bot.send_group_msg(group_id=target_id, message=img_seg)
            logger.info(f"图片已发送到群 {target_id}: {file_path.name} ({len(file_data)} bytes)")
            await send_img.finish(f"已发送 {file_path.name} ({len(file_data)} bytes) -> 群 {target_id}")
        else:
            await bot.send_private_msg(user_id=target_id, message=img_seg)
            logger.info(f"图片已发送到 {target_id}: {file_path.name} ({len(file_data)} bytes)")
            await send_img.finish(f"已发送 {file_path.name} ({len(file_data)} bytes) -> QQ {target_id}")

    except Exception as e:
        logger.error(f"发送图片失败: {e}")
        await send_img.finish(f"发送失败: {e}")

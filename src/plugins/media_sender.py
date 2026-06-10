"""媒体发送模块 —— 统一发送图片、视频、语音。

基于 OneBot v11 协议，默认使用 base64:// 编码实现跨机器兼容。
若 bot 与 OneBot 同机部署，可改用 file:// 协议以获得更高效率。

命令：
    /sendimg   <文件路径> <QQ号|group:群号>
    /sendvideo <文件路径> <QQ号|group:群号>
    /sendvoice <文件路径> <QQ号|group:群号>

供其他插件调用：
    from src.plugins.media_sender import get_media_sender
    sender = get_media_sender()
    await sender.send_image(bot, target=3433559280, file_path="...")
    await sender.send_video(bot, target="group:123456", file_path="...")
    await sender.send_voice(bot, target=3433559280, file_path="...")
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional, Union

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.params import CommandArg

logger = logging.getLogger("hikari.plugins.media_sender")

# 默认协议：base64（跨机器）或 file（同机部署时更快）
# 改为 "file" 则要求 OneBot 服务端能直接访问该路径
PROTOCOL = "base64"


# ============================================================================
# MediaSender 核心类
# ============================================================================

class MediaSender:
    """媒体发送器。

    封装 OneBot v11 的 image/video/record 三种消息段，
    统一处理路径校验、编码和目标解析。
    """

    @staticmethod
    async def _read_and_encode(file_path: str | Path) -> tuple[bytes, str, str]:
        """读取文件并返回 (原始字节, mime类型, base64字符串)。

        Returns:
            (data, mime_type, base64_string)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")

        # 根据扩展名推测 MIME 类型
        suffix = path.suffix.lower()
        mime_map = {
            # 图片
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            # 视频
            ".mp4": "video/mp4",
            ".avi": "video/x-msvideo",
            ".mov": "video/quicktime",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
            # 语音
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".aac": "audio/aac",
            ".flac": "audio/flac",
            ".amr": "audio/amr",
            ".silk": "audio/silk",
        }
        mime = mime_map.get(suffix, "application/octet-stream")

        return data, mime, b64

    @staticmethod
    def _build_ref(file_path: str | Path) -> str:
        """根据全局 PROTOCOL 构建文件引用字符串。"""
        path = Path(file_path)
        if PROTOCOL == "file":
            return f"file:///{path.as_posix()}"
        else:
            data = path.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            return f"base64://{b64}"

    @staticmethod
    def _parse_target(target: str | int) -> tuple[bool, int]:
        """解析目标：返回 (是否群聊, ID)。"""
        if isinstance(target, int):
            return False, target
        s = str(target).strip()
        if s.startswith("group:"):
            return True, int(s[len("group:"):])
        return False, int(s)

    # ── 公开发送方法 ──────────────────────────────────────────

    @classmethod
    async def send_image(
        cls,
        bot: Bot,
        target: Union[int, str],
        file_path: str | Path,
    ) -> None:
        """发送图片。

        Args:
            bot: Bot 实例
            target: 私聊 QQ 号(int) 或 "group:群号"(str)
            file_path: 图片文件路径
        """
        path = Path(file_path)
        ref = cls._build_ref(path)
        img = MessageSegment.image(ref)
        is_group, target_id = cls._parse_target(target)

        if is_group:
            await bot.send_group_msg(group_id=target_id, message=img)
        else:
            await bot.send_private_msg(user_id=target_id, message=img)

        logger.info(
            "图片已发送 -> %s: %s (%d bytes)",
            f"群{target_id}" if is_group else f"QQ{target_id}",
            path.name,
            path.stat().st_size,
        )

    @classmethod
    async def send_video(
        cls,
        bot: Bot,
        target: Union[int, str],
        file_path: str | Path,
    ) -> None:
        """发送视频。

        Args:
            bot: Bot 实例
            target: 私聊 QQ 号 或 "group:群号"
            file_path: 视频文件路径
        """
        path = Path(file_path)
        ref = cls._build_ref(path)
        vid = MessageSegment.video(ref)
        is_group, target_id = cls._parse_target(target)

        if is_group:
            await bot.send_group_msg(group_id=target_id, message=vid)
        else:
            await bot.send_private_msg(user_id=target_id, message=vid)

        logger.info(
            "视频已发送 -> %s: %s (%d bytes)",
            f"群{target_id}" if is_group else f"QQ{target_id}",
            path.name,
            path.stat().st_size,
        )

    @classmethod
    async def send_voice(
        cls,
        bot: Bot,
        target: Union[int, str],
        file_path: str | Path,
    ) -> None:
        """发送语音（私聊 / 群聊均可）。

        Args:
            bot: Bot 实例
            target: 私聊 QQ 号 或 "group:群号"
            file_path: 音频文件路径（mp3/wav/ogg/amr 等）
        """
        path = Path(file_path)
        ref = cls._build_ref(path)
        voice = MessageSegment.record(ref)
        is_group, target_id = cls._parse_target(target)

        if is_group:
            await bot.send_group_msg(group_id=target_id, message=voice)
        else:
            await bot.send_private_msg(user_id=target_id, message=voice)

        logger.info(
            "语音已发送 -> %s: %s (%d bytes)",
            f"群{target_id}" if is_group else f"QQ{target_id}",
            path.name,
            path.stat().st_size,
        )


# ============================================================================
# 模块级单例
# ============================================================================

_media_sender: Optional[MediaSender] = None


def get_media_sender() -> MediaSender:
    """获取 MediaSender 实例（供其他插件调用）。"""
    global _media_sender
    if _media_sender is None:
        _media_sender = MediaSender()
    return _media_sender


# ============================================================================
# 命令：/sendimg  /sendvideo  /sendvoice
# ============================================================================

async def _handle_media(
    bot: Bot,
    args: Message,
    media_type: str,
) -> None:
    """统一处理媒体命令。"""
    sender = get_media_sender()
    send_func = {
        "image": sender.send_image,
        "video": sender.send_video,
        "voice": sender.send_voice,
    }[media_type]
    label = {"image": "图片", "video": "视频", "voice": "语音"}[media_type]

    text = str(args).strip()
    if not text:
        await on_command(media_type).finish(
            f"用法: /send{media_type} <文件路径> <QQ号|group:群号>"
        )

    parts = text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await on_command(media_type).finish(
            f"缺少目标参数\n用法: /send{media_type} <文件路径> <QQ号|group:群号>"
        )

    file_str = parts[0].strip().strip('"').strip("'")
    target_str = parts[1].strip()

    path = Path(file_str)
    if not path.exists():
        logger.warning(f"{label}文件不存在: {path}")
        # 注意：on_command 的 finish 需要从当前事件上下文获取
        # 这里只记录日志，实际 finish 由外层处理
        return  # 避免异常传递

    try:
        await send_func(bot, target=target_str, file_path=path)
    except Exception as e:
        logger.error(f"发送{label}失败: {e}")


# ── 图片命令 ──────────────────────────────────────────────

send_img_cmd = on_command("sendimg", priority=10)


@send_img_cmd.handle()
async def handle_sendimg(bot: Bot, args: Message = CommandArg()):
    """发送图片。"""
    text = str(args).strip()
    if not text:
        await send_img_cmd.finish("用法: /sendimg <文件路径> <QQ号|group:群号>")

    parts = text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await send_img_cmd.finish("缺少目标参数\n用法: /sendimg <文件路径> <QQ号|group:群号>")

    file_str = parts[0].strip().strip('"').strip("'")
    target_str = parts[1].strip()
    path = Path(file_str)

    if not path.exists():
        await send_img_cmd.finish(f"文件不存在: {file_str}")

    try:
        sender = get_media_sender()
        await sender.send_image(bot, target=target_str, file_path=path)
        await send_img_cmd.finish(
            f"图片已发送: {path.name} ({path.stat().st_size} bytes) -> {target_str}"
        )
    except Exception as e:
        logger.error(f"发送图片失败: {e}")
        await send_img_cmd.finish(f"发送失败: {e}")


# ── 视频命令 ──────────────────────────────────────────────

send_video_cmd = on_command("sendvideo", priority=10)


@send_video_cmd.handle()
async def handle_sendvideo(bot: Bot, args: Message = CommandArg()):
    """发送视频。"""
    text = str(args).strip()
    if not text:
        await send_video_cmd.finish("用法: /sendvideo <文件路径> <QQ号|group:群号>")

    parts = text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await send_video_cmd.finish("缺少目标参数\n用法: /sendvideo <文件路径> <QQ号|group:群号>")

    file_str = parts[0].strip().strip('"').strip("'")
    target_str = parts[1].strip()
    path = Path(file_str)

    if not path.exists():
        await send_video_cmd.finish(f"文件不存在: {file_str}")

    try:
        sender = get_media_sender()
        await sender.send_video(bot, target=target_str, file_path=path)
        await send_video_cmd.finish(
            f"视频已发送: {path.name} ({path.stat().st_size} bytes) -> {target_str}"
        )
    except Exception as e:
        logger.error(f"发送视频失败: {e}")
        await send_video_cmd.finish(f"发送失败: {e}")


# ── 语音命令 ──────────────────────────────────────────────

send_voice_cmd = on_command("sendvoice", priority=10)


@send_voice_cmd.handle()
async def handle_sendvoice(bot: Bot, args: Message = CommandArg()):
    """发送语音。"""
    text = str(args).strip()
    if not text:
        await send_voice_cmd.finish("用法: /sendvoice <文件路径> <QQ号|group:群号>")

    parts = text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await send_voice_cmd.finish("缺少目标参数\n用法: /sendvoice <文件路径> <QQ号|group:群号>")

    file_str = parts[0].strip().strip('"').strip("'")
    target_str = parts[1].strip()
    path = Path(file_str)

    if not path.exists():
        await send_voice_cmd.finish(f"文件不存在: {file_str}")

    try:
        sender = get_media_sender()
        await sender.send_voice(bot, target=target_str, file_path=path)
        await send_voice_cmd.finish(
            f"语音已发送: {path.name} ({path.stat().st_size} bytes) -> {target_str}"
        )
    except Exception as e:
        logger.error(f"发送语音失败: {e}")
        await send_voice_cmd.finish(f"发送失败: {e}")

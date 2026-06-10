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

from src.plugins.admin import WHITELIST

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

    # ── bytes 发送方法（供视频解析等场景直接传入下载好的数据）──

    @classmethod
    def _bytes_to_b64(cls, data: bytes) -> str:
        return f"base64://{base64.b64encode(data).decode('ascii')}"

    @staticmethod
    def _guess_type(filename: str) -> str:
        """根据扩展名推断媒体类型: image / video / voice。"""
        ext = Path(filename).suffix.lower()
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}
        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
        voice_exts = {".mp3", ".wav", ".ogg", ".aac", ".flac", ".amr", ".silk", ".opus", ".m4a"}
        if ext in image_exts:
            return "image"
        if ext in video_exts:
            return "video"
        if ext in voice_exts:
            return "voice"
        return "image"  # 兜底当作图片

    @classmethod
    async def send_bytes(
        cls,
        bot: Bot,
        target: Union[int, str],
        data: bytes,
        filename: str = "media",
    ) -> None:
        """发送媒体字节数据，自动判断类型。

        Args:
            bot: Bot 实例
            target: 私聊 QQ 号 或 "group:群号"
            data: 文件字节数据
            filename: 原始文件名（用于推断类型）
        """
        media_type = cls._guess_type(filename)
        if media_type == "image":
            await cls.send_image_bytes(bot, target, data, filename)
        elif media_type == "video":
            await cls.send_video_bytes(bot, target, data, filename)
        else:
            await cls.send_voice_bytes(bot, target, data, filename)

    @classmethod
    async def send_image_bytes(
        cls,
        bot: Bot,
        target: Union[int, str],
        data: bytes,
        filename: str = "image",
    ) -> None:
        ref = cls._bytes_to_b64(data)
        img = MessageSegment.image(ref)
        is_group, target_id = cls._parse_target(target)
        if is_group:
            await bot.send_group_msg(group_id=target_id, message=img)
        else:
            await bot.send_private_msg(user_id=target_id, message=img)
        logger.info(
            "图片(bytes)已发送 -> %s: %s (%d bytes)",
            f"群{target_id}" if is_group else f"QQ{target_id}",
            filename,
            len(data),
        )

    @classmethod
    async def send_video_bytes(
        cls,
        bot: Bot,
        target: Union[int, str],
        data: bytes,
        filename: str = "video",
    ) -> None:
        ref = cls._bytes_to_b64(data)
        vid = MessageSegment.video(ref)
        is_group, target_id = cls._parse_target(target)
        if is_group:
            await bot.send_group_msg(group_id=target_id, message=vid)
        else:
            await bot.send_private_msg(user_id=target_id, message=vid)
        logger.info(
            "视频(bytes)已发送 -> %s: %s (%d bytes)",
            f"群{target_id}" if is_group else f"QQ{target_id}",
            filename,
            len(data),
        )

    @classmethod
    async def send_voice_bytes(
        cls,
        bot: Bot,
        target: Union[int, str],
        data: bytes,
        filename: str = "voice",
    ) -> None:
        ref = cls._bytes_to_b64(data)
        voice = MessageSegment.record(ref)
        is_group, target_id = cls._parse_target(target)
        if is_group:
            await bot.send_group_msg(group_id=target_id, message=voice)
        else:
            await bot.send_private_msg(user_id=target_id, message=voice)
        logger.info(
            "语音(bytes)已发送 -> %s: %s (%d bytes)",
            f"群{target_id}" if is_group else f"QQ{target_id}",
            filename,
            len(data),
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

# 文件路径安全校验白名单（只允许这些目录及其子目录）
_ALLOWED_SEND_DIRS: list[Path] = []


def _get_allowed_send_dirs() -> list[Path]:
    """惰性初始化允许的目录列表。"""
    global _ALLOWED_SEND_DIRS
    if not _ALLOWED_SEND_DIRS:
        root = Path(__file__).resolve().parent.parent.parent.parent
        _ALLOWED_SEND_DIRS = [
            root,
            root / "downloads",
            root / "data",
        ]
    return _ALLOWED_SEND_DIRS


def _validate_file_path(file_str: str) -> tuple[bool, Path | None, str]:
    """验证文件路径安全性，拒绝路径遍历。

    Returns:
        (是否安全, 解析后路径, 错误消息)
    """
    if not file_str.strip():
        return False, None, "文件路径为空"

    # 拒绝明显的路径遍历
    if ".." in file_str.replace("\\", "/").split("/"):
        return False, None, "不允许使用 '..' 访问上级目录"

    try:
        path = Path(file_str).resolve()
    except (OSError, ValueError) as e:
        return False, None, f"路径解析失败: {e}"

    # 检查是否在允许的目录内
    for allowed in _get_allowed_send_dirs():
        try:
            path.relative_to(allowed)
            return True, path, ""
        except ValueError:
            continue

    return False, path, f"安全限制：不允许访问该路径。请将文件放到项目目录下。"


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

    safe, path, err = _validate_file_path(file_str)
    if not safe:
        logger.warning(f"拒绝不安全路径: {file_str} — {err}")
        return

    if path is None or not path.exists():
        logger.warning(f"{label}文件不存在: {file_str}")
        return

    try:
        await send_func(bot, target=target_str, file_path=path)
    except Exception as e:
        logger.error(f"发送{label}失败: {e}")


# ── 图片命令 ──────────────────────────────────────────────

send_img_cmd = on_command("sendimg", rule=WHITELIST, priority=10)


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

    safe, path, err = _validate_file_path(file_str)
    if not safe:
        await send_img_cmd.finish(f"❌ {err}")

    if path is None or not path.exists():
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

send_video_cmd = on_command("sendvideo", rule=WHITELIST, priority=10)


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

    safe, path, err = _validate_file_path(file_str)
    if not safe:
        await send_video_cmd.finish(f"❌ {err}")

    if path is None or not path.exists():
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

send_voice_cmd = on_command("sendvoice", rule=WHITELIST, priority=10)


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

    safe, path, err = _validate_file_path(file_str)
    if not safe:
        await send_voice_cmd.finish(f"❌ {err}")

    if path is None or not path.exists():
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

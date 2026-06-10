"""文件发送模块 —— 通过 OneBot 的文件上传 API 发送文件。

与媒体模块不同：
- 媒体模块：base64 编码发送图片/视频/语音（受 QQ 大小限制）
- 文件模块：通过 upload_private_file/upload_group_file 发送文件（可传大文件）

供其他插件调用：
    from src.plugins.file_sender import get_file_sender
    sender = get_file_sender()
    await sender.send_file(bot, target=3433559280, file_url="http://...", filename="video.mp4")
    await sender.send_file(bot, target="group:123456", file_url="http://...", filename="doc.pdf")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.params import CommandArg

from src.plugins.admin import WHITELIST

logger = logging.getLogger("hikari.plugins.file_sender")


# ============================================================================
# FileSender 核心类
# ============================================================================

class FileSender:
    """文件发送器。

    使用 OneBot v11 的 upload_private_file / upload_group_file API，
    file 参数支持本地路径或 http(s) URL（由 OneBot 服务端下载）。
    """

    @staticmethod
    def _parse_target(target: Union[int, str]) -> tuple[bool, int]:
        """解析目标：返回 (是否群聊, ID)。"""
        if isinstance(target, int):
            return False, target
        s = str(target).strip()
        if s.startswith("group:"):
            return True, int(s[len("group:"):])
        return False, int(s)

    @classmethod
    async def send_file(
        cls,
        bot: Bot,
        target: Union[int, str],
        file_path_or_url: str,
        filename: str = "file",
    ) -> bool:
        """发送文件。

        Args:
            bot: Bot 实例
            target: 私聊 QQ 号 或 "group:群号"
            file_path_or_url: 本地路径或 http(s) URL
            filename: 显示文件名

        Returns:
            True 成功，False 失败
        """
        is_group, target_id = cls._parse_target(target)

        try:
            if is_group:
                await bot.upload_group_file(
                    group_id=target_id,
                    file=str(file_path_or_url),
                    name=filename,
                )
            else:
                await bot.upload_private_file(
                    user_id=target_id,
                    file=str(file_path_or_url),
                    name=filename,
                )

            dest = f"群{target_id}" if is_group else f"QQ{target_id}"
            logger.info(f"文件已发送 → {dest}: {filename}")
            return True

        except Exception as e:
            dest = f"群{target_id}" if is_group else f"QQ{target_id}"
            logger.error(f"文件发送失败 → {dest}: {filename} — {e}")
            return False


# ============================================================================
# 单例
# ============================================================================

_file_sender: Optional[FileSender] = None


def get_file_sender() -> FileSender:
    global _file_sender
    if _file_sender is None:
        _file_sender = FileSender()
    return _file_sender


# ============================================================================
# 命令: /sendfile  <路径或URL>  <目标>
# ============================================================================

# 安全校验：从 media_sender 复用路径验证逻辑
def _validate_local_path(file_str: str) -> tuple[bool, Path | None, str]:
    """验证本地文件路径安全性。对于 URL 则跳过此校验。"""
    if "://" in file_str:
        return True, None, ""  # URL 由 file_sender 透传给 OneBot
    if not file_str.strip():
        return False, None, "文件路径为空"
    if ".." in file_str.replace("\\", "/").split("/"):
        return False, None, "不允许使用 '..' 访问上级目录"
    try:
        path = Path(file_str).resolve()
    except (OSError, ValueError) as e:
        return False, None, f"路径解析失败: {e}"
    # 允许项目根目录及其子目录
    root = Path(__file__).resolve().parent.parent.parent.parent
    try:
        path.relative_to(root)
        return True, path, ""
    except ValueError:
        return False, path, "安全限制：不允许访问项目目录外的路径"


send_file_cmd = on_command("sendfile", rule=WHITELIST, priority=10)


@send_file_cmd.handle()
async def handle_sendfile(bot: Bot, args: Message = CommandArg()):
    """发送文件命令。"""
    text = str(args).strip()
    if not text:
        await send_file_cmd.finish("用法: /sendfile <文件路径或URL> <QQ号|group:群号>")

    parts = text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await send_file_cmd.finish("用法: /sendfile <文件路径或URL> <QQ号|group:群号>")

    file_str = parts[0].strip().strip('"').strip("'")
    target_str = parts[1].strip()

    # 安全校验（本地路径）
    safe, _path, err = _validate_local_path(file_str)
    if not safe:
        await send_file_cmd.finish(f"❌ {err}")

    # 提取文件名
    parsed_name = Path(file_str.split("?")[0]).name or "file"
    # 如果是 URL，尝试从路径推断
    if "://" in file_str:
        from urllib.parse import urlparse
        url_name = Path(urlparse(file_str).path).name
        if url_name:
            parsed_name = url_name

    sender = get_file_sender()
    ok = await sender.send_file(bot, target=target_str, file_path_or_url=file_str, filename=parsed_name)

    if ok:
        await send_file_cmd.finish(f"文件已发送: {parsed_name} → {target_str}")
    else:
        await send_file_cmd.finish(f"文件发送失败: {parsed_name}")

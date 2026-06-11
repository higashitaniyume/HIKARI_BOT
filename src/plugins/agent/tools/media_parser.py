"""媒体解析工具 —— Cobalt API 视频/图片下载。"""

import logging
from pathlib import Path

import httpx
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, PrivateMessageEvent

from src.core.config import COBALT_API
from src.plugins.file_sender import get_file_sender

from . import MAX_DOWNLOAD_SIZE, MAX_QQ_VIDEO_SIZE, MAX_QQ_UPLOAD_SIZE

logger = logging.getLogger("hikari.plugins.agent")


async def tool_parse_media_url(bot: Bot, event: Event, url: str) -> str:
    """解析媒体 URL 并发送。"""
    if not COBALT_API:
        return "❌ 视频解析服务未配置"

    is_private = isinstance(event, PrivateMessageEvent)
    target = event.user_id if is_private else f"group:{event.group_id}"

    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            resp = await client.post(
                COBALT_API,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"url": url},
            )
            result = resp.json()
    except Exception as e:
        logger.error(f"Cobalt API 调用失败: {e}")
        return f"❌ 解析失败: {e}"

    status = result.get("status")
    if status == "error":
        return f"❌ 解析失败: [{result.get('error', {}).get('code', 'unknown')}]"

    if status in ("redirect", "tunnel"):
        return await _handle_single(bot, target, result)
    if status == "picker":
        return await _handle_picker(bot, target, result)

    return f"⚠️ 未知返回状态: {status}"


async def _handle_single(bot: Bot, target, result: dict) -> str:
    file_url = result.get("url", "")
    filename = result.get("filename", "media")
    if not file_url:
        return "❌ 未获取到下载链接"

    content_length = 0
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            head = await client.head(file_url)
            content_length = int(head.headers.get("Content-Length", 0))
    except Exception:
        pass

    if content_length > MAX_DOWNLOAD_SIZE:
        return f"📥 {filename}\n文件过大 ({content_length / (1024**2):.1f} MB)\n下载链接: {file_url}"

    ext = Path(filename).suffix.lower()
    is_video = ext in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
    qq_limit = MAX_QQ_VIDEO_SIZE if is_video else MAX_QQ_UPLOAD_SIZE

    if content_length > qq_limit or (content_length == 0 and is_video):
        ok = await get_file_sender().send_file(
            bot, target=target, file_path_or_url=file_url, filename=filename)
        return f"✅ 已下载并发送: {filename}" if ok else f"📥 {filename}\n下载链接: {file_url}"

    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
            dl = await client.get(file_url)
            dl.raise_for_status()
            data = dl.read()
    except Exception as e:
        return f"❌ 下载失败: {e}"

    from src.plugins.media_sender import get_media_sender
    try:
        await get_media_sender().send_bytes(bot, target=target, data=data, filename=filename)
        return f"✅ 已下载并发送: {filename}"
    except Exception:
        return f"📥 {filename}\n下载链接: {file_url}"


async def _handle_picker(bot: Bot, target, result: dict) -> str:
    items = result.get("picker", [])
    sent = 0
    for i, item in enumerate(items):
        item_url = item.get("url", "")
        item_type = item.get("type", "photo")
        if not item_url:
            continue
        ext = ".jpg" if item_type == "photo" else ".mp4"
        fname = f"media_{i+1}_{item_type}{ext}"
        try:
            if item_type == "video":
                ok = await get_file_sender().send_file(
                    bot, target=target, file_path_or_url=item_url, filename=fname)
                if ok:
                    sent += 1
                continue
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                dl = await client.get(item_url)
                dl.raise_for_status()
                data = dl.read()
            from src.plugins.media_sender import get_media_sender
            await get_media_sender().send_bytes(bot, target=target, data=data, filename=fname)
            sent += 1
        except Exception as e:
            logger.error(f"picker #{i+1} 失败: {e}")

    if sent == 0:
        return "❌ 所有媒体下载失败"
    if sent == len(items):
        return f"✅ 已下载并发送全部 {sent} 个媒体"
    return f"📥 {sent}/{len(items)} 个媒体已发送"

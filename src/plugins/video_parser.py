"""视频/媒体解析模块 —— 自动识别消息中的媒体 URL 并通过 Cobalt 解析。

支持服务：
    B站, YouTube, X/Twitter, TikTok, Instagram, Facebook, Vimeo, SoundCloud,
    Reddit, Tumblr, Pinterest, Twitch, Snapchat, Dailymotion, VK, OK,
    Bluesky, Streamable, Newgrounds, Rutube, Loom 等

触发方式：
    私聊或群聊中发送支持的媒体 URL，自动解析并返回下载链接。
    群聊中需要 @机器人 才会触发（避免骚扰）。

API：自建 Cobalt 实例 http://192.168.31.2:9000/
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.rule import to_me
from nonebot.params import CommandArg

from src.core.config import COBALT_API
from src.plugins.admin import WHITELIST

logger = logging.getLogger("hikari.plugins.video_parser")

# ============================================================================
# 支持的服务域名 → 显示名称
# ============================================================================

SUPPORTED_DOMAINS: dict[str, str] = {
    # 视频平台
    "bilibili.com": "B站",
    "www.bilibili.com": "B站",
    "b23.tv": "B站",
    "youtube.com": "YouTube",
    "www.youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "vimeo.com": "Vimeo",
    "dailymotion.com": "Dailymotion",
    "www.dailymotion.com": "Dailymotion",
    "streamable.com": "Streamable",
    "www.streamable.com": "Streamable",
    "rutube.ru": "Rutube",
    "loom.com": "Loom",
    "www.loom.com": "Loom",
    # 社交平台
    "x.com": "X/Twitter",
    "twitter.com": "X/Twitter",
    "tiktok.com": "TikTok",
    "www.tiktok.com": "TikTok",
    "vm.tiktok.com": "TikTok",
    "instagram.com": "Instagram",
    "www.instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "www.facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "reddit.com": "Reddit",
    "www.reddit.com": "Reddit",
    "tumblr.com": "Tumblr",
    "www.tumblr.com": "Tumblr",
    "pinterest.com": "Pinterest",
    "www.pinterest.com": "Pinterest",
    "snapchat.com": "Snapchat",
    "www.snapchat.com": "Snapchat",
    "vk.com": "VK",
    "www.vk.com": "VK",
    "ok.ru": "OK",
    "newgrounds.com": "Newgrounds",
    "www.newgrounds.com": "Newgrounds",
    "bsky.app": "Bluesky",
    # 音频
    "soundcloud.com": "SoundCloud",
    # Twitch
    "twitch.tv": "Twitch",
    "www.twitch.tv": "Twitch",
    "clips.twitch.tv": "Twitch Clips",
}


def _extract_urls(text: str) -> list[str]:
    """从文本中提取所有 URL。"""
    # 匹配 http/https URL
    pattern = r"https?://[^\s]+"
    urls = re.findall(pattern, text)
    # 去重、去尾标点
    seen = set()
    result = []
    for u in urls:
        u = u.rstrip(".,;:!?）)】』」\"'")
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _match_domain(url: str) -> Optional[tuple[str, str]]:
    """检查 URL 是否匹配支持的服务。

    Returns:
        (域名, 显示名称) 或 None
    """
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        # 去掉可能的 www. 前缀再试
        for domain, name in SUPPORTED_DOMAINS.items():
            if host == domain or host.endswith("." + domain):
                return domain, name
    except Exception:
        pass
    return None


# ============================================================================
# Cobalt API 调用
# ============================================================================

async def _call_cobalt(media_url: str) -> dict:
    """调用 Cobalt API 解析媒体 URL。

    Returns:
        API 响应 JSON（可能包含 status/url/filename/picker/error 等字段）
    """
    payload = {
        "url": media_url,
        "downloadMode": "auto",
        "alwaysProxy": True,
        "filenameStyle": "basic",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            COBALT_API,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return response.json()


# ============================================================================
# 响应构建
# ============================================================================

def _format_response(result: dict, service_name: str) -> str:
    """将 Cobalt 响应格式化为可读的 QQ 消息。"""
    status = result.get("status")

    if status in ("redirect", "tunnel"):
        file_url = result.get("url", "")
        filename = result.get("filename", "未知")
        return (
            f"📥 [{service_name}] 解析成功\n"
            f"文件名: {filename}\n"
            f"下载链接: {file_url}"
        )

    elif status == "picker":
        items = result.get("picker", [])
        if not items:
            return f"📥 [{service_name}] 解析完成，但无可下载媒体"

        lines = [f"📥 [{service_name}] 包含 {len(items)} 个媒体:"]
        for i, item in enumerate(items[:4], 1):  # 最多显示 4 个
            item_type = item.get("type", "unknown")
            item_url = item.get("url", "")
            if item_url:
                lines.append(f"  {i}. [{item_type}] {item_url}")
        if len(items) > 4:
            lines.append(f"  ... 还有 {len(items) - 4} 个")
        return "\n".join(lines)

    elif status == "error":
        error_info = result.get("error", {})
        error_code = error_info.get("code", "unknown")
        error_msg = error_info.get("message", "未知错误")
        return f"❌ [{service_name}] 解析失败: [{error_code}] {error_msg}"

    else:
        return f"⚠️ [{service_name}] 未知返回状态: {status}"


# ============================================================================
# 消息处理
# ============================================================================

# 群聊：@机器人 触发
group_parser = on_message(rule=to_me() & WHITELIST, priority=85, block=False)


@group_parser.handle()
async def handle_group_parse(bot: Bot, event: Event):
    """群聊中 @机器人 发送媒体链接时触发解析。"""
    if not isinstance(event, GroupMessageEvent):
        return

    pure_text = event.get_plaintext().strip()
    await _parse_and_reply(bot, event, pure_text)


# 私聊：任意消息触发
private_parser = on_message(rule=WHITELIST, priority=96, block=False)


@private_parser.handle()
async def handle_private_parse(bot: Bot, event: Event):
    """私聊中发送媒体链接时触发解析。"""
    if not isinstance(event, PrivateMessageEvent):
        return

    pure_text = event.get_plaintext().strip()
    await _parse_and_reply(bot, event, pure_text)


async def _parse_and_reply(bot: Bot, event: Event, text: str):
    """核心解析逻辑。"""
    if not text:
        return

    urls = _extract_urls(text)
    if not urls:
        return

    is_private = isinstance(event, PrivateMessageEvent)

    for url in urls:
        match = _match_domain(url)
        if not match:
            continue

        _, service_name = match
        logger.info(f"[视频解析] {'私聊' if is_private else f'群{event.group_id}'} "
                     f"用户{event.user_id}: {service_name} - {url[:60]}")

        try:
            result = await _call_cobalt(url)
            reply = _format_response(result, service_name)
        except httpx.HTTPStatusError as e:
            logger.error(f"Cobalt API HTTP 错误: {e.response.status_code}")
            reply = f"❌ [{service_name}] 解析服务返回错误 ({e.response.status_code})"
        except httpx.TimeoutException:
            logger.error(f"Cobalt API 超时: {url[:60]}")
            reply = f"⏱️ [{service_name}] 解析超时，请稍后重试"
        except Exception as e:
            logger.error(f"Cobalt 解析异常: {e}")
            reply = f"❌ [{service_name}] 解析失败: {e}"

        # 发送回复
        try:
            if is_private:
                await bot.send_private_msg(user_id=event.user_id, message=reply)
            else:
                at_seg = MessageSegment.at(event.user_id)
                await bot.send_group_msg(
                    group_id=event.group_id,
                    message=at_seg + MessageSegment.text("\n" + reply),
                )
        except Exception as e:
            logger.error(f"发送解析结果失败: {e}")

    return  # 让其他 handler 继续（block=False）

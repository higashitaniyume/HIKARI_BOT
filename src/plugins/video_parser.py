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
import time
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
    pattern = r"https?://[^\s]+"
    urls = re.findall(pattern, text)
    seen = set()
    result = []
    for u in urls:
        u = u.rstrip(".,;:!?）)】』」\"'")
        if u not in seen:
            seen.add(u)
            result.append(u)
    logger.debug(f"URL 提取: 原始文本{len(text)}字 → 找到{len(result)}个URL: {result}")
    return result


def _match_domain(url: str) -> Optional[tuple[str, str]]:
    """检查 URL 是否匹配支持的服务。

    Returns:
        (域名, 显示名称) 或 None
    """
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        for domain, name in SUPPORTED_DOMAINS.items():
            if host == domain or host.endswith("." + domain):
                logger.debug(f"URL 匹配成功: {url[:80]} → [{name}] (匹配域名: {domain})")
                return domain, name
    except Exception as e:
        logger.debug(f"URL 域名解析异常: {url[:80]} — {e}")
    logger.debug(f"URL 匹配失败（非支持平台）: {url[:80]}")
    return None


# ============================================================================
# Cobalt API 调用
# ============================================================================

async def _call_cobalt(media_url: str) -> dict:
    """调用 Cobalt API 解析媒体 URL。

    Returns:
        API 响应 JSON
    """
    payload = {
        "url": media_url,
        "downloadMode": "auto",
        "alwaysProxy": True,
        "filenameStyle": "basic",
    }

    logger.debug(f"Cobalt 请求 → {COBALT_API}")
    logger.debug(f"  Payload: url={media_url[:100]}, downloadMode=auto, alwaysProxy=true")

    async with httpx.AsyncClient(timeout=60.0) as client:
        start = time.monotonic()
        response = await client.post(
            COBALT_API,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        elapsed = time.monotonic() - start

        logger.debug(
            f"Cobalt 响应 → HTTP {response.status_code} "
            f"(耗时 {elapsed:.2f}s, body {len(response.content)} bytes)"
        )

        # 即使 HTTP 错误也尝试解析 body（cobalt 用 HTTP 400 返回错误详情）
        try:
            result = response.json()
        except Exception:
            logger.error(f"Cobalt 返回非 JSON: HTTP {response.status_code} — {response.text[:200]}")
            response.raise_for_status()

        status = result.get("status")
        if status == "error":
            err = result.get("error", {})
            logger.warning(
                f"Cobalt 解析失败 → 服务: {err.get('context', {}).get('service', 'unknown')}, "
                f"错误码: {err.get('code', 'unknown')}"
            )
        elif status in ("redirect", "tunnel"):
            logger.info(
                f"Cobalt 解析成功 → 状态: {status}, "
                f"文件: {result.get('filename', '?')}, "
                f"大小: {response.headers.get('Content-Length', '?')}"
            )
        elif status == "picker":
            picker = result.get("picker", [])
            logger.info(f"Cobalt 解析成功 → picker ({len(picker)} 个媒体)")

        return result


# ============================================================================
# 响应构建
# ============================================================================

def _format_response(result: dict, service_name: str) -> str:
    """将 Cobalt 响应格式化为可读的 QQ 消息。"""
    status = result.get("status")

    if status in ("redirect", "tunnel"):
        file_url = result.get("url", "")
        filename = result.get("filename", "未知")
        logger.info(
            f"[{service_name}] 格式化输出 → redirect/tunnel | "
            f"文件: {filename}, URL: {file_url[:80]}"
        )
        return (
            f"📥 [{service_name}] 解析成功\n"
            f"文件名: {filename}\n"
            f"下载链接: {file_url}"
        )

    elif status == "picker":
        items = result.get("picker", [])
        if not items:
            logger.warning(f"[{service_name}] picker 为空")
            return f"📥 [{service_name}] 解析完成，但无可下载媒体"

        logger.info(
            f"[{service_name}] 格式化输出 → picker ({len(items)} 个媒体): "
            + ", ".join(f"{i.get('type','?')}" for i in items[:5])
        )
        lines = [f"📥 [{service_name}] 包含 {len(items)} 个媒体:"]
        for i, item in enumerate(items[:4], 1):
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
        context = error_info.get("context", {})
        error_service = context.get("service", "unknown")
        logger.warning(
            f"[{service_name}] 格式化输出 → error | "
            f"错误码: {error_code}, 来源服务: {error_service}"
        )
        return f"❌ [{service_name}] 解析失败: [{error_code}] (来自 {error_service})"

    else:
        logger.warning(f"[{service_name}] 未知返回状态: {status}")
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
        logger.debug("消息文本为空，跳过解析")
        return

    urls = _extract_urls(text)
    if not urls:
        logger.debug(f"未提取到 URL，跳过解析 (文本 {len(text)} 字前50字: {text[:50]})")
        return

    is_private = isinstance(event, PrivateMessageEvent)
    location = f"私聊{event.user_id}" if is_private else f"群{event.group_id}/用户{event.user_id}"
    logger.info(f"[视频解析] 收到消息 | {location} | 共 {len(urls)} 个 URL")

    for i, url in enumerate(urls, 1):
        match = _match_domain(url)
        if not match:
            logger.debug(f"[视频解析] URL #{i}/{len(urls)} 跳过（非支持平台）: {url[:80]}")
            continue

        _, service_name = match
        logger.info(
            f"[视频解析] URL #{i}/{len(urls)} 开始解析 | "
            f"来源: {location}, 平台: {service_name}, URL: {url[:80]}"
        )

        try:
            result = await _call_cobalt(url)
            reply = _format_response(result, service_name)
        except httpx.HTTPStatusError as e:
            logger.error(
                f"[视频解析] HTTP 错误 | 平台: {service_name}, "
                f"状态码: {e.response.status_code}, "
                f"响应体: {e.response.text[:200]}"
            )
            reply = f"❌ [{service_name}] 解析服务返回错误 ({e.response.status_code})"
        except httpx.TimeoutException:
            logger.error(f"[视频解析] 超时 | 平台: {service_name}, URL: {url[:80]}")
            reply = f"⏱️ [{service_name}] 解析超时，请稍后重试"
        except httpx.ConnectError as e:
            logger.error(f"[视频解析] 连接失败 | Cobalt API ({COBALT_API}): {e}")
            reply = f"❌ [{service_name}] 无法连接解析服务，请检查 Cobalt 是否运行"
        except Exception as e:
            logger.error(f"[视频解析] 未知异常 | 平台: {service_name}: {type(e).__name__}: {e}")
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
            logger.info(f"[视频解析] 结果已发送 | 平台: {service_name}, 目标: {location}")
        except Exception as e:
            logger.error(f"[视频解析] 发送结果失败 | 平台: {service_name}: {e}")

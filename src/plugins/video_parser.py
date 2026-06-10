"""
视频/媒体解析模块 —— 自动识别消息中的媒体 URL 并通过 Cobalt 解析。

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
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.rule import to_me

from src.core.config import COBALT_API
from src.plugins.admin import WHITELIST
from src.plugins.media_sender import get_media_sender

logger = logging.getLogger("hikari.plugins.video_parser")

# 最大下载文件大小（字节），超出则只发链接不下载
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB

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

    seen: set[str] = set()
    result: list[str] = []

    for u in urls:
        u = u.rstrip(".,;:!?）)】』」\"'")
        if u not in seen:
            seen.add(u)
            result.append(u)

    logger.debug(
        f"URL 提取: 原始文本{len(text)}字 → 找到{len(result)}个URL: {result}"
    )
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
                logger.debug(
                    f"URL 匹配成功: {url[:80]} → [{name}] (匹配域名: {domain})"
                )
                return domain, name

    except Exception as e:
        logger.debug(f"URL 域名解析异常: {url[:80]} — {e}")

    logger.debug(f"URL 匹配失败（非支持平台）: {url[:80]}")
    return None


def has_media_url(text: str) -> bool:
    """检查文本中是否包含支持的媒体 URL。

    供 AI 聊天等模块调用，有媒体链接时跳过 AI 处理。
    """
    if not text:
        return False
    urls = _extract_urls(text)
    for url in urls:
        if _match_domain(url):
            return True
    return False


# ============================================================================
# Cobalt API 调用
# ============================================================================

async def _call_cobalt(media_url: str) -> dict:
    """调用 Cobalt API 解析媒体 URL。

    Returns:
        API 响应 JSON
    """

    # 重要：
    # 这里故意只发送 url，保持和你手动 curl 成功的请求一致。
    # 不默认加 alwaysProxy / downloadMode / filenameStyle，避免触发 X/Twitter 的不同解析路径。
    payload = {
        "url": media_url,
    }

    logger.debug(f"Cobalt 请求 → {COBALT_API}")
    logger.debug(f"Cobalt Payload → {payload}")

    # trust_env=False：
    # 避免机器人所在环境的 HTTP_PROXY / HTTPS_PROXY 影响访问内网 Cobalt。
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
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

        # cobalt 会用 HTTP 400 返回 JSON 错误详情，所以不要直接 raise_for_status。
        try:
            result = response.json()
        except Exception:
            logger.error(
                f"Cobalt 返回非 JSON: HTTP {response.status_code} — "
                f"{response.text[:500]}"
            )
            response.raise_for_status()
            raise

        logger.debug(f"Cobalt 原始返回 → {result}")

        status = result.get("status")

        if status == "error":
            err = result.get("error", {})
            context = err.get("context", {})
            logger.warning(
                f"Cobalt 解析失败 → 服务: {context.get('service', 'unknown')}, "
                f"错误码: {err.get('code', 'unknown')}, "
                f"完整错误: {err}"
            )

        elif status in ("redirect", "tunnel"):
            logger.info(
                f"Cobalt 解析成功 → 状态: {status}, "
                f"文件: {result.get('filename', '?')}, "
                f"URL: {str(result.get('url', ''))[:100]}"
            )

        elif status == "picker":
            picker = result.get("picker", [])
            logger.info(f"Cobalt 解析成功 → picker ({len(picker)} 个媒体)")

        else:
            logger.warning(f"Cobalt 返回未知状态 → {status}, 原始返回: {result}")

        return result


# ============================================================================
# 响应构建
# ============================================================================

async def _download_and_send(
    bot: Bot,
    target: int | str,
    result: dict,
    service_name: str,
) -> str | None:
    """下载 Cobalt 解析结果并通过媒体模块发送。

    Returns:
        发送失败时返回错误文本；成功时返回 None（无需发文字）。
    """
    status = result.get("status")

    if status in ("redirect", "tunnel"):
        file_url = result.get("url", "")
        filename = result.get("filename", "media")
        logger.info(
            f"[{service_name}] 开始下载 → {filename} ({file_url[:80]})"
        )

        try:
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                dl_resp = await client.get(file_url)
                dl_resp.raise_for_status()
                data = dl_resp.read()

            size_mb = len(data) / (1024 * 1024)
            logger.info(f"[{service_name}] 下载完成 → {filename} ({size_mb:.1f} MB)")

            if len(data) > MAX_DOWNLOAD_SIZE:
                logger.warning(f"[{service_name}] 文件过大 ({size_mb:.1f} MB)，发送链接")
                return f"📥 [{service_name}] {filename}\n文件过大 ({size_mb:.1f} MB)\n下载链接: {file_url}"

            sender = get_media_sender()
            await sender.send_bytes(bot, target=target, data=data, filename=filename)
            logger.info(f"[{service_name}] 媒体已发送 → {filename}")
            return None  # 成功，无需文本

        except httpx.HTTPStatusError as e:
            logger.error(f"[{service_name}] 下载 HTTP 错误: {e.response.status_code}")
            return f"❌ [{service_name}] 下载失败 (HTTP {e.response.status_code})"
        except httpx.TimeoutException:
            logger.error(f"[{service_name}] 下载超时")
            return f"⏱️ [{service_name}] 下载超时"
        except Exception as e:
            logger.error(f"[{service_name}] 下载异常: {type(e).__name__}: {e}")
            return f"❌ [{service_name}] 下载失败: {e}"

    if status == "picker":
        items = result.get("picker", [])
        if not items:
            logger.warning(f"[{service_name}] picker 为空")
            return f"📥 [{service_name}] 解析完成，但无可下载媒体"

        logger.info(f"[{service_name}] picker {len(items)} 个媒体，开始逐个下载")

        sent = 0
        for i, item in enumerate(items):
            item_url = item.get("url", "")
            item_type = item.get("type", "photo")
            if not item_url:
                continue

            try:
                async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                    dl_resp = await client.get(item_url)
                    dl_resp.raise_for_status()
                    data = dl_resp.read()

                if len(data) > MAX_DOWNLOAD_SIZE:
                    logger.warning(f"[{service_name}] picker #{i+1} 过大，跳过")
                    continue

                ext = ".jpg" if item_type == "photo" else ".mp4"
                filename = f"{service_name}_{i+1}_{item_type}{ext}"
                sender = get_media_sender()
                await sender.send_bytes(bot, target=target, data=data, filename=filename)
                sent += 1
                logger.info(f"[{service_name}] picker #{i+1}/{len(items)} 已发送")

            except Exception as e:
                logger.error(f"[{service_name}] picker #{i+1} 失败: {type(e).__name__}: {e}")

        if sent == 0:
            return f"❌ [{service_name}] 所有媒体下载失败"
        if sent < len(items):
            return f"📥 [{service_name}] {sent}/{len(items)} 个媒体已发送"
        return None  # 全部成功

    if status == "error":
        error_info = result.get("error", {})
        error_code = error_info.get("code", "unknown")
        context = error_info.get("context", {})
        error_service = context.get("service", "unknown")
        logger.warning(
            f"[{service_name}] 解析失败 → 错误码: {error_code}, 服务: {error_service}"
        )
        return f"❌ [{service_name}] 解析失败: [{error_code}] (来自 {error_service})"

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
    location = (
        f"私聊{event.user_id}"
        if is_private
        else f"群{event.group_id}/用户{event.user_id}"
    )

    logger.info(f"[视频解析] 收到消息 | {location} | 共 {len(urls)} 个 URL")

    for i, url in enumerate(urls, 1):
        match = _match_domain(url)

        if not match:
            logger.debug(
                f"[视频解析] URL #{i}/{len(urls)} 跳过（非支持平台）: {url[:80]}"
            )
            continue

        _, service_name = match

        logger.info(
            f"[视频解析] URL #{i}/{len(urls)} 开始解析 | "
            f"来源: {location}, 平台: {service_name}, URL: {url[:80]}"
        )

        # ── 解析 & 下载 & 发送 ────────────────────
        target = event.user_id if is_private else f"group:{event.group_id}"
        reply: str | None = None

        try:
            result = await _call_cobalt(url)
            reply = await _download_and_send(bot, target, result, service_name)

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[视频解析] HTTP 错误 | 平台: {service_name}, "
                f"状态码: {e.response.status_code}, "
                f"响应体: {e.response.text[:500]}"
            )
            reply = f"❌ [{service_name}] 解析服务返回错误 ({e.response.status_code})"

        except httpx.TimeoutException:
            logger.error(f"[视频解析] 超时 | 平台: {service_name}, URL: {url[:80]}")
            reply = f"⏱️ [{service_name}] 解析超时，请稍后重试"

        except httpx.ConnectError as e:
            logger.error(f"[视频解析] 连接失败 | Cobalt API ({COBALT_API}): {e}")
            reply = f"❌ [{service_name}] 无法连接解析服务，请检查 Cobalt 是否运行"

        except Exception as e:
            logger.exception(
                f"[视频解析] 未知异常 | 平台: {service_name}: "
                f"{type(e).__name__}: {e}"
            )
            reply = f"❌ [{service_name}] 解析失败: {e}"

        # ── 只有失败时才发文字；成功时媒体已直接发送 ────
        if reply is not None:
            try:
                if is_private:
                    await bot.send_private_msg(
                        user_id=event.user_id,
                        message=reply,
                    )
                else:
                    at_seg = MessageSegment.at(event.user_id)
                    await bot.send_group_msg(
                        group_id=event.group_id,
                        message=at_seg + MessageSegment.text("\n" + reply),
                    )
                logger.info(f"[视频解析] 错误已回复 | 平台: {service_name}")
            except Exception as e:
                logger.error(f"[视频解析] 发送结果失败 | 平台: {service_name}: {e}")
        else:
            logger.info(f"[视频解析] 媒体已发送 | 平台: {service_name}, 目标: {location}")
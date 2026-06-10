"""Agent 工具定义与实现 —— OpenAI function calling 的 function 列表 + 执行逻辑。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)

from src.core.config import COBALT_API, SEARXNG_API, SUPER_ADMIN
from src.plugins.admin import get_whitelist
from src.plugins.file_sender import get_file_sender

from .memory import get_memory

logger = logging.getLogger("hikari.plugins.agent")

# ============================================================================
# 常量
# ============================================================================

MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_QQ_UPLOAD_SIZE = 8 * 1024 * 1024    # 8 MB
MAX_QQ_VIDEO_SIZE = 15 * 1024 * 1024    # 15 MB

# ============================================================================
# OpenAI 工具定义（function calling）
# ============================================================================

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "向当前会话发送一条聊天回复。"
                "当用户只是想聊天、提问、吐槽、闲聊时使用此函数。"
                "这是最常用的函数。"
                "在群聊中，默认会自动 @发送者；可以通过 at_user 参数 @其他群成员。"
                "回复风格请遵循系统提示词中定义的角色设定。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要发送的回复内容",
                    },
                    "at_user": {
                        "type": "integer",
                        "description": (
                            "（群聊专属）要 @ 的 QQ 号。"
                            "不填则默认 @发送者。填 0 表示不 @任何人。"
                            "当用户说'帮我@小明告诉他...'时，参考群成员映射找到小明的QQ号。"
                        ),
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_media_url",
            "description": (
                "解析媒体 URL 并下载/发送媒体文件。"
                "当用户发送了 X/Twitter 链接时自动调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要解析的媒体 URL",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_whitelist",
            "description": (
                "管理机器人的白名单。"
                "支持的操作：add_user(添加用户), add_group(添加群), "
                "remove_user(移除用户), remove_group(移除群), list(查看白名单), status(查看当前状态)。"
                "只有超级管理员才能修改白名单。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "add_user", "add_group",
                            "remove_user", "remove_group",
                            "list", "status",
                        ],
                        "description": "要执行的白名单操作",
                    },
                    "target_id": {
                        "type": "integer",
                        "description": "目标 QQ 号或群号",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_memory",
            "description": "管理当前用户的 AI 对话记忆。操作：clear(清除记忆), view(查看记忆条数)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["clear", "view"],
                        "description": "记忆操作类型",
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_help",
            "description": "显示机器人的帮助信息和可用功能列表。当用户说'帮助'、'help'、'命令'、'菜单'、'功能'时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_media_or_file",
            "description": "发送图片、视频、语音或文件给指定目标。当用户要求发送某个文件或媒体时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_or_url": {
                        "type": "string",
                        "description": "本地文件路径或 URL",
                    },
                    "target": {
                        "type": "string",
                        "description": "目标：QQ号（私聊）或 'group:群号'（群聊）",
                    },
                },
                "required": ["path_or_url", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to",
            "description": (
                "主动向指定 QQ 号或群发送一条消息。"
                "当用户说'帮我告诉XXX...'、'帮我给XXX发...'、'通知一下XXX...'时使用。"
                "target 格式：QQ号（私聊）或 'group:群号'（群聊）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "目标：QQ号或 'group:群号'",
                    },
                    "text": {
                        "type": "string",
                        "description": "要发送的消息内容",
                    },
                    "at_user": {
                        "type": "integer",
                        "description": "（群聊专属）要 @ 的 QQ 号，不填则不 @",
                    },
                },
                "required": ["target", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "搜索网页获取实时信息。当用户问需要查资料、最新消息、实时数据、"
                "你不知道的事实性问题时，务必先调用此工具搜索再回答。"
                "返回 JSON 格式的搜索结果（标题、URL、摘要）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取当前时间和日期。当用户问'现在几点'、'今天几号'时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# ============================================================================
# 帮助文本
# ============================================================================

HELP_TEXT = """╔════════════════════════════╗
║   HIKARI_BOT 功能列表     ║
╚════════════════════════════╝

💬 聊天（自然语言即可）
・群内 @我 + 任意消息
・私聊任意消息
・帮我@某人 告诉他xxx

📥 媒体解析
・直接发送 X/Twitter 链接

👥 白名单管理（管理员）
・把QQxxx加入白名单
・查看白名单

🧠 记忆
・清除我的记忆
・查看记忆

📎 主动发送
・帮我把xxx发给QQxxx

⏰ 其他
・现在几点？"""

# ============================================================================
# 工具实现
# ============================================================================


async def _tool_send_message(
    bot: Bot, event: Event, text: str,
    user_id: int, group_id: Optional[int],
    at_user: int | None = None,
) -> str:
    """发送聊天回复，可指定 @ 目标。"""
    if isinstance(event, GroupMessageEvent):
        if at_user == 0:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.text(text),
            )
        elif at_user is not None and at_user > 0:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.at(at_user) + MessageSegment.text("\n" + text),
            )
        else:
            await bot.send_group_msg(
                group_id=event.group_id,
                message=MessageSegment.at(user_id) + MessageSegment.text("\n" + text),
            )
    else:
        await bot.send_private_msg(user_id=user_id, message=text)
    return "消息已发送"


async def _tool_send_to(
    bot: Bot, target: str, text: str, at_user: int | None = None,
) -> str:
    """主动向指定目标发送消息。"""
    is_group = target.startswith("group:")
    try:
        if is_group:
            group_id = int(target[len("group:"):])
            msg = MessageSegment.text(text)
            if at_user and at_user > 0:
                msg = MessageSegment.at(at_user) + MessageSegment.text("\n" + text)
            await bot.send_group_msg(group_id=group_id, message=msg)
            return f"已发送到群 {group_id}"
        else:
            qq = int(target)
            await bot.send_private_msg(user_id=qq, message=text)
            return f"已发送到 QQ {qq}"
    except Exception as e:
        return f"❌ 发送失败: {e}"


async def _tool_search_web(query: str) -> str:
    """通过 SearXNG 搜索网页。"""
    if not SEARXNG_API:
        return "❌ 搜索服务未配置"

    try:
        import urllib.parse
        url = f"{SEARXNG_API.rstrip('/')}/search?q={urllib.parse.quote(query)}&format=json"
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "⏱️ 搜索超时，请稍后再试"
    except Exception as e:
        logger.error(f"搜索失败: {e}")
        return f"❌ 搜索失败: {e}"

    results = data.get("results", [])
    if not results:
        return f"未找到关于「{query}」的搜索结果"

    # 取前 5 条，格式化输出
    lines = [f"搜索「{query}」的结果（共 {len(results)} 条，显示前 5 条）："]
    for i, r in enumerate(results[:5]):
        title = r.get("title", "无标题")
        url = r.get("url", "")
        snippet = (r.get("content") or r.get("snippet", ""))[:200]
        lines.append(f"\n{i + 1}. {title}\n   {url}\n   {snippet}")

    return "\n".join(lines)


async def _tool_get_time() -> str:
    """获取当前时间。"""
    now = datetime.now()
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    return (
        f"现在是 {now.year}年{now.month}月{now.day}日 "
        f"星期{weekday} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
    )


async def _tool_parse_media_url(bot: Bot, event: Event, url: str) -> str:
    """解析媒体 URL 并发送。"""
    if not COBALT_API:
        return "❌ 视频解析服务未配置"

    is_private = isinstance(event, PrivateMessageEvent)
    target = (
        event.user_id if is_private
        else f"group:{event.group_id}"  # type: ignore[attr-defined]
    )

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
        err = result.get("error", {})
        return f"❌ 解析失败: [{err.get('code', 'unknown')}]"

    if status in ("redirect", "tunnel"):
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
            size_mb = content_length / (1024 * 1024)
            return f"📥 {filename}\n文件过大 ({size_mb:.1f} MB)\n下载链接: {file_url}"

        ext = Path(filename).suffix.lower()
        is_video = ext in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
        qq_limit = MAX_QQ_VIDEO_SIZE if is_video else MAX_QQ_UPLOAD_SIZE

        if content_length > qq_limit or (content_length == 0 and is_video):
            f_sender = get_file_sender()
            ok = await f_sender.send_file(bot, target=target,
                                          file_path_or_url=file_url,
                                          filename=filename)
            if ok:
                return f"✅ 已下载并发送: {filename}"
            return f"📥 {filename}\n下载链接: {file_url}"

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

    if status == "picker":
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

    return f"⚠️ 未知返回状态: {status}"


async def _tool_manage_whitelist(
    event: Event, action: str, target_id: int | None,
) -> str:
    """管理白名单。"""
    wl = get_whitelist()
    user_id = event.user_id

    if action in ("add_user", "add_group", "remove_user", "remove_group"):
        if user_id != SUPER_ADMIN:
            return "❌ 只有超级管理员才能修改白名单"

    if action == "add_user":
        if target_id is None:
            return "❌ 请指定要添加的用户 QQ 号"
        await wl.add_user(target_id)
        return f"✅ 已添加用户 {target_id} 到白名单"
    elif action == "add_group":
        if target_id is None:
            return "❌ 请指定要添加的群号"
        await wl.add_group(target_id)
        return f"✅ 已添加群 {target_id} 到白名单"
    elif action == "remove_user":
        if target_id is None:
            return "❌ 请指定要移除的用户 QQ 号"
        if target_id == SUPER_ADMIN:
            return "❌ 不能移除超级管理员"
        await wl.remove_user(target_id)
        return f"✅ 已从白名单移除用户 {target_id}"
    elif action == "remove_group":
        if target_id is None:
            return "❌ 请指定要移除的群号"
        await wl.remove_group(target_id)
        return f"✅ 已从白名单移除群 {target_id}"
    elif action == "list":
        data = await wl.get_list()
        users = ", ".join(str(u) for u in data["users"])
        groups = ", ".join(str(g) for g in data["groups"])
        return (
            f"=== 白名单 ===\n"
            f"用户 ({len(data['users'])}): {users or '无'}\n"
            f"群   ({len(data['groups'])}): {groups or '无'}"
        )
    elif action == "status":
        if isinstance(event, GroupMessageEvent):
            g_ok = wl.is_group_allowed(event.group_id)
            u_ok = wl.is_user_allowed(user_id)
            return (
                f"当前群 {event.group_id}: {'✅ 已授权' if g_ok else '❌ 未授权'}\n"
                f"当前用户 {user_id}: {'✅ 已授权' if u_ok else '❌ 未授权'}"
            )
        else:
            u_ok = wl.is_user_allowed(user_id)
            return f"当前用户 {user_id}: {'✅ 已授权' if u_ok else '❌ 未授权'}"
    return f"❌ 未知操作: {action}"


async def _tool_manage_memory(
    user_id: int, group_id: Optional[int], action: str,
) -> str:
    """管理 AI 记忆。"""
    mem = get_memory()
    scope = f"群{group_id}" if group_id else "私聊"
    if action == "clear":
        await mem.clear(user_id, group_id)
        return f"✅ 已清除你在 {scope} 的 AI 记忆"
    elif action == "view":
        count = await mem.count(user_id, group_id)
        return f"📝 你在 {scope} 的 AI 记忆: {count} 条（{count // 2} 轮对话）"
    return f"❌ 未知操作: {action}"


async def _tool_show_help() -> str:
    return HELP_TEXT


async def _tool_send_media_or_file(bot: Bot, path_or_url: str, target: str) -> str:
    """发送媒体或文件。"""
    from urllib.parse import urlparse
    from src.plugins.media_sender import get_media_sender

    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        f_sender = get_file_sender()
        url_name = Path(urlparse(path_or_url).path).name or "file"
        ok = await f_sender.send_file(bot, target=target,
                                      file_path_or_url=path_or_url,
                                      filename=url_name)
        if ok:
            return f"✅ 文件已发送: {url_name} → {target}"
        return f"❌ 文件发送失败: {url_name}"

    path = Path(path_or_url)
    if not path.exists():
        return f"❌ 文件不存在: {path_or_url}"

    sender = get_media_sender()
    try:
        await sender.send_bytes(bot, target=target,
                                data=path.read_bytes(),
                                filename=path.name)
        return f"✅ 已发送: {path.name} → {target}"
    except Exception as e:
        f_sender = get_file_sender()
        ok = await f_sender.send_file(bot, target=target,
                                      file_path_or_url=str(path),
                                      filename=path.name)
        if ok:
            return f"✅ 文件已发送: {path.name} → {target}"
        return f"❌ 发送失败: {e}"


# ============================================================================
# 工具执行调度
# ============================================================================


async def execute_tool(
    bot: Bot,
    event: Event,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: int,
    group_id: Optional[int],
) -> str:
    """执行工具函数并返回结果文本。"""
    logger.info(f"执行工具: {tool_name}({arguments})")

    if tool_name == "send_message":
        return await _tool_send_message(
            bot, event, arguments.get("text", ""), user_id, group_id,
            at_user=arguments.get("at_user"),
        )
    elif tool_name == "parse_media_url":
        return await _tool_parse_media_url(bot, event, arguments.get("url", ""))
    elif tool_name == "manage_whitelist":
        return await _tool_manage_whitelist(
            event, arguments.get("action", ""), arguments.get("target_id"))
    elif tool_name == "manage_memory":
        return await _tool_manage_memory(user_id, group_id, arguments.get("action", ""))
    elif tool_name == "show_help":
        return await _tool_show_help()
    elif tool_name == "send_media_or_file":
        return await _tool_send_media_or_file(
            bot, arguments.get("path_or_url", ""), arguments.get("target", str(user_id)))
    elif tool_name == "send_to":
        return await _tool_send_to(
            bot, arguments.get("target", ""), arguments.get("text", ""),
            at_user=arguments.get("at_user"))
    elif tool_name == "search_web":
        return await _tool_search_web(arguments.get("query", ""))
    elif tool_name == "get_time":
        return await _tool_get_time()

    return f"❌ 未知工具: {tool_name}"

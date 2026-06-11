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

from src.core.config import COBALT_API, DEEPSEEK_API_KEY, SEARXNG_API, SUPER_ADMIN
from src.core.message_store import get_message_store
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
                "在群聊中，默认会自动 @发送者。"
                "重要：@某人必须用 at_user 参数(填QQ号)，禁止在 text 里写 [CQ:at,qq=xxx]。"
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
            "description": "管理 AI 对当前用户的长期记忆（偏好/习惯/事实），不是聊天记录。操作：clear(清除记忆), view(查看记忆条数)。搜索聊天记录请用 search_chat_history。",
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
            "name": "get_group_info",
            "description": (
                "获取当前群聊的成员信息。"
                "当用户问'群里有谁'、'有多少人'、'管理员是谁'、'群主是谁'时调用。"
                "仅在群聊场景有效。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_chat_history",
            "description": (
                "搜索聊天记录——查历史消息的唯一工具，不是查AI记忆。"
                "当用户说'查记录'、'之前谁说过'、'翻聊天'、'搜消息'、'历史记录'时调用。"
                "支持关键词+日期范围(如'6月1日到6月10日')。"
                "群聊只能搜本群，私聊只能搜当前私聊，不可跨上下文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词（可选，不填则返回最近消息）",
                    },
                    "count": {
                        "type": "integer",
                        "description": "返回条数上限，默认 10",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "起始日期，如 '2026-06-01' 或 '6月1日'",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，如 '2026-06-10' 或 '6月10日'",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_balance",
            "description": (
                "查询 DeepSeek API 账户余额。当用户问'还剩多少钱'、'API余额'、'账户余额'时调用。"
            ),
            "parameters": {"type": "object", "properties": {}},
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
    """发送聊天回复，可指定 @ 目标。
    注意：@某人请用 at_user 参数传入QQ号，不要在 text 里写 [CQ:at,qq=xxx]！"""
    # 安全阀：AI 如果把 [CQ:at,qq=xxx] 写进 text，自动提取并清理
    import re
    if isinstance(text, str):
        cq_at = re.search(r"\[CQ:at,qq=(\d+)\]", text)
        if cq_at:
            if at_user is None or at_user == 0:
                at_user = int(cq_at.group(1))
            text = re.sub(r"\[CQ:at,qq=\d+\]\s*", "", text).strip()
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
    *,
    event: Event | None = None,
) -> str:
    """主动向指定目标发送消息。

    安全限制：
    - 群聊中只能发到当前群
    - 私聊中只能发给自己
    - 超级管理员可发任意目标
    """
    from src.core.config import SUPER_ADMIN as _SA

    # ── 安全校验：限制目标范围 ─────────────────────
    if event is not None and event.user_id != _SA:
        if isinstance(event, GroupMessageEvent):
            allowed_target = f"group:{event.group_id}"
            if target != allowed_target:
                return (
                    f"❌ 安全限制：在当前群聊中只能发送到本群 ({allowed_target})。"
                    f"如需发送到其他目标，请联系超级管理员。"
                )
        elif isinstance(event, PrivateMessageEvent):
            allowed_target = str(event.user_id)
            if target != allowed_target:
                return (
                    f"❌ 安全限制：在私聊中只能发送给自己。"
                    f"如需发送到其他目标，请联系超级管理员。"
                )

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


async def _tool_get_group_info(bot: Bot, group_id: int) -> str:
    """获取群聊成员信息。"""
    try:
        members = await bot.get_group_member_list(group_id=group_id)
    except Exception as e:
        return f"❌ 获取群成员失败: {e}"

    if not members:
        return "未获取到群成员信息"

    total = len(members)

    # 分类：群主、管理员、普通成员
    owner_list: list[dict] = []
    admin_list: list[dict] = []
    member_list: list[dict] = []

    for m in members:
        role = m.get("role", "member")
        if role == "owner":
            owner_list.append(m)
        elif role == "admin":
            admin_list.append(m)
        else:
            member_list.append(m)

    lines = [f"群成员共 {total} 人"]

    # 群主
    for m in owner_list:
        nick = m.get("card") or m.get("nickname", "?")
        lines.append(f"\n👑 群主: {nick} (QQ {m['user_id']})")

    # 管理员
    if admin_list:
        admins = [
            f"  {m.get('card') or m.get('nickname', '?')} (QQ {m['user_id']})"
            for m in admin_list
        ]
        lines.append(f"\n🛡 管理员 ({len(admin_list)} 人):")
        lines.extend(admins)

    # 普通成员：人少就列出来，人多就统计
    if total <= 30:
        if member_list:
            members_str = [
                f"  {m.get('card') or m.get('nickname', '?')} (QQ {m['user_id']})"
                for m in member_list
            ]
            lines.append(f"\n👥 成员 ({len(member_list)} 人):")
            lines.extend(members_str)
    else:
        lines.append(f"\n👥 普通成员: {len(member_list)} 人（人数较多，仅列出管理员以上）")

    return "\n".join(lines)


def _parse_date(s: str) -> str | None:
    """解析中文日期字符串为 YYYY-MM-DD 格式。"""
    import re
    s = s.strip()
    # 2026-06-01
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # 6月1日 或 6月1
    m = re.match(r"(\d{1,2})月(\d{1,2})[日号]?", s)
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    # 2026年6月1日
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]?", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


async def _tool_search_chat_history(
    group_id: int | None, user_id: int,
    keyword: str = "", count: int = 10,
    start_date: str = "", end_date: str = "",
) -> str:
    """搜索当前会话的聊天记录（支持关键词+日期范围，不可跨上下文）。"""
    import re as _re
    store = get_message_store()
    count = max(1, min(count, 15))

    if group_id is not None:
        messages = await store.get_group_messages(group_id)
        scope = f"群 {group_id}"
    else:
        messages = await store.get_private_messages(user_id)
        scope = "私聊"

    if not messages:
        return f"当前 {scope} 暂无聊天记录"

    # 日期范围过滤
    if start_date or end_date:
        start_d = _parse_date(start_date) if start_date else None
        end_d = _parse_date(end_date) if end_date else None
        if start_d is not None or end_d is not None:
            filtered = []
            for m in messages:
                t = str(m.get("time", ""))[:10]  # YYYY-MM-DD
                if start_d and t < start_d:
                    continue
                if end_d and t > end_d:
                    continue
                filtered.append(m)
            messages = filtered

    # 关键词搜索
    kw = (keyword or "").strip().lower()
    if kw:
        matched = []
        for m in messages:
            msg_text = str(m.get("message", "")).lower()
            raw_text = str(m.get("raw_message", "")).lower()
            sender_info = m.get("sender", {})
            sender_name = (
                sender_info.get("card")
                or sender_info.get("nickname")
                or str(sender_info.get("user_id", "?"))
            ).lower()
            if kw in msg_text or kw in raw_text or kw in sender_name:
                matched.append(m)
        messages = matched

    if not messages:
        desc = f"关键词「{keyword}」" if kw else ""
        desc += f" 日期 {start_date}~{end_date}" if (start_date or end_date) else ""
        return f"在 {scope} 的记录中未找到匹配的消息{('（' + desc.strip() + '）') if desc else ''}"

    recent = messages[-count:]

    date_info = ""
    if start_date or end_date:
        date_info = f"，{start_date or '...'} ~ {end_date or '...'}"
    lines = [f"{scope} 的聊天记录" + (f"（搜索「{keyword}」{date_info}，{len(messages)} 条匹配，显示最近 {len(recent)} 条）：" if kw else f"（最近 {len(recent)} 条）：")]
    for m in recent:
        sender = m.get("sender", {})
        uid = sender.get("user_id", "?")
        nick = sender.get("card") or sender.get("nickname") or str(uid)
        t = str(m.get("time", ""))[:19]
        msg = str(m.get("message", ""))[:200]
        lines.append(f"  [{t}] {nick}(QQ{uid}): {msg}")

    return "\n".join(lines)


async def _tool_check_balance() -> str:
    """查询 DeepSeek API 账户余额。"""
    if not DEEPSEEK_API_KEY:
        return "❌ API Key 未配置"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.deepseek.com/user/balance",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "⏱️ 查询超时"
    except Exception as e:
        return f"❌ 查询失败: {e}"

    # DeepSeek balance API 返回格式:
    # {"is_available": true, "balance_infos": [{"currency": "CNY", "total_balance": "...", ...}]}
    if data.get("is_available"):
        infos = data.get("balance_infos", [])
        if infos:
            parts = []
            for info in infos:
                currency = info.get("currency", "?")
                total = info.get("total_balance", "?")
                used = info.get("topped_up_balance", "?")
                parts.append(f"{total} {currency}")
            return f"✅ DeepSeek 余额: {', '.join(parts)}"
        return "✅ API 可用，但未获取到余额明细"
    return f"⚠️ API 状态: {data}"


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


# ============================================================================
# 本地文件安全访问控制
# ============================================================================

# AI 只能读取这些目录下的文件
_ALLOWED_LOCAL_DIRS: list[Path] = []


def _get_allowed_dirs() -> list[Path]:
    """获取允许 AI 访问的本地目录列表（惰性初始化，只允许项目内的特定目录）。"""
    global _ALLOWED_LOCAL_DIRS
    if not _ALLOWED_LOCAL_DIRS:
        root = Path(__file__).resolve().parent.parent.parent.parent  # 项目根
        candidates = [
            root / "downloads",
            root / "data" / "media",
            root / "data" / "files",
        ]
        _ALLOWED_LOCAL_DIRS = [d for d in candidates if d.exists()]
        if not _ALLOWED_LOCAL_DIRS:
            (root / "downloads").mkdir(parents=True, exist_ok=True)
            _ALLOWED_LOCAL_DIRS = [root / "downloads"]
        logger.info(f"AI 文件访问白名单目录: {_ALLOWED_LOCAL_DIRS}")
    return _ALLOWED_LOCAL_DIRS


def _resolve_safe_path(user_path: str) -> Path | None:
    """安全解析用户提供的文件路径，拒绝路径遍历攻击。

    - 相对路径：相对于项目根目录解析
    - 绝对路径：直接解析
    - 拒绝含 .. 的路径
    - 拒绝不在允许目录内的路径

    Returns:
        安全的 Path 对象，不安全时返回 None
    """
    if not user_path or not user_path.strip():
        return None

    try:
        raw = Path(user_path)

        # 拒绝明显的路径遍历（即使后面会 resolve 也要提前拦截）
        if ".." in user_path.replace("\\", "/").split("/"):
            return None

        root = Path(__file__).resolve().parent.parent.parent.parent
        if raw.is_absolute():
            resolved = raw.resolve()
        else:
            resolved = (root / raw).resolve()

        # 必须在允许的目录内
        for allowed in _get_allowed_dirs():
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue
        return None
    except (OSError, ValueError):
        return None


async def _tool_send_media_or_file(
    bot: Bot, path_or_url: str, target: str,
) -> str:
    """发送媒体或文件（仅允许安全路径和 http(s) URL）。"""
    from urllib.parse import urlparse
    from src.plugins.media_sender import get_media_sender

    # ── URL 分支：只允许 http/https ──────────────────
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        f_sender = get_file_sender()
        url_name = Path(urlparse(path_or_url).path).name or "file"
        ok = await f_sender.send_file(bot, target=target,
                                      file_path_or_url=path_or_url,
                                      filename=url_name)
        if ok:
            return f"✅ 文件已发送: {url_name} → {target}"
        return f"❌ 文件发送失败: {url_name}"

    # ── 本地文件分支：安全校验 ─────────────────────
    resolved = _resolve_safe_path(path_or_url)
    if resolved is None:
        logger.warning(f"拒绝不安全路径: {path_or_url}")
        return (
            "❌ 安全限制：不允许访问该路径。"
            "请将文件放到 downloads/ 目录下再试。"
        )

    if not resolved.exists():
        return f"❌ 文件不存在: {path_or_url}"

    sender = get_media_sender()
    try:
        await sender.send_bytes(bot, target=target,
                                data=resolved.read_bytes(),
                                filename=resolved.name)
        return f"✅ 已发送: {resolved.name} → {target}"
    except Exception as e:
        f_sender = get_file_sender()
        ok = await f_sender.send_file(bot, target=target,
                                      file_path_or_url=str(resolved),
                                      filename=resolved.name)
        if ok:
            return f"✅ 文件已发送: {resolved.name} → {target}"
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
            at_user=arguments.get("at_user"), event=event)
    elif tool_name == "get_group_info":
        if group_id is None:
            return "❌ 此功能仅在群聊中可用"
        return await _tool_get_group_info(bot, group_id)
    elif tool_name == "search_chat_history":
        return await _tool_search_chat_history(
            group_id, user_id,
            keyword=arguments.get("keyword", ""),
            count=arguments.get("count", 10),
            start_date=arguments.get("start_date", ""),
            end_date=arguments.get("end_date", ""),
        )
    elif tool_name == "search_web":
        return await _tool_search_web(arguments.get("query", ""))
    elif tool_name == "check_balance":
        return await _tool_check_balance()
    elif tool_name == "get_time":
        return await _tool_get_time()

    return f"❌ 未知工具: {tool_name}"

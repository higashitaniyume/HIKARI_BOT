"""Agent 工具包 —— OpenAI function calling 定义 + 执行调度。

每个工具实现在独立模块中:
    chat.py          send_message + send_to
    media_parser.py  parse_media_url
    whitelist.py     manage_whitelist
    manage_memory.py manage_memory
    show_help.py     show_help
    send_file.py     send_media_or_file + 路径安全
    search.py        search_web + search_chat_history
    group_info.py    get_group_info
    misc.py          check_balance + get_time
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from nonebot.adapters.onebot.v11 import Bot, Event

logger = logging.getLogger("hikari.plugins.agent")

# ============================================================================
# 常量
# ============================================================================

MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024
MAX_QQ_UPLOAD_SIZE = 8 * 1024 * 1024
MAX_QQ_VIDEO_SIZE = 15 * 1024 * 1024
_SAFE_QQ = 3433559280

# ============================================================================
# 错误拦截
# ============================================================================

_ERROR_PATTERNS = [
    "Traceback", "AttributeError", "TypeError", "KeyError", "ValueError",
    "ImportError", "ModuleNotFoundError", "ConnectionError", "TimeoutError",
    "HTTPStatusError", "NoneType", "object has no attribute",
    "missing required", "Network is unreachable", "Connection refused",
    "timed out", "❌", "⏱️",
]


def _filter_error(text: str, target_user_id: int) -> str:
    if target_user_id == _SAFE_QQ or not text:
        return text
    tl = text.lower()
    for pat in _ERROR_PATTERNS:
        if pat.lower() in tl:
            logger.warning(f"拦截错误输出 → QQ{target_user_id}，原文前60字: {text[:60]}")
            return "出了点小事故喵，taffy脑子有点乱，等一下再试试~"
    return text


# ============================================================================
# 工具实现（按需导入，避免启动时加载所有模块）
# ============================================================================

from .chat import tool_send_message, tool_send_to
from .media_parser import tool_parse_media_url
from .whitelist import tool_manage_whitelist
from .manage_memory import tool_manage_memory
from .show_help import tool_show_help
from .send_file import tool_send_media_or_file
from .search import tool_search_web, tool_search_chat_history
from .group_info import tool_get_group_info
from .misc import tool_check_balance, tool_get_time
from .profile import tool_get_user_profile
from ..onebot_tools import build_onebot_tools, execute_onebot_api

# ============================================================================
# OpenAI 工具定义（function calling）
# ============================================================================

# 内置工具 + OneBot API 动态工具
_STATIC_TOOLS: list[dict[str, Any]] = [
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
                    "text": {"type": "string", "description": "要发送的回复内容"},
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
            "description": "解析媒体 URL 并下载/发送媒体文件。当用户发送了 X/Twitter 链接时自动调用。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "要解析的媒体 URL"}},
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
                        "enum": ["add_user", "add_group", "remove_user", "remove_group", "list", "status"],
                        "description": "要执行的白名单操作",
                    },
                    "target_id": {"type": "integer", "description": "目标 QQ 号或群号"},
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
                    "action": {"type": "string", "enum": ["clear", "view"], "description": "记忆操作类型"},
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
                    "path_or_url": {"type": "string", "description": "本地文件路径或 URL"},
                    "target": {"type": "string", "description": "目标：QQ号（私聊）或 'group:群号'（群聊）"},
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
                    "target": {"type": "string", "description": "目标：QQ号或 'group:群号'"},
                    "text": {"type": "string", "description": "要发送的消息内容"},
                    "at_user": {"type": "integer", "description": "（群聊专属）要 @ 的 QQ 号，不填则不 @"},
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
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_group_info",
            "description": "获取当前群聊的成员信息。当用户问'群里有谁'、'有多少人'、'管理员是谁'、'群主是谁'时调用。仅在群聊场景有效。",
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
                    "keyword": {"type": "string", "description": "搜索关键词（可选，不填则返回最近消息）"},
                    "count": {"type": "integer", "description": "返回条数上限，默认 10"},
                    "start_date": {"type": "string", "description": "起始日期，如 '2026-06-01' 或 '6月1日'"},
                    "end_date": {"type": "string", "description": "结束日期，如 '2026-06-10' 或 '6月10日'"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_balance",
            "description": "查询 DeepSeek API 账户余额。当用户问'还剩多少钱'、'API余额'、'账户余额'时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_profile",
            "description": (
                "查询 QQ 用户的名片信息（昵称、性别、年龄、群名片、角色等）。"
                "当用户问'看看XXX的名片'、'XXX的资料'、'查一下这个人'时调用。"
                "user_id 为 QQ 号，在当前群聊中可获取更详细的群名片信息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "要查询的 QQ 号"},
                },
                "required": ["user_id"],
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

# 合并内置工具 + OneBot API 动态工具（去重：静态工具优先）
_static_names = {t["function"]["name"] for t in _STATIC_TOOLS}
_onbot = [t for t in build_onebot_tools() if t["function"]["name"] not in _static_names]
TOOLS: list[dict[str, Any]] = _STATIC_TOOLS + _onbot

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
        return await tool_send_message(
            bot, event, arguments.get("text", ""), user_id, group_id,
            at_user=arguments.get("at_user"))
    elif tool_name == "parse_media_url":
        return await tool_parse_media_url(bot, event, arguments.get("url", ""))
    elif tool_name == "manage_whitelist":
        return await tool_manage_whitelist(event, arguments.get("action", ""), arguments.get("target_id"))
    elif tool_name == "manage_memory":
        return await tool_manage_memory(user_id, group_id, arguments.get("action", ""))
    elif tool_name == "show_help":
        return await tool_show_help()
    elif tool_name == "send_media_or_file":
        return await tool_send_media_or_file(
            bot, arguments.get("path_or_url", ""), arguments.get("target", str(user_id)))
    elif tool_name == "send_to":
        return await tool_send_to(
            bot, arguments.get("target", ""), arguments.get("text", ""),
            at_user=arguments.get("at_user"), event=event)
    elif tool_name == "get_group_info":
        if group_id is None:
            return "❌ 此功能仅在群聊中可用"
        return await tool_get_group_info(bot, group_id)
    elif tool_name == "search_chat_history":
        return await tool_search_chat_history(
            group_id, user_id,
            keyword=arguments.get("keyword", ""),
            count=arguments.get("count", 10),
            start_date=arguments.get("start_date", ""),
            end_date=arguments.get("end_date", ""))
    elif tool_name == "search_web":
        return await tool_search_web(arguments.get("query", ""))
    elif tool_name == "check_balance":
        return await tool_check_balance()
    elif tool_name == "get_user_profile":
        return await tool_get_user_profile(bot, arguments.get("user_id", 0), group_id=group_id)
    elif tool_name == "get_time":
        return await tool_get_time()

    # ── OneBot API 动态工具 ────────────────────────
    elif tool_name.startswith(("get_", "send_", "set_", "delete_")):
        return await execute_onebot_api(bot, tool_name, arguments)

    return f"❌ 未知工具: {tool_name}"

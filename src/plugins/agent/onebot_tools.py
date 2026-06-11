"""OneBot v11 API 动态工具 —— AI 可直接调用白名单内的 OneBot API。

安全分级：
    read    → 只读，直接放行
    write   → 安全写入，放行
    blocked → 危险操作，硬拦截

AI 看到的工具描述包含 API 的功能说明和参数，可自行判断何时调用。
"""

from __future__ import annotations

import logging
from typing import Any

from nonebot.adapters.onebot.v11 import Bot

logger = logging.getLogger("hikari.plugins.agent")

# ============================================================================
# API 白名单（名称 → {schema, level, description}）
# ============================================================================

ONEBOT_APIS: dict[str, dict] = {
    # ── 读取 ──────────────────────────────────────────
    "get_login_info": {
        "level": "read",
        "description": "获取机器人自己的 QQ 号和昵称。当用户问'你是谁'、'你QQ多少'时调用。",
        "parameters": {},
    },
    "get_version_info": {
        "level": "read",
        "description": "获取 OneBot 实现的版本信息（app名称、版本号、协议版本等）。调试用。",
        "parameters": {},
    },
    "get_status": {
        "level": "read",
        "description": "获取 OneBot 运行状态（是否在线、运行时间等）。",
        "parameters": {},
    },
    "get_msg": {
        "level": "read",
        "description": "获取单条消息的详细信息。message_id 从回复消息对象中获取。",
        "parameters": {
            "message_id": {"type": "integer", "description": "消息 ID"},
        },
    },
    "get_group_info": {
        "level": "read",
        "description": "获取群的基本信息：群名称、成员数、群公告等。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
        },
    },
    "get_group_list": {
        "level": "read",
        "description": "获取机器人加入的所有群列表。",
        "parameters": {},
    },
    "get_group_member_list": {
        "level": "read",
        "description": "获取群成员列表。返回每个成员的 user_id、昵称、群名片、角色(owner/admin/member)等信息。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
        },
    },
    "get_group_member_info": {
        "level": "read",
        "description": "获取指定群成员的详细信息。user_id必须是QQ号(数字)，昵称请先从群成员列表中查找。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "user_id": {"type": "integer", "description": "QQ 号(数字)，从群成员映射查找，不要猜"},
        },
    },
    "get_group_honor_info": {
        "level": "read",
        "description": (
            "获取群荣誉信息。type 为 'talkative'(龙王)、'performer'(群聊之火)、'legend'(群聊炽焰)、"
            "'strong_newbie'(冒尖小春笋)、'emotion'(快乐源泉) 或不填(all)。"
            "当用户问'谁是龙王'、'谁发言最多'、'最近谁最活跃'时调用。"
        ),
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "type": {"type": "string", "description": "荣誉类型，不填则返回全部"},
        },
    },
    "get_stranger_info": {
        "level": "read",
        "description": "获取陌生人/好友的QQ名片信息。user_id必须是QQ号(数字)，昵称请先查群成员映射。",
        "parameters": {
            "user_id": {"type": "integer", "description": "QQ 号(数字)，从群成员映射查找，不要猜"},
        },
    },
    "get_friend_list": {
        "level": "read",
        "description": "获取机器人的好友列表。",
        "parameters": {},
    },
    "get_group_msg_history": {
        "level": "read",
        "description": "获取群聊历史消息。count 为条数(1~100)，返回发送者、时间、消息内容。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "count": {"type": "integer", "description": "获取条数，默认 10，上限 100"},
        },
    },
    "get_friend_msg_history": {
        "level": "read",
        "description": "获取私聊历史消息。count 为条数(1~100)。",
        "parameters": {
            "user_id": {"type": "integer", "description": "QQ 号"},
            "count": {"type": "integer", "description": "获取条数，默认 10，上限 100"},
        },
    },
    "get_group_system_msg": {
        "level": "read",
        "description": "获取群系统消息（加群验证、邀请等）。当用户问'有没有人申请加群'时调用。",
        "parameters": {},
    },
    "get_essence_msg_list": {
        "level": "read",
        "description": "获取群精华消息列表。当用户问'精华消息'、'最近加了什么精'时调用。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
        },
    },
    "get_ban_list": {
        "level": "read",
        "description": "获取当前群内被禁言的成员名单。当用户问'谁被禁言了'时调用。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
        },
    },
    "get_group_at_all_remain": {
        "level": "read",
        "description": "获取群里 @全体成员 的剩余次数。当用户问'还能@全体吗'时调用。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
        },
    },
    "get_profile_like": {
        "level": "read",
        "description": "获取好友的赞过信息。当用户问'谁给我点赞了'时调用。",
        "parameters": {},
    },
    "can_send_image": {
        "level": "read",
        "description": "检查当前环境下能否发送图片。",
        "parameters": {},
    },
    "can_send_record": {
        "level": "read",
        "description": "检查当前环境下能否发送语音。",
        "parameters": {},
    },
    "get_group_file_system_info": {
        "level": "read",
        "description": "获取群文件系统信息（文件总数、已用空间等）。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
        },
    },
    "get_group_root_files": {
        "level": "read",
        "description": "获取群文件根目录列表。当用户问'群文件有什么'时调用。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
        },
    },
    "get_group_files_by_folder": {
        "level": "read",
        "description": "获取群文件子目录的文件列表。folder_id 为文件夹 ID（从 get_group_root_files 获取）。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "folder_id": {"type": "string", "description": "文件夹 ID"},
        },
    },
    "get_group_file_url": {
        "level": "read",
        "description": "获取群文件的下载链接。file_id 从文件列表中获取。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "file_id": {"type": "string", "description": "文件 ID"},
        },
    },
    "get_private_file_url": {
        "level": "read",
        "description": "获取私聊文件的下载链接。",
        "parameters": {
            "user_id": {"type": "integer", "description": "QQ 号"},
            "file_id": {"type": "string", "description": "文件 ID"},
        },
    },
    # ── 安全写入 ──────────────────────────────────────
    "send_msg": {
        "level": "write",
        "description": "发送消息（自动根据参数判断群聊/私聊）。message_type 为 'private' 或 'group'。",
        "parameters": {
            "message_type": {"type": "string", "description": "'private' 或 'group'"},
            "user_id": {"type": "integer", "description": "目标 QQ 号（私聊时）"},
            "group_id": {"type": "integer", "description": "目标群号（群聊时）"},
            "message": {"type": "string", "description": "消息内容(纯文本)"},
        },
    },
    "send_private_msg": {
        "level": "write",
        "description": "发送私聊消息。",
        "parameters": {
            "user_id": {"type": "integer", "description": "目标 QQ 号"},
            "message": {"type": "string", "description": "消息内容(纯文本)"},
        },
    },
    "send_group_msg": {
        "level": "write",
        "description": "发送群聊消息。message 为消息内容(支持纯文本)。",
        "parameters": {
            "group_id": {"type": "integer", "description": "目标群号"},
            "message": {"type": "string", "description": "消息内容(纯文本)"},
        },
    },
    "set_group_card": {
        "level": "write",
        "description": "修改群成员的群名片。user_id必须是QQ号(数字)，昵称请先从群成员列表查找。不填card则清空。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "user_id": {"type": "integer", "description": "QQ 号(数字)，从群成员映射查找，不要猜"},
            "card": {"type": "string", "description": "新的群名片，不填则清空"},
        },
    },
    "send_like": {
        "level": "write",
        "description": (
            "给好友点赞。user_id 必须是 QQ 号(数字)。"
            "如果用户说昵称(如'给小鱼点赞')，先查群成员映射或调用 get_group_member_list 找到对应的 QQ 号，"
            "不要填入自己的 QQ 号。"
        ),
        "parameters": {
            "user_id": {"type": "integer", "description": "目标 QQ 号(数字)。从群成员映射中查找，不要猜。"},
            "times": {"type": "integer", "description": "点赞次数，默认 1，上限 10"},
        },
    },
    "set_essence_msg": {
        "level": "write",
        "description": "设置群精华消息。需要管理员权限。当用户说'加精'、'设为精华'时调用。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "message_id": {"type": "integer", "description": "消息 ID（从聊天记录中获取）"},
        },
    },
    "delete_essence_msg": {
        "level": "write",
        "description": "删除群精华消息。需要管理员权限。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "message_id": {"type": "integer", "description": "消息 ID"},
        },
    },
    "friend_poke": {
        "level": "write",
        "description": "戳一戳好友。user_id 必须是 QQ 号(数字)，从群成员映射查找，不要猜。",
        "parameters": {
            "user_id": {"type": "integer", "description": "目标 QQ 号(数字)。从群成员映射中查找，不要猜。"},
        },
    },
    "group_poke": {
        "level": "write",
        "description": "戳一戳群成员。user_id 必须是 QQ 号(数字)，从群成员映射查找，不要猜。",
        "parameters": {
            "group_id": {"type": "integer", "description": "群号"},
            "user_id": {"type": "integer", "description": "目标 QQ 号(数字)。从群成员映射中查找，不要猜。"},
        },
    },
    "mark_msg_as_read": {
        "level": "write",
        "description": "标记消息为已读。仅当用户明确要求'标记已读'时调用。",
        "parameters": {},
    },
    # ── 以下被拦截 ────────────────────────────────────
    # set_group_kick, set_group_ban, set_group_whole_ban,
    # set_group_admin, set_group_leave, delete_msg, delete_friend,
    # set_group_name, set_group_avatar, set_qq_profile,
    # set_friend_add_request, set_group_add_request,
    # get_cookies, get_csrf_token, get_credentials 等危险操作不在白名单中
}

# 危险 API 拦截列表（即使 AI 请求也不执行）
_BLOCKED_APIS = {
    # 群管理（权限过大）
    "set_group_kick", "set_group_ban", "set_group_whole_ban",
    "set_group_admin", "set_group_leave", "set_group_anonymous_ban",
    # 消息删除
    "delete_msg",
    # 修改群/机器人资料
    "set_group_name", "set_group_avatar", "set_qq_profile",
    # 删除好友
    "delete_friend",
    # 处理申请
    "set_friend_add_request", "set_group_add_request",
    # 系统操作
    "set_restart", "clean_cache",
    # 凭证泄露（极高风险：含 cookies / csrf_token / 登录凭证）
    "get_cookies", "get_csrf_token", "get_credentials",
}

# ============================================================================
# 动态工具生成与执行
# ============================================================================


def build_onebot_tools() -> list[dict[str, Any]]:
    """将白名单 API 转为 OpenAI function calling 的 tools 列表。"""
    tools = []
    for name, spec in ONEBOT_APIS.items():
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": f"[OneBot API - {spec['level']}] {spec['description']}",
                "parameters": {
                    "type": "object",
                    "properties": spec["parameters"],
                    "required": list(spec["parameters"].keys()),
                },
            },
        })
    return tools


async def execute_onebot_api(
    bot: Bot,
    api_name: str,
    arguments: dict[str, Any],
) -> str:
    """执行 OneBot API 调用，带安全检查。"""
    # 危险 API 硬拦截
    if api_name in _BLOCKED_APIS:
        logger.warning(f"拦截危险 API: {api_name}")
        return f"❌ 安全限制：API '{api_name}' 不可调用（危险操作）。"

    # 未知 API
    if api_name not in ONEBOT_APIS:
        return f"❌ 未知 API: {api_name}"

    spec = ONEBOT_APIS[api_name]

    # 参数类型转换（AI 可能传字符串类型的数字）
    params = {}
    for key, param_spec in spec["parameters"].items():
        val = arguments.get(key)
        if val is None:
            if key in spec["parameters"]:
                return f"❌ 缺少必填参数: {key}"
            continue
        # 类型转换
        if param_spec["type"] == "integer" and isinstance(val, str):
            try:
                val = int(val)
            except ValueError:
                return f"❌ 参数 {key} 应为整数，收到: {val}"
        params[key] = val

    # 执行
    logger.info(f"OneBot API: {api_name}({params})")
    try:
        method = getattr(bot, api_name, None)
        if method is None:
            return f"❌ Bot 不支持此 API: {api_name}"

        result = await method(**params)

        # 格式化返回值
        if isinstance(result, dict):
            return _format_api_result(api_name, result)
        elif isinstance(result, list):
            return _format_api_list(api_name, result)
        elif isinstance(result, (int, float, bool, str)):
            return f"[{api_name}] {result}"
        else:
            return f"[{api_name}] 执行成功"

    except Exception as e:
        logger.error(f"API 执行失败: {api_name}({params}) → {e}")
        return f"❌ API 调用失败: {e}"


def _format_api_result(api_name: str, data: dict) -> str:
    """格式化单个结果。"""
    if api_name == "get_login_info":
        return f"机器人: {data.get('nickname', '?')}(QQ{data.get('user_id', '?')})"
    elif api_name == "get_version_info":
        return (f"app: {data.get('app_name','?')} v{data.get('app_version','?')}, "
                f"协议: {data.get('protocol_version','?')}")
    elif api_name == "get_stranger_info":
        nick = data.get("nickname", "?")
        sex = {"male": "男", "female": "女", "unknown": "未知"}.get(data.get("sex", ""), "?")
        return f"QQ {data.get('user_id', '?')}: {nick}, {sex}, 年龄{data.get('age', '?')}"
    elif api_name == "get_group_member_info":
        nick = data.get("card") or data.get("nickname", "?")
        role = {"owner": "群主", "admin": "管理员", "member": "成员"}.get(data.get("role", ""), "?")
        return f"{nick}(QQ{data.get('user_id','?')}), {role}"
    elif api_name == "get_group_info":
        return f"群「{data.get('group_name','?')}」({data.get('group_id','?')}), {data.get('member_count','?')}人"
    elif api_name == "get_status":
        return f"在线: {data.get('online', '?')}, 状态: {data.get('good', '?')}"
    elif api_name == "get_group_at_all_remain":
        return (f"@全体成员: {'可以' if data.get('can_at_all') else '不可以'}，"
                f"群剩余 {data.get('remain_at_all_count_for_group','?')} 次，"
                f"个人剩余 {data.get('remain_at_all_count_for_uin','?')} 次")
    elif api_name == "can_send_image":
        return "可以发送图片" if data.get("yes") else "不能发送图片"
    elif api_name == "can_send_record":
        return "可以发送语音" if data.get("yes") else "不能发送语音"
    elif api_name in ("get_group_file_url", "get_private_file_url"):
        return f"文件链接: {data.get('url', '?')}"
    elif api_name == "get_group_file_system_info":
        return (f"文件数: {data.get('file_count','?')}, "
                f"总大小: {data.get('total_size','?')} 字节, "
                f"已用: {data.get('used_space','?')}")
    else:
        return f"[{api_name}] {data}"


def _format_api_list(api_name: str, data: list) -> str:
    """格式化列表结果。"""
    if not data:
        return f"[{api_name}] 空列表"

    # 成员列表
    if api_name == "get_group_member_list":
        total = len(data)
        if total <= 20:
            items = []
            for m in data:
                nick = m.get("card") or m.get("nickname", "?")
                role = {"owner": "👑", "admin": "🛡", "member": ""}.get(m.get("role", ""), "")
                items.append(f"  {role}{nick}(QQ{m.get('user_id','?')})")
            return f"群成员共 {total} 人:\n" + "\n".join(items)
        else:
            owners = [m for m in data if m.get("role") == "owner"]
            admins = [m for m in data if m.get("role") == "admin"]
            items = []
            for m in owners + admins:
                nick = m.get("card") or m.get("nickname", "?")
                role = {"owner": "👑", "admin": "🛡"}.get(m.get("role", ""), "")
                items.append(f"  {role}{nick}(QQ{m.get('user_id','?')})")
            return f"群成员共 {total} 人（仅显示管理）:\n" + "\n".join(items)

    # 群列表
    if api_name == "get_group_list":
        items = [f"  群{m.get('group_id','?')}「{m.get('group_name','?')}」({m.get('member_count','?')}人)" for m in data]
        return f"共 {len(data)} 个群:\n" + "\n".join(items)

    # 好友列表
    if api_name == "get_friend_list":
        items = [f"  {m.get('nickname','?')}(QQ{m.get('user_id','?')})" for m in data]
        return f"共 {len(data)} 个好友:\n" + "\n".join(items)

    # 群荣誉
    if api_name == "get_group_honor_info":
        # data is list of honor groups: [{group_name, current_talkative, ...}]
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            lines = []
            for group in data:
                talkative = group.get("current_talkative", {})
                if talkative:
                    lines.append(f"  🐉 龙王: {talkative.get('nickname','?')}(QQ{talkative.get('user_id','?')})")
                performer = group.get("performer", [])
                if performer:
                    names = [f"{p.get('nickname','?')}(QQ{p.get('user_id','?')})" for p in performer[:5]]
                    lines.append(f"  🔥 群聊之火: {', '.join(names)}")
            if lines:
                return "\n".join(lines)
            return f"[{api_name}] {data}"

    # 禁言列表
    if api_name == "get_ban_list":
        items = []
        for m in data:
            uid = m.get("user_id", "?")
            duration = m.get("duration", 0)
            items.append(f"  QQ{uid}: 禁言 {duration} 秒")
        return f"禁言列表 ({len(data)} 人):\n" + "\n".join(items)

    # 群系统消息
    if api_name == "get_group_system_msg":
        items = []
        for m in data:
            if isinstance(m, dict):
                items.append(f"  [{m.get('time','?')}] {m.get('message','')[:200]}")
        return f"系统消息 ({len(data)} 条):\n" + "\n".join(items[:10])

    # 精华消息列表
    if api_name == "get_essence_msg_list":
        items = []
        for m in data:
            if isinstance(m, dict):
                sender = m.get("sender_nick", "?")
                content = str(m.get("content", ""))[:150]
                items.append(f"  {sender}: {content}")
        return f"精华消息 ({len(data)} 条):\n" + "\n".join(items[:10])

    # 群文件根目录
    if api_name == "get_group_root_files":
        folders = data.get("folders", []) if isinstance(data, dict) else []
        files = data.get("files", []) if isinstance(data, dict) else []
        lines = []
        for f in folders:
            lines.append(f"  📁 {f.get('folder_name','?')} (ID: {f.get('folder_id','?')})")
        for f in files:
            lines.append(f"  📄 {f.get('file_name','?')} ({f.get('file_size','?')} bytes)")
        return f"群文件根目录:\n" + "\n".join(lines[:20]) if lines else "群文件根目录为空"

    # 群文件子目录
    if api_name == "get_group_files_by_folder":
        folders = data.get("folders", []) if isinstance(data, dict) else []
        files = data.get("files", []) if isinstance(data, dict) else []
        lines = []
        for f in folders:
            lines.append(f"  📁 {f.get('folder_name','?')} (ID: {f.get('folder_id','?')})")
        for f in files:
            lines.append(f"  📄 {f.get('file_name','?')} ({f.get('file_size','?')} bytes)")
        return f"文件夹内容:\n" + "\n".join(lines[:20]) if lines else "文件夹为空"

    # 消息历史（群 + 私聊）
    if api_name in ("get_group_msg_history", "get_friend_msg_history"):
        items = []
        for m in data:
            if hasattr(m, "sender"):
                nick = getattr(m.sender, "nickname", "") or getattr(m.sender, "card", "") or "?"
                items.append(f"  [{getattr(m, 'time', '?')}] {nick}: {str(getattr(m, 'message', ''))[:120]}")
            elif isinstance(m, dict):
                sender = m.get("sender", {})
                nick = sender.get("card") or sender.get("nickname", "?")
                items.append(f"  [{m.get('time','?')}] {nick}: {str(m.get('message',''))[:120]}")
        return f"最近 {len(items)} 条消息:\n" + "\n".join(items)

    # 赞过信息
    if api_name == "get_profile_like":
        items = [f"  {m.get('nickname','?')}(QQ{m.get('user_id','?')})" for m in data if isinstance(m, dict)]
        return f"赞过你的人 ({len(items)} 人):\n" + "\n".join(items[:15]) if items else "无人点赞"

    # 兜底：截断显示
    preview = "\n".join(str(m)[:200] for m in data[:10])
    if len(data) > 10:
        preview += f"\n... 还有 {len(data) - 10} 条"
    return f"[{api_name}] 共 {len(data)} 条:\n{preview}"

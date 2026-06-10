"""管理员鉴权模块 —— 白名单控制 + 超级管理员。

特性：
- 超级管理员 3433559280 硬编码，永远通过鉴权
- 白名单 JSON 文件热读写：修改文件后立即生效，无需重启
- 提供 WHITELIST Rule 和 SUPER_ADMIN Rule 供其他插件使用

命令（仅超级管理员）：
    /wl add user <qq>      — 添加好友白名单
    /wl add group <群号>   — 添加群白名单
    /wl remove user <qq>   — 移除好友白名单
    /wl remove group <群号> — 移除群白名单
    /wl list               — 查看白名单
    /wl status             — 查看当前会话是否在白名单
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from nonebot import on_command, get_bot
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.params import CommandArg
from nonebot.rule import Rule

from src.core.config import SUPER_ADMIN, WHITELIST_FILE

logger = logging.getLogger("hikari.plugins.admin")

# ============================================================================
# WhitelistManager —— 热读写 JSON 白名单
# ============================================================================

_DEFAULT_WHITELIST = {
    "users": [SUPER_ADMIN],
    "groups": [],
}


class WhitelistManager:
    """白名单管理器（单例 + 文件热读写）。

    每次鉴权前检查 JSON 文件的 mtime，若发生变化则自动重新加载。
    """

    _instance: Optional["WhitelistManager"] = None

    def __new__(cls) -> "WhitelistManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._file_path = Path(WHITELIST_FILE)
        self._lock = asyncio.Lock()
        self._last_mtime: float = 0.0
        self._data: dict = {}
        self._ensure_file()
        self._load()
        self._initialized = True
        logger.info(f"白名单管理器已就绪 → {self._file_path}")

    # ── 文件操作 ──────────────────────────────────────────

    def _ensure_file(self) -> None:
        """创建默认白名单文件（如不存在）。"""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._write_json(_DEFAULT_WHITELIST)

    def _read_json(self) -> dict:
        """读取 JSON 文件。"""
        try:
            return json.loads(self._file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"白名单 JSON 解析失败: {e}，使用默认配置")
            return _DEFAULT_WHITELIST

    def _write_json(self, data: dict) -> None:
        """写入 JSON 文件。"""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 热加载 ────────────────────────────────────────────

    def _check_reload(self) -> None:
        """检查文件 mtime，若变更则重新加载。"""
        try:
            mtime = os.path.getmtime(self._file_path)
        except OSError:
            return
        if mtime != self._last_mtime:
            self._load()
            logger.debug("白名单文件已变更，自动重载")

    def _load(self) -> None:
        """从文件加载白名单。"""
        self._data = self._read_json()
        self._data.setdefault("users", [])
        self._data.setdefault("groups", [])
        # 确保超级管理员始终在列表中
        if SUPER_ADMIN not in self._data["users"]:
            self._data["users"].append(SUPER_ADMIN)
        try:
            self._last_mtime = os.path.getmtime(self._file_path)
        except OSError:
            pass

    # ── 鉴权 ──────────────────────────────────────────────

    def is_user_allowed(self, user_id: int) -> bool:
        """检查用户是否在白名单中。超级管理员永远通过。"""
        if user_id == SUPER_ADMIN:
            return True
        self._check_reload()
        return user_id in self._data.get("users", [])

    def is_group_allowed(self, group_id: int) -> bool:
        """检查群是否在白名单中。"""
        self._check_reload()
        return group_id in self._data.get("groups", [])

    def is_allowed(self, event: Event) -> bool:
        """根据事件判断是否有权限。"""
        user_id = event.user_id
        if user_id == SUPER_ADMIN:
            return True
        self._check_reload()
        if isinstance(event, GroupMessageEvent):
            return (
                event.group_id in self._data.get("groups", [])
                or user_id in self._data.get("users", [])
            )
        else:
            return user_id in self._data.get("users", [])

    # ── 管理操作（需持有锁，调用方负责加锁）───────────────

    async def add_user(self, user_id: int) -> None:
        self._check_reload()
        if user_id not in self._data["users"]:
            self._data["users"].append(user_id)
            self._write_json(self._data)
            self._load()

    async def remove_user(self, user_id: int) -> None:
        if user_id == SUPER_ADMIN:
            return  # 不能移除超级管理员
        self._check_reload()
        if user_id in self._data["users"]:
            self._data["users"].remove(user_id)
            self._write_json(self._data)
            self._load()

    async def add_group(self, group_id: int) -> None:
        self._check_reload()
        if group_id not in self._data["groups"]:
            self._data["groups"].append(group_id)
            self._write_json(self._data)
            self._load()

    async def remove_group(self, group_id: int) -> None:
        self._check_reload()
        if group_id in self._data["groups"]:
            self._data["groups"].remove(group_id)
            self._write_json(self._data)
            self._load()

    async def get_list(self) -> dict:
        self._check_reload()
        return {
            "users": list(self._data.get("users", [])),
            "groups": list(self._data.get("groups", [])),
        }


# ============================================================================
# 全局单例
# ============================================================================

_whitelist: Optional[WhitelistManager] = None


def get_whitelist() -> WhitelistManager:
    global _whitelist
    if _whitelist is None:
        _whitelist = WhitelistManager()
    return _whitelist


# ============================================================================
# NoneBot2 Rule —— 供其他插件使用
# ============================================================================

async def _whitelist_check(event: Event) -> bool:
    return get_whitelist().is_allowed(event)


async def _super_admin_check(event: Event) -> bool:
    return event.user_id == SUPER_ADMIN


WHITELIST = Rule(_whitelist_check)
"""白名单 Rule：通过白名单鉴权或超级管理员即为 True。"""

SUPER_ADMIN_RULE = Rule(_super_admin_check)
"""超级管理员 Rule：仅 3433559280 可通过。"""


# ============================================================================
# 管理员命令
# ============================================================================

wl_cmd = on_command("wl", rule=SUPER_ADMIN_RULE, priority=5)


@wl_cmd.handle()
async def handle_wl(bot: Bot, event: Event, args: Message = CommandArg()):
    """白名单管理命令入口。"""
    text = str(args).strip()
    if not text:
        await wl_cmd.finish(
            "白名单管理命令：\n"
            "/wl add user <qq> — 添加用户\n"
            "/wl add group <群号> — 添加群\n"
            "/wl remove user <qq> — 移除用户\n"
            "/wl remove group <群号> — 移除群\n"
            "/wl list — 查看白名单\n"
            "/wl status — 查看当前会话状态"
        )

    wl = get_whitelist()
    parts = text.split(maxsplit=2)

    # ── list ──────────────────────────────────────────
    if parts[0] == "list":
        data = await wl.get_list()
        users = ", ".join(str(u) for u in data["users"])
        groups = ", ".join(str(g) for g in data["groups"])
        await wl_cmd.finish(
            f"=== 白名单 ===\n"
            f"用户 ({len(data['users'])}): {users or '无'}\n"
            f"群   ({len(data['groups'])}): {groups or '无'}"
        )

    # ── status ────────────────────────────────────────
    if parts[0] == "status":
        if isinstance(event, GroupMessageEvent):
            g_ok = wl.is_group_allowed(event.group_id)
            u_ok = wl.is_user_allowed(event.user_id)
            await wl_cmd.finish(
                f"当前群 {event.group_id}: {'✅ 已授权' if g_ok else '❌ 未授权'}\n"
                f"当前用户 {event.user_id}: {'✅ 已授权' if u_ok else '❌ 未授权'}"
            )
        else:
            u_ok = wl.is_user_allowed(event.user_id)
            await wl_cmd.finish(
                f"当前用户 {event.user_id}: {'✅ 已授权' if u_ok else '❌ 未授权'}"
            )

    # ── add / remove ──────────────────────────────────
    if len(parts) < 3:
        await wl_cmd.finish("用法: /wl <add|remove> <user|group> <ID>")

    action, target_type, target_id_str = parts

    try:
        target_id = int(target_id_str)
    except ValueError:
        await wl_cmd.finish(f"无效的 ID: {target_id_str}")

    if action == "add":
        if target_type == "user":
            await wl.add_user(target_id)
            await wl_cmd.finish(f"✅ 已添加用户 {target_id} 到白名单")
        elif target_type == "group":
            await wl.add_group(target_id)
            await wl_cmd.finish(f"✅ 已添加群 {target_id} 到白名单")
        else:
            await wl_cmd.finish(f"未知类型: {target_type}，请用 user 或 group")

    elif action == "remove":
        if target_type == "user":
            if target_id == SUPER_ADMIN:
                await wl_cmd.finish("❌ 不能移除超级管理员")
            await wl.remove_user(target_id)
            await wl_cmd.finish(f"✅ 已从白名单移除用户 {target_id}")
        elif target_type == "group":
            await wl.remove_group(target_id)
            await wl_cmd.finish(f"✅ 已从白名单移除群 {target_id}")
        else:
            await wl_cmd.finish(f"未知类型: {target_type}，请用 user 或 group")

    else:
        await wl_cmd.finish(f"未知操作: {action}，请用 add 或 remove")

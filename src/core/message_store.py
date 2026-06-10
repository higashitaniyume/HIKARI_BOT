"""消息存储 —— 将收到的消息以 JSON 格式持久化。

每个 QQ 号/群号对应一个 JSON 文件，消息以数组形式追加。

目录结构：
    data/messages/
    ├── private/
    │   ├── 123456789.json
    │   └── 987654321.json
    └── group/
        ├── 10001.json
        └── 20002.json

使用方式：
    from src.core.message_store import get_message_store
    store = get_message_store()
    await store.save_private_msg(user_id=123456, record={...})
    await store.save_group_msg(group_id=789012, record={...})
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("hikari.core.message_store")


class MessageStore:
    """消息持久化存储。

    单例模式，创建后通过 get_message_store() 获取。
    """

    _instance: Optional["MessageStore"] = None
    _lock: asyncio.Lock

    def __new__(cls, base_dir: str = "data/messages") -> "MessageStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, base_dir: str = "data/messages") -> None:
        if self._initialized:
            return
        self._base_dir = Path(base_dir)
        self._private_dir = self._base_dir / "private"
        self._group_dir = self._base_dir / "group"
        self._lock = asyncio.Lock()
        self._ensure_dirs()
        self._initialized = True
        logger.info(f"消息存储已就绪 → {self._base_dir.resolve()}")

    # ─── 内部工具 ───────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        """创建必要的目录。"""
        self._private_dir.mkdir(parents=True, exist_ok=True)
        self._group_dir.mkdir(parents=True, exist_ok=True)

    async def _append_json(self, file_path: Path, record: dict[str, Any]) -> None:
        """向 JSON 文件追加一条记录。"""
        async with self._lock:
            # 读取现有数据
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    data = json.loads(content)
                    if not isinstance(data, list):
                        data = []
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"JSON 解析失败，重建文件: {file_path}")
                    data = []
            else:
                data = []

            # 追加并写回
            data.append(record)
            file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # ─── 公共接口 ───────────────────────────────────────────────

    async def save_private_msg(self, user_id: int, record: dict[str, Any]) -> None:
        """保存一条私聊消息。

        Args:
            user_id: 发送者 QQ 号
            record: 消息记录字典，需包含 time/timestamp/sender/message 等字段
        """
        file_path = self._private_dir / f"{user_id}.json"
        await self._append_json(file_path, record)
        logger.debug(f"私聊消息已存储 → user={user_id}")

    async def save_group_msg(self, group_id: int, record: dict[str, Any]) -> None:
        """保存一条群消息。

        Args:
            group_id: 群号
            record: 消息记录字典，需包含 time/timestamp/sender/group_id/message 等字段
        """
        file_path = self._group_dir / f"{group_id}.json"
        await self._append_json(file_path, record)
        logger.debug(f"群消息已存储 → group={group_id}")

    # ─── 查询接口 ───────────────────────────────────────────────

    async def get_private_messages(self, user_id: int) -> list[dict[str, Any]]:
        """获取某个用户的私聊消息记录。"""
        file_path = self._private_dir / f"{user_id}.json"
        if not file_path.exists():
            return []
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return []

    async def get_group_messages(self, group_id: int) -> list[dict[str, Any]]:
        """获取某个群的消息记录。"""
        file_path = self._group_dir / f"{group_id}.json"
        if not file_path.exists():
            return []
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return []


def get_message_store(base_dir: str = "data/messages") -> MessageStore:
    """获取全局唯一的 MessageStore 实例。"""
    return MessageStore(base_dir)

"""消息存储 —— 将收到的消息以 JSON 格式持久化。

每个 QQ 号/群号对应一个 JSON 文件，消息以数组形式追加。
使用分片锁减少不同文件之间的竞争，支持轮转防无限增长。

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

# 单个消息文件的最大保留条数（超过则轮转）
_MAX_MESSAGES_PER_FILE = 5000


class MessageStore:
    """消息持久化存储。

    使用 per-file 锁而非全局锁，避免不同用户/群的写入互相阻塞。
    消息数超过阈值时自动轮转归档旧文件。
    """

    _instance: Optional["MessageStore"] = None

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
        self._locks: dict[str, asyncio.Lock] = {}
        self._ensure_dirs()
        self._initialized = True
        logger.info(f"消息存储已就绪 → {self._base_dir.resolve()}")

    # ─── 内部工具 ───────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        """创建必要的目录。"""
        self._private_dir.mkdir(parents=True, exist_ok=True)
        self._group_dir.mkdir(parents=True, exist_ok=True)

    def _get_lock(self, file_key: str) -> asyncio.Lock:
        """获取 per-file 锁（按需创建）。"""
        if file_key not in self._locks:
            self._locks[file_key] = asyncio.Lock()
        return self._locks[file_key]

    async def _append_json(self, file_path: Path, record: dict[str, Any]) -> None:
        """向 JSON 文件追加一条记录（per-file 锁，非全局锁）。"""
        file_key = str(file_path)
        lock = self._get_lock(file_key)
        async with lock:
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

            # ── 轮转：超过阈值时归档旧文件 ──────────────
            if len(data) > _MAX_MESSAGES_PER_FILE:
                self._rotate(file_path, data)
                # 轮转后只保留最新的一半
                keep = _MAX_MESSAGES_PER_FILE // 2
                data = data[-keep:]

            file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    @staticmethod
    def _rotate(file_path: Path, data: list) -> None:
        """将当前文件归档为带时间戳的备份文件。"""
        try:
            import time
            stamp = int(time.time())
            backup = file_path.with_suffix(f".{stamp}.json")
            backup.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"消息文件已轮转: {file_path.name} → {backup.name}")
        except Exception as e:
            logger.warning(f"消息文件轮转失败: {e}")

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
        lock = self._get_lock(str(file_path))
        async with lock:
            try:
                return json.loads(file_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                return []

    async def get_group_messages(self, group_id: int) -> list[dict[str, Any]]:
        """获取某个群的消息记录。"""
        file_path = self._group_dir / f"{group_id}.json"
        if not file_path.exists():
            return []
        lock = self._get_lock(str(file_path))
        async with lock:
            try:
                return json.loads(file_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                return []

    # ─── 清理 ───────────────────────────────────────────────────

    async def cleanup_old_locks(self) -> None:
        """清理长时间未使用的锁（可在后台定期调用）。"""
        # 简单策略：清理所有空闲锁（下次访问时会自动重建）
        # 这里只做保守清理：移除所有未持有的锁
        to_remove = []
        for key, lock in self._locks.items():
            if not lock.locked():
                to_remove.append(key)
        for key in to_remove:
            del self._locks[key]
        if to_remove:
            logger.debug(f"清理了 {len(to_remove)} 个空闲消息存储锁")


def get_message_store(base_dir: str = "data/messages") -> MessageStore:
    """获取全局唯一的 MessageStore 实例。"""
    return MessageStore(base_dir)

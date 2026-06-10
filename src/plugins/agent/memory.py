"""Per-user AI 对话记忆管理器。

与 ai_chat.py 的 MemoryManager 功能相同，独立实现以避免循环导入。
记忆文件兼容，可在 agent 和 ai_chat 之间无缝切换。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

from src.core.config import (
    AI_MEMORY_DIR,
    MAX_MEMORY_MESSAGES,
    get_system_prompt,
)
from .client import estimate_tokens, estimate_total_tokens

logger = logging.getLogger("hikari.plugins.agent")
_MEMORY_CACHE_TTL = 60.0


class MemoryManager:
    """Per-user AI 对话记忆管理器。"""

    def __init__(self, base_dir: str = AI_MEMORY_DIR):
        self._base = Path(base_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    def _cache_key(self, user_id: int, group_id: Optional[int]) -> str:
        return f"{group_id or 'private'}:{user_id}"

    def _get_lock(self, user_id: int, group_id: Optional[int]) -> asyncio.Lock:
        key = self._cache_key(user_id, group_id)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _file_path(self, user_id: int, group_id: Optional[int]) -> Path:
        if group_id is not None:
            return self._base / "group" / str(group_id) / f"{user_id}.json"
        return self._base / "private" / f"{user_id}.json"

    def _load(self, file_path: Path, cache_key: str) -> list[dict]:
        if cache_key in self._cache:
            ts, mem = self._cache[cache_key]
            if time.monotonic() - ts < _MEMORY_CACHE_TTL:
                return mem
        if not file_path.exists():
            return []
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._cache[cache_key] = (time.monotonic(), data)
                return data
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"记忆 JSON 损坏: {file_path} — {e}")
        return []

    def _trim_by_tokens(self, memory: list[dict]) -> list[dict]:
        max_tokens = max(4000, MAX_MEMORY_MESSAGES * 120)
        system_tokens = estimate_tokens(get_system_prompt()) + 4
        budget = max(500, max_tokens - system_tokens)
        # 单条截断：超过 2000 字符的消息截断
        trimmed: list[dict] = []
        for msg in memory:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 2000:
                msg = {**msg, "content": content[:2000] + "…（已截断）"}
            trimmed.append(msg)
        memory = trimmed
        while memory:
            if estimate_total_tokens(memory) <= budget:
                break
            memory = memory[2:]
        return memory

    def _save(self, file_path: Path, memory: list[dict], cache_key: str) -> None:
        memory = self._trim_by_tokens(memory)
        self._cache[cache_key] = (time.monotonic(), memory)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(memory, ensure_ascii=False),
            encoding="utf-8",
        )

    async def get_memory(
        self, user_id: int, group_id: Optional[int] = None
    ) -> list[dict]:
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            return self._load(self._file_path(user_id, group_id), cache_key)

    async def append(
        self, user_id: int, user_msg: str, assistant_msg: str,
        group_id: Optional[int] = None,
    ) -> None:
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            path = self._file_path(user_id, group_id)
            memory = self._load(path, cache_key)
            memory.append({"role": "user", "content": user_msg})
            memory.append({"role": "assistant", "content": assistant_msg})
            self._save(path, memory, cache_key)

    async def clear(self, user_id: int, group_id: Optional[int] = None) -> None:
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            self._cache.pop(cache_key, None)
            path = self._file_path(user_id, group_id)
            if path.exists():
                path.unlink()
                logger.info(f"记忆已清除: {path}")

    async def count(self, user_id: int, group_id: Optional[int] = None) -> int:
        mem = await self.get_memory(user_id, group_id)
        return len(mem)


# 全局单例
_memory_manager: Optional[MemoryManager] = None


def get_memory() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager

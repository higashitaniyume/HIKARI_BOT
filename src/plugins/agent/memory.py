"""Per-user AI 对话记忆管理器。

两段式记忆：
    热记忆（hot）: JSON 格式的最近对话 + 历史摘要，Token 预算裁剪
    冷记忆（cold）: memory.md，长期存储用户偏好/话题/事实

触发归档：热记忆产生 [历史摘要] 时自动写入冷记忆。
冷记忆过大时自动 AI 压缩。

目录结构：
    data/ai_memory/
    ├── private/{uid}.json       # 热记忆
    ├── private/{uid}_memory.md  # 冷记忆
    ├── group/{gid}/{uid}.json
    └── group/{gid}/{uid}_memory.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.core.config import (
    AI_MEMORY_DIR,
    DEEPSEEK_MODEL,
    MAX_MEMORY_MESSAGES,
    get_system_prompt,
)
from .client import estimate_tokens, estimate_total_tokens

logger = logging.getLogger("hikari.plugins.agent")
_MEMORY_CACHE_TTL = 60.0

# 压缩触发阈值
_SUMMARIZE_THRESHOLD = 0.75
# 每次压缩的最少消息条数
_MIN_SUMMARIZE_PAIRS = 3
# 冷记忆最大字符数（超过则 AI 压缩）
_MAX_COLD_MEMORY_CHARS = 3000


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

    async def _summarize(self, messages: list[dict]) -> str:
        """调用 AI 将一组对话消息压缩成简短摘要。"""
        from .client import get_client

        # 过滤掉已有的摘要消息，只压缩真实对话
        chat_only = [m for m in messages if m.get("role") in ("user", "assistant")]
        if len(chat_only) < 2:
            return ""

        # 构建对话文本
        lines: list[str] = []
        for m in chat_only:
            role = "用户" if m["role"] == "user" else "AI"
            content = str(m.get("content", ""))[:500]
            lines.append(f"[{role}]: {content}")

        prompt = (
            "请将以下对话历史压缩成一段简洁的摘要（200字以内），"
            "保留关键话题、决定、用户偏好和重要上下文：\n\n"
            + "\n".join(lines)
        )

        try:
            client = get_client()
            resp = await client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个对话摘要助手，用中文输出。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=256,
                temperature=0.3,
            )
            summary = resp.choices[0].message.content or ""
            if summary:
                logger.debug(f"对话压缩完成: {len(chat_only)} 条 → {len(summary)} 字摘要")
            return summary.strip()
        except Exception as e:
            logger.warning(f"对话压缩失败: {e}")
            return ""

    async def _trim_and_summarize(self, memory: list[dict]) -> list[dict]:
        """智能裁剪：超预算时先压缩旧对话，再不行才丢弃。"""
        max_tokens = max(4000, MAX_MEMORY_MESSAGES * 120)
        system_tokens = estimate_tokens(get_system_prompt()) + 4
        budget = max(500, max_tokens - system_tokens)

        # 单条截断
        trimmed: list[dict] = []
        for msg in memory:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 2000:
                msg = {**msg, "content": content[:2000] + "…（已截断）"}
            trimmed.append(msg)
        memory = trimmed

        current = estimate_total_tokens(memory)
        if current <= budget:
            return memory

        # ── 压缩阶段：对旧消息进行 AI 摘要 ──────────────
        # 找到分割点：保留最近 ~60% 的 token 预算，压缩前 ~40%
        keep_budget = int(budget * 0.6)
        split_idx = 0
        accum = 0
        for i, msg in enumerate(memory):
            accum += estimate_tokens(msg.get("content", "")) + 4
            if accum > keep_budget:
                split_idx = i
                break

        # 对齐到 (user, assistant) 对边界
        while split_idx < len(memory) and memory[split_idx].get("role") != "user":
            split_idx += 1

        old_part = memory[:split_idx]
        recent_part = memory[split_idx:]

        # 旧消息中有足够对话才值得压缩
        old_chat = [m for m in old_part if m.get("role") in ("user", "assistant")]
        if len(old_chat) >= _MIN_SUMMARIZE_PAIRS * 2:
            summary = await self._summarize(old_part)
            if summary:
                # 检查是否有更早的摘要，合并之
                old_summaries = [
                    m.get("content", "")
                    for m in memory
                    if m.get("role") == "system" and m.get("content", "").startswith("[历史摘要]")
                ]
                if old_summaries:
                    # 合并旧摘要到新摘要前面
                    merged = "; ".join(old_summaries) + "\n" + summary
                    # 去掉旧摘要
                    recent_part = [m for m in recent_part if not (
                        m.get("role") == "system"
                        and str(m.get("content", "")).startswith("[历史摘要]")
                    )]
                    summary = merged

                result = [{"role": "system", "content": f"[历史摘要] {summary}"}] + recent_part
                logger.info(
                    f"对话压缩: {len(memory)} 条 → {len(result)} 条 "
                    f"(压缩了 {len(old_part)} 条旧消息)"
                )
                memory = result

        # ── 丢弃阶段：压缩后还超预算则丢弃最旧对 ─────────
        while memory:
            if estimate_total_tokens(memory) <= budget:
                break
            memory = memory[2:]

        return memory

    async def _save(
        self, file_path: Path, memory: list[dict], cache_key: str,
        user_id: int, group_id: Optional[int],
    ) -> None:
        memory = await self._trim_and_summarize(memory)

        # ── 冷记忆归档 ──────────────────────────────
        # 提取热记忆中的 [历史摘要]，归档到用户冷记忆 + 群共享记忆
        summaries = [
            str(m.get("content", ""))
            for m in memory
            if m.get("role") == "system"
            and str(m.get("content", "")).startswith("[历史摘要]")
        ]
        for s in summaries:
            clean = s.removeprefix("[历史摘要]").strip()
            if clean:
                await self._archive_to_md(user_id, group_id, clean)
                if group_id is not None:
                    await self._archive_to_group_memory(group_id, clean)

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
            await self._save(path, memory, cache_key, user_id, group_id)

    async def clear(self, user_id: int, group_id: Optional[int] = None) -> None:
        cache_key = self._cache_key(user_id, group_id)
        lock = self._get_lock(user_id, group_id)
        async with lock:
            self._cache.pop(cache_key, None)
            # 清除热记忆
            path = self._file_path(user_id, group_id)
            if path.exists():
                path.unlink()
                logger.info(f"热记忆已清除: {path}")
            # 清除冷记忆
            md_path = self._memory_md_path(user_id, group_id)
            if md_path.exists():
                md_path.unlink()
                logger.info(f"冷记忆已清除: {md_path}")

    async def count(self, user_id: int, group_id: Optional[int] = None) -> int:
        mem = await self.get_memory(user_id, group_id)
        return len(mem)

    # ── 冷记忆（memory.md）────────────────────────────

    def _memory_md_path(self, user_id: int, group_id: Optional[int]) -> Path:
        if group_id is not None:
            return self._base / "group" / str(group_id) / f"{user_id}_memory.md"
        return self._base / "private" / f"{user_id}_memory.md"

    async def get_long_term_memory(
        self, user_id: int, group_id: Optional[int] = None,
    ) -> str:
        """获取冷记忆内容（用于注入 system prompt）。"""
        path = self._memory_md_path(user_id, group_id)
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return f"[关于此用户的长期记忆]\n{content}"
        except Exception as e:
            logger.warning(f"读取冷记忆失败: {path} — {e}")
        return ""

    async def _archive_to_md(
        self, user_id: int, group_id: Optional[int], summary: str,
    ) -> None:
        """将热记忆摘要归档到冷记忆 memory.md。"""
        if not summary:
            return

        path = self._memory_md_path(user_id, group_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_entry = f"## {date_str}\n{summary}\n"

        existing = ""
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except Exception:
                pass

        combined = existing + "\n" + new_entry if existing else new_entry

        # 冷记忆过大 → AI 压缩
        if len(combined) > _MAX_COLD_MEMORY_CHARS:
            compressed = await self._summarize_cold_memory(combined)
            if compressed:
                combined = f"# 压缩于 {date_str}\n{compressed}\n"
                logger.info(f"冷记忆已压缩: {len(existing)} → {len(combined)} 字符")

        path.write_text(combined.strip() + "\n", encoding="utf-8")
        logger.info(f"已归档到冷记忆: {path}")

    async def _summarize_cold_memory(self, content: str) -> str:
        """AI 压缩过大的冷记忆。"""
        from .client import get_client

        if len(content) < 500:
            return content

        prompt = (
            "请将以下用户的长期记忆内容压缩整理，保留所有关键事实：\n"
            "- 用户的身份、偏好、习惯\n"
            "- 重要的话题和决定\n"
            "- 用户明确提到的个人信息\n"
            "- 按时间倒序排列\n\n"
            f"{content[-4000:]}"  # 只取最后 4000 字符
        )

        try:
            client = get_client()
            resp = await client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个信息整理助手，用中文输出。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
                temperature=0.3,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning(f"冷记忆压缩失败: {e}")
            return content

    # ── 群聊记忆（整个群的共享冷记忆）──────────────────

    def _group_memory_path(self, group_id: int) -> Path:
        return self._base / "group" / str(group_id) / "_group.md"

    async def get_group_memory(self, group_id: int) -> str:
        """获取群聊共享记忆，用于注入 system prompt。"""
        path = self._group_memory_path(group_id)
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return f"[关于本群的共享记忆]\n{content}"
        except Exception as e:
            logger.warning(f"读取群记忆失败: {path} — {e}")
        return ""

    async def _archive_to_group_memory(self, group_id: int, summary: str) -> None:
        """将热记忆摘要归档到群共享 memory.md。"""
        if not summary:
            return

        path = self._group_memory_path(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_entry = f"## {date_str}\n{summary}\n"

        existing = ""
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except Exception:
                pass

        combined = existing + "\n" + new_entry if existing else new_entry

        if len(combined) > _MAX_COLD_MEMORY_CHARS:
            compressed = await self._summarize_cold_memory(combined)
            if compressed:
                combined = f"# 压缩于 {date_str}\n{compressed}\n"
                logger.info(f"群记忆已压缩: {len(existing)} → {len(combined)} 字符")

        path.write_text(combined.strip() + "\n", encoding="utf-8")
        logger.info(f"已归档到群记忆: {path}")

    # ── 清除冷记忆 ────────────────────────────────────

    async def clear_long_term_memory(
        self, user_id: int, group_id: Optional[int] = None,
    ) -> None:
        """清除冷记忆文件。"""
        path = self._memory_md_path(user_id, group_id)
        if path.exists():
            path.unlink()
            logger.info(f"冷记忆已清除: {path}")


# 全局单例
_memory_manager: Optional[MemoryManager] = None


def get_memory() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager

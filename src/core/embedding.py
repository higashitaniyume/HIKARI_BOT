"""嵌入模型 + 记忆向量存储 —— 硅基流动 Qwen3 Embedding API。

用途：归档 memory.md 摘要时嵌入向量 → AI 可通过语义搜索回忆过去。

模型：Qwen/Qwen3-Embedding-8B（4096维）
性能：~24ms/次（API 网络往返）
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("hikari.core.embedding")

# ============================================================================
# 配置
# ============================================================================

_CONFIG_CACHE: dict = {}


def _load_config() -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE:
        return _CONFIG_CACHE
    try:
        from src.core.config import _ROOT
        p = _ROOT / "config.json"
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            _CONFIG_CACHE = raw.get("embedding", {})
    except Exception:
        pass
    if not _CONFIG_CACHE:
        _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _get_config() -> tuple[str, str, str]:
    cfg = _load_config()
    return (
        cfg.get("api_url", "https://api.siliconflow.cn/v1/embeddings"),
        cfg.get("api_key", ""),
        cfg.get("model", "Qwen/Qwen3-Embedding-8B"),
    )


# ============================================================================
# 嵌入 API
# ============================================================================


async def embed_one(text: str) -> list[float]:
    if not text.strip():
        return [0.0] * 4096
    r = await embed_batch([text])
    return r[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    api_url, api_key, model = _get_config()
    if not api_key:
        logger.warning("嵌入 API Key 未配置")
        return [[0.0] * 4096 for _ in texts]

    valid = [t for t in texts if t.strip()]
    if not valid:
        return [[0.0] * 4096 for _ in texts]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            start = time.monotonic()
            resp = await client.post(
                api_url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "input": valid},
            )
            elapsed = time.monotonic() - start
            resp.raise_for_status()
            data = resp.json()

        embeddings = [item["embedding"] for item in data["data"]]
        logger.debug(
            f"嵌入完成: {len(valid)}条, {len(embeddings[0])}d, "
            f"{elapsed:.2f}s, tokens={data.get('usage', {}).get('total_tokens', '?')}"
        )
        return embeddings
    except Exception as e:
        logger.error(f"嵌入 API 失败: {e}")
        return [[0.0] * 4096 for _ in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


# ============================================================================
# 记忆向量存储（只存 memory.md 摘要的向量，不是每条消息）
# ============================================================================


class MemoryVectorStore:
    """针对 memory.md 摘要的语义向量索引。

    目录结构：
        data/memory_vectors/
        ├── group_{gid}_{uid}.json   ← 个人在群里的记忆向量
        ├── group_{gid}__group.json  ← 群共享记忆向量
        └── private_{uid}.json       ← 私聊记忆向量

    每条记录：{text, embedding, date}
    """

    _MAX_RECORDS = 500

    def __init__(self, store_dir: str = "data/memory_vectors"):
        self._base = Path(store_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _file_path(self, user_id: int, group_id: int | None) -> Path:
        if group_id is not None:
            return self._base / f"group_{group_id}_{user_id}.json"
        return self._base / f"private_{user_id}.json"

    def _group_path(self, group_id: int) -> Path:
        return self._base / f"group_{group_id}__group.json"

    async def add(
        self, user_id: int, group_id: int | None,
        text: str, date_str: str = "",
        *, is_group_shared: bool = False,
    ) -> None:
        """存入一条记忆摘要及向量。"""
        if not text.strip():
            return

        vec = await embed_one(text)
        if all(v == 0.0 for v in vec):
            return

        record = {"text": text[:800], "embedding": vec, "date": date_str}

        path = (
            self._group_path(group_id) if is_group_shared
            else self._file_path(user_id, group_id)
        )
        async with self._lock:
            data = []
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = []
            data.append(record)
            if len(data) > self._MAX_RECORDS:
                data = data[-self._MAX_RECORDS:]
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    async def search(
        self, user_id: int, group_id: int | None,
        query: str, top_k: int = 5,
    ) -> list[dict]:
        """语义搜索记忆摘要。"""
        qv = await embed_one(query)
        if all(v == 0.0 for v in qv):
            return []

        # 同时搜个人记忆和群共享记忆
        paths = [self._file_path(user_id, group_id)]
        if group_id is not None:
            paths.append(self._group_path(group_id))

        all_records = []
        async with self._lock:
            for path in paths:
                if not path.exists():
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    all_records.extend(data)
                except Exception:
                    continue

        if not all_records:
            return []

        scores = []
        for rec in all_records:
            vec = rec.get("embedding", [])
            if len(vec) != len(qv):
                continue
            scores.append((sum(a * b for a, b in zip(qv, vec)), rec))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [
            {"text": rec["text"], "date": rec.get("date", ""), "similarity": round(sim, 3)}
            for sim, rec in scores[:top_k]
        ]


_mv_store: Optional[MemoryVectorStore] = None


def get_memory_vector_store() -> MemoryVectorStore:
    global _mv_store
    if _mv_store is None:
        _mv_store = MemoryVectorStore()
    return _mv_store

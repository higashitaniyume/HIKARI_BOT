"""嵌入模型 —— 通过硅基流动 Qwen3 Embedding API 将文本转为语义向量。

模型：Qwen/Qwen3-Embedding-8B（4096维，中英文跨语言）
性能：~24ms/条（网络往返），纯 API 调用无本地 GPU 需求
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
# 配置（从 config.json 读取，带兜底）
# ============================================================================

_EMBEDDING_CONFIG: dict = {}


def _load_config() -> dict:
    global _EMBEDDING_CONFIG
    if _EMBEDDING_CONFIG:
        return _EMBEDDING_CONFIG
    try:
        from src.core.config import _ROOT
        config_path = _ROOT / "config.json"
        if config_path.exists():
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            _EMBEDDING_CONFIG = raw.get("embedding", {})
    except Exception:
        pass
    if not _EMBEDDING_CONFIG:
        _EMBEDDING_CONFIG = {}
    return _EMBEDDING_CONFIG


def _get_api_config() -> tuple[str, str, str]:
    """返回 (api_url, api_key, model)。"""
    cfg = _load_config()
    api_url = cfg.get("api_url", "https://api.siliconflow.cn/v1/embeddings")
    api_key = cfg.get("api_key", "")
    model = cfg.get("model", "Qwen/Qwen3-Embedding-8B")
    return api_url, api_key, model


# ============================================================================
# 嵌入 API
# ============================================================================


async def embed_one(text: str) -> list[float]:
    """嵌入单条文本。"""
    if not text.strip():
        return [0.0] * 4096
    result = await embed_batch([text])
    return result[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量嵌入文本。"""
    api_url, api_key, model = _get_api_config()
    if not api_key:
        logger.warning("嵌入 API Key 未配置")
        return [[0.0] * 4096 for _ in texts]

    # 去空
    valid = [t for t in texts if t.strip()]
    if not valid:
        return [[0.0] * 4096 for _ in texts]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            start = time.monotonic()
            resp = await client.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "input": valid},
            )
            elapsed = time.monotonic() - start
            resp.raise_for_status()
            data = resp.json()

        embeddings = [item["embedding"] for item in data["data"]]
        usage = data.get("usage", {})
        logger.debug(
            f"嵌入完成: {len(valid)} 条 → {len(embeddings[0])}d, "
            f"耗时 {elapsed:.2f}s, tokens={usage.get('total_tokens', '?')}"
        )
        return embeddings

    except Exception as e:
        logger.error(f"嵌入 API 调用失败: {e}")
        return [[0.0] * 4096 for _ in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


# ============================================================================
# 向量存储
# ============================================================================


class VectorStore:
    """轻量向量存储：JSON 文件 + 余弦相似度搜索。

    每上下文一个文件，存在 vector_store/ 目录下。
    每条消息存储文本 + 向量 + 元数据。
    每上下文上限 3000 条，超了删最旧的。
    """

    _MAX_RECORDS = 3000

    def __init__(self, store_dir: str = "data/vector_store"):
        self._base = Path(store_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _file_path(self, group_id: int | None, user_id: int) -> Path:
        if group_id is not None:
            return self._base / f"group_{group_id}.json"
        return self._base / f"private_{user_id}.json"

    async def add(
        self, group_id: int | None, user_id: int,
        text: str, sender_name: str, sender_id: int,
        msg_time: str, msg_id: int | None = None,
    ) -> None:
        """存入一条消息及其向量。后台异步调用，不影响主流程。"""
        if not text.strip():
            return

        vec = await embed_one(text)
        if all(v == 0.0 for v in vec):
            return

        record = {
            "text": text[:500],
            "embedding": vec,
            "sender_name": sender_name,
            "sender_id": sender_id,
            "time": msg_time,
            "msg_id": msg_id,
        }

        path = self._file_path(group_id, user_id)
        async with self._lock:
            data = []
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = []
            data.append(record)
            # 超限删最旧
            if len(data) > self._MAX_RECORDS:
                data = data[-self._MAX_RECORDS:]
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    async def search(
        self, group_id: int | None, user_id: int,
        query: str, top_k: int = 5,
    ) -> list[dict]:
        """语义搜索：返回最相似的 top_k 条消息。"""
        path = self._file_path(group_id, user_id)
        if not path.exists():
            return []

        qv = await embed_one(query)
        if all(v == 0.0 for v in qv):
            return []

        async with self._lock:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return []

        if not data:
            return []

        scores = []
        for rec in data:
            vec = rec.get("embedding", [])
            if len(vec) != len(qv):
                continue
            sim = sum(a * b for a, b in zip(qv, vec))
            scores.append((sim, rec))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "text": rec["text"],
                "sender_name": rec.get("sender_name", "?"),
                "sender_id": rec.get("sender_id", 0),
                "time": rec.get("time", ""),
                "similarity": round(sim, 3),
            }
            for sim, rec in scores[:top_k]
        ]


_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store

"""本地嵌入模型 —— 将文本转为语义向量，用于相似度搜索。

模型：paraphrase-multilingual-MiniLM-L12-v2（384维，中英文，~120MB）
性能：~1.1ms/条（CPU），首次加载 ~15s

首次加载需要从 HuggingFace 下载模型。国内可通过代理或镜像加速：
    HF_ENDPOINT=https://hf-mirror.com          # 镜像
    HTTPS_PROXY=http://127.0.0.1:7897           # 代理
    在 config.json 的 embedding 节配置：
        "mirror": "https://hf-mirror.com",
        "proxy": "http://127.0.0.1:7897"
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("hikari.core.embedding")

_model: Optional["EmbeddingModel"] = None


def _load_embedding_config() -> tuple[str, str]:
    """从 config.json 读取 embedding 相关的代理/镜像配置。"""
    mirror = ""
    proxy = ""
    try:
        # 延迟导入避免循环
        from src.core.config import _ROOT
        config_path = _ROOT / "config.json"
        if config_path.exists():
            import json
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            emb = raw.get("embedding", {})
            mirror = emb.get("mirror", "")
            proxy = emb.get("proxy", "")
    except Exception:
        pass
    return mirror, proxy


class EmbeddingModel:
    """本地嵌入模型封装。"""

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self._model_name = model_name
        self._model = None
        self._dim = 0
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return

        # ── 配置代理/镜像 ──────────────────────────
        mirror, proxy = _load_embedding_config()
        if mirror and "HF_ENDPOINT" not in os.environ:
            os.environ["HF_ENDPOINT"] = mirror
            logger.info(f"HuggingFace 镜像: {mirror}")
        if proxy:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                if key not in os.environ:
                    os.environ[key] = proxy
            logger.info(f"HuggingFace 代理: {proxy}")

        logger.info(f"加载嵌入模型: {self._model_name} ...")
        start = time.monotonic()
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(self._model_name)
        self._dim = self._model.get_embedding_dimension()
        self._loaded = True
        elapsed = time.monotonic() - start
        logger.info(f"嵌入模型就绪: dim={self._dim}, 耗时 {elapsed:.1f}s")

    @property
    def dim(self) -> int:
        self._ensure_loaded()
        return self._dim

    async def encode_one(self, text: str) -> list[float]:
        """嵌入单条文本。"""
        if not text.strip():
            return [0.0] * self.dim
        result = await self.encode_batch([text])
        return result[0]

    async def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入（CPU 密集型，扔到线程池跑）。"""
        self._ensure_loaded()
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: self._model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist(),
        )
        return embeddings


def get_embedding() -> EmbeddingModel:
    """获取全局嵌入模型单例。"""
    global _model
    if _model is None:
        _model = EmbeddingModel()
    return _model


# ============================================================================
# 向量存储
# ============================================================================


class VectorStore:
    """轻量向量存储：JSON 文件 + NumPy 余弦相似度搜索。

    存储结构（每个 context 一个文件）：
        vector_store/
        ├── group_1071939984.json
        └── private_3433559280.json

    每条记录：
        {"text": "...", "embedding": [...], "sender": "...", "time": "...", "msg_id": 123}
    """

    def __init__(self, store_dir: str = "data/vector_store"):
        import json
        from pathlib import Path
        self._base = Path(store_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._json = json

    def _file_path(self, group_id: int | None, user_id: int) -> "Path":
        if group_id is not None:
            return self._base / f"group_{group_id}.json"
        return self._base / f"private_{user_id}.json"

    async def add(
        self,
        group_id: int | None,
        user_id: int,
        text: str,
        sender_name: str,
        sender_id: int,
        msg_time: str,
        msg_id: int | None = None,
    ) -> None:
        """存入一条消息及其向量。"""
        if not text.strip():
            return

        emb = get_embedding()
        vec = await emb.encode_one(text)

        record = {
            "text": text,
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
                    data = self._json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = []
            data.append(record)
            path.write_text(
                self._json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )

    async def search(
        self,
        group_id: int | None,
        user_id: int,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """语义搜索：返回最相似的 top_k 条消息。"""
        path = self._file_path(group_id, user_id)
        if not path.exists():
            return []

        emb = get_embedding()
        qv = await emb.encode_one(query)

        async with self._lock:
            try:
                data = self._json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return []

        if not data:
            return []

        # 算余弦相似度（向量已归一化，点积 = 余弦相似度）
        scores = []
        for rec in data:
            vec = rec.get("embedding", [])
            if len(vec) != len(qv):
                continue
            sim = sum(a * b for a, b in zip(qv, vec))
            scores.append((sim, rec))

        scores.sort(key=lambda x: x[0], reverse=True)
        top = scores[:top_k]

        return [
            {
                "text": rec["text"],
                "sender_name": rec.get("sender_name", "?"),
                "sender_id": rec.get("sender_id", 0),
                "time": rec.get("time", ""),
                "similarity": round(sim, 3),
            }
            for sim, rec in top
        ]


_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store

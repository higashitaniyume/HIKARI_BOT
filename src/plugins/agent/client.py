"""DeepSeek API 客户端、Token 估算、重试逻辑。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

from openai import AsyncOpenAI

from src.core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
)

logger = logging.getLogger("hikari.plugins.agent")

# ============================================================================
# 常量
# ============================================================================

_MAX_CONCURRENT_API = 3
_MAX_API_RETRIES = 3

# ============================================================================
# Token 估算
# ============================================================================

_RE_CHINESE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
_RE_ENGLISH_WORD = re.compile(r"[a-zA-Z]+")
_RE_ENGLISH_CHAR = re.compile(r"[a-zA-Z]")


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。中文≈1.5t/字，英文≈1.3t/词，其余≈0.25t/符。"""
    chinese = len(_RE_CHINESE.findall(text))
    english_words = len(_RE_ENGLISH_WORD.findall(text))
    english_chars = len(_RE_ENGLISH_CHAR.findall(text))
    other = len(text) - chinese - english_chars
    return max(0, int(chinese * 1.5 + english_words * 1.3 + other * 0.25))


def estimate_total_tokens(messages: list[dict]) -> int:
    """估算消息列表的总 token 数（含角色标记开销≈4t/条）。"""
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", "")) + 4
    return total


# ============================================================================
# DeepSeek 客户端
# ============================================================================

_client: Optional[AsyncOpenAI] = None
_api_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_API)


def get_client() -> AsyncOpenAI | None:
    """获取全局唯一的 AsyncOpenAI 客户端。

    若 API key 未配置则返回 None，调用方应自行处理。
    """
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            logger.warning("DEEPSEEK_API_KEY 未设置！Agent 将无法工作。")
            return None
        _client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=60.0,
        )
    return _client


# ============================================================================
# 重试判断
# ============================================================================


def should_retry(exc: Exception) -> bool:
    """判断 API 异常是否应该重试。"""
    status = getattr(exc, "status_code", None)
    if status is not None:
        if status == 429:
            return True
        if 500 <= status < 600:
            return True
        if status < 500:
            return False
    exc_name = type(exc).__name__
    if "Connection" in exc_name or "Timeout" in exc_name:
        return True
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    return False


# ============================================================================
# AI 调用
# ============================================================================


async def call_ai(
    messages: list[dict],
    tools: list[dict] | None = None,
) -> dict:
    """调用 DeepSeek API（支持 function calling）。

    Returns:
        API 响应的 choice 字典（含 message 和可选的 tool_calls）
    """
    if not DEEPSEEK_API_KEY:
        return {"message": {"content": "❌ AI 未配置，请联系管理员。", "tool_calls": None}}

    client = get_client()
    last_error: Optional[Exception] = None

    for attempt in range(_MAX_API_RETRIES):
        try:
            async with _api_semaphore:
                start = time.monotonic()
                kwargs: dict[str, Any] = {
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "max_tokens": 1024,
                    "temperature": 0.8,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                response = await client.chat.completions.create(**kwargs)
                elapsed = time.monotonic() - start
                choice = response.choices[0]
                info_parts = [
                    f"模型={DEEPSEEK_MODEL}",
                    f"耗时={elapsed:.1f}s",
                    f"tokens={response.usage.total_tokens if response.usage else '?'}",
                ]
                if attempt > 0:
                    info_parts.append(f"重试#{attempt}")
                if choice.message.tool_calls:
                    names = [tc.function.name for tc in choice.message.tool_calls]
                    info_parts.append(f"工具调用={names}")
                logger.info(f"AI 响应 ({', '.join(info_parts)})")
                return {
                    "message": {
                        "role": choice.message.role,
                        "content": choice.message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in (choice.message.tool_calls or [])
                        ] if choice.message.tool_calls else None,
                    },
                }

        except Exception as e:
            last_error = e
            if attempt < _MAX_API_RETRIES - 1 and should_retry(e):
                delay = 2 ** attempt
                logger.warning(
                    f"AI API 调用失败 (attempt {attempt + 1}/{_MAX_API_RETRIES}), "
                    f"{delay}s 后重试: {type(e).__name__}: {e}"
                )
                await asyncio.sleep(delay)
            else:
                break

    logger.error(
        f"AI API 调用最终失败: {type(last_error).__name__}: {last_error}"
    )
    return {"message": {"content": "❌ AI 暂时不可用，请稍后再试~", "tool_calls": None}}

"""杂项工具 —— 余额查询（DeepSeek + 硅基流动）+ 时间获取。"""

import asyncio
from datetime import datetime
from urllib.parse import urlparse

import httpx

from src.core.config import DEEPSEEK_API_KEY, EMBEDDING_API_KEY, EMBEDDING_API_URL


async def tool_check_balance() -> str:
    """查询 DeepSeek + 硅基流动 API 账户余额（并发）。"""
    ds, sf = await asyncio.gather(
        _check_deepseek(),
        _check_siliconflow(),
    )
    return "\n".join(r for r in [ds, sf] if r)


async def _check_deepseek() -> str:
    """查询 DeepSeek 账户余额。"""
    if not DEEPSEEK_API_KEY:
        return "❌ DeepSeek API Key 未配置"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "⏱️ DeepSeek 查询超时"
    except Exception as e:
        return f"❌ DeepSeek 查询失败: {e}"

    if data.get("is_available"):
        infos = data.get("balance_infos", [])
        if infos:
            parts = [f"{info.get('total_balance', '?')} {info.get('currency', '?')}" for info in infos]
            return f"✅ DeepSeek 余额: {', '.join(parts)}"
        return "✅ DeepSeek API 可用，但未获取到余额明细"
    return f"⚠️ DeepSeek API 状态: {data}"


async def _check_siliconflow() -> str:
    """查询硅基流动账户余额。"""
    if not EMBEDDING_API_KEY:
        return "❌ 硅基流动 API Key 未配置"

    try:
        # 从 embedding API URL 推导 base URL
        parsed = urlparse(EMBEDDING_API_URL) if EMBEDDING_API_URL else urlparse("https://api.siliconflow.cn")
        base = f"{parsed.scheme}://{parsed.netloc}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base}/v1/user/info",
                headers={"Authorization": f"Bearer {EMBEDDING_API_KEY}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "⏱️ 硅基流动 查询超时"
    except Exception as e:
        return f"❌ 硅基流动 查询失败: {e}"

    # 响应格式: {"code": 20000, "data": {"balance": "...", "totalBalance": "...", ...}}
    inner = data.get("data", {})
    if data.get("code") == 20000 and inner:
        total = inner.get("totalBalance") or inner.get("balance", "?")
        charge = inner.get("chargeBalance", "")
        if charge:
            return f"✅ 硅基流动 余额: {total} (含赠送 {charge})"
        return f"✅ 硅基流动 余额: {total}"
    return f"⚠️ 硅基流动 API 状态: {data}"


async def tool_get_time() -> str:
    """获取当前时间。"""
    now = datetime.now()
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    return (
        f"现在是 {now.year}年{now.month}月{now.day}日 "
        f"星期{weekday} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
    )

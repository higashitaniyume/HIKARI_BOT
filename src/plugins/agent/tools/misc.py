"""杂项工具 —— 余额查询 + 时间获取。"""

from datetime import datetime

import httpx

from src.core.config import DEEPSEEK_API_KEY


async def tool_check_balance() -> str:
    """查询 DeepSeek API 账户余额。"""
    if not DEEPSEEK_API_KEY:
        return "❌ API Key 未配置"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "⏱️ 查询超时"
    except Exception as e:
        return f"❌ 查询失败: {e}"

    if data.get("is_available"):
        infos = data.get("balance_infos", [])
        if infos:
            parts = [f"{info.get('total_balance', '?')} {info.get('currency', '?')}" for info in infos]
            return f"✅ DeepSeek 余额: {', '.join(parts)}"
        return "✅ API 可用，但未获取到余额明细"
    return f"⚠️ API 状态: {data}"


async def tool_get_time() -> str:
    """获取当前时间。"""
    now = datetime.now()
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    return (
        f"现在是 {now.year}年{now.month}月{now.day}日 "
        f"星期{weekday} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
    )

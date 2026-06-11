"""搜索工具 —— 网页搜索 + 聊天记录搜索。"""

import logging
import re

import httpx

from src.core.config import SEARXNG_API
from src.core.message_store import get_message_store

logger = logging.getLogger("hikari.plugins.agent")


def _parse_date(s: str) -> str | None:
    """解析中文日期字符串为 YYYY-MM-DD 格式。"""
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"(\d{1,2})月(\d{1,2})[日号]?", s)
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]?", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


# ── 网页搜索 ──────────────────────────────────────────────


async def tool_search_web(query: str) -> str:
    """通过 SearXNG 搜索网页。"""
    if not SEARXNG_API:
        return "❌ 搜索服务未配置"

    try:
        import urllib.parse
        url = f"{SEARXNG_API.rstrip('/')}/search?q={urllib.parse.quote(query)}&format=json"
        # 限制响应体大小，防止超大响应耗尽内存
        async with httpx.AsyncClient(timeout=15.0, trust_env=False,
                                      limits=httpx.Limits(max_keepalive_connections=1)) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            # 检查响应大小
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > 10 * 1024 * 1024:  # 10 MB
                return "❌ 搜索结果过大，请缩小搜索范围"
            data = resp.json()
    except httpx.TimeoutException:
        return "⏱️ 搜索超时，请稍后再试"
    except Exception as e:
        logger.error(f"搜索失败: {e}")
        return f"❌ 搜索失败: {e}"

    results = data.get("results", [])
    if not results:
        return f"未找到关于「{query}」的搜索结果"

    lines = [f"搜索「{query}」的结果（共 {len(results)} 条，显示前 5 条）："]
    for i, r in enumerate(results[:5]):
        title = r.get("title", "无标题")
        url = r.get("url", "")
        snippet = (r.get("content") or r.get("snippet", ""))[:200]
        lines.append(f"\n{i + 1}. {title}\n   {url}\n   {snippet}")
    return "\n".join(lines)


# ── 聊天记录搜索 ──────────────────────────────────────────


async def tool_search_chat_history(
    group_id: int | None, user_id: int,
    keyword: str = "", count: int = 10,
    start_date: str = "", end_date: str = "",
) -> str:
    """搜索当前会话的聊天记录（支持关键词+日期范围，不可跨上下文）。"""
    store = get_message_store()
    count = max(1, min(count, 15))

    if group_id is not None:
        messages = await store.get_group_messages(group_id)
        scope = f"群 {group_id}"
    else:
        messages = await store.get_private_messages(user_id)
        scope = "私聊"

    if not messages:
        return f"当前 {scope} 暂无聊天记录"

    # 日期范围过滤
    if start_date or end_date:
        start_d = _parse_date(start_date) if start_date else None
        end_d = _parse_date(end_date) if end_date else None
        if start_d is not None or end_d is not None:
            filtered = []
            for m in messages:
                t = str(m.get("time", ""))[:10]
                if start_d and t < start_d:
                    continue
                if end_d and t > end_d:
                    continue
                filtered.append(m)
            messages = filtered

    # 关键词搜索
    kw = (keyword or "").strip().lower()
    if kw:
        matched = []
        for m in messages:
            msg_text = str(m.get("message", "")).lower()
            raw_text = str(m.get("raw_message", "")).lower()
            sender_info = m.get("sender", {})
            sender_name = (
                sender_info.get("card")
                or sender_info.get("nickname")
                or str(sender_info.get("user_id", "?"))
            ).lower()
            if kw in msg_text or kw in raw_text or kw in sender_name:
                matched.append(m)
        messages = matched

    if not messages:
        desc = f"关键词「{keyword}」" if kw else ""
        desc += f" 日期 {start_date}~{end_date}" if (start_date or end_date) else ""
        return f"在 {scope} 的记录中未找到匹配的消息{('（' + desc.strip() + '）') if desc else ''}"

    recent = messages[-count:]
    date_info = f"，{start_date or '...'} ~ {end_date or '...'}" if (start_date or end_date) else ""
    lines = [f"{scope} 的聊天记录" + (f"（搜索「{keyword}」{date_info}，{len(messages)} 条匹配，显示最近 {len(recent)} 条）：" if kw else f"（最近 {len(recent)} 条）：")]
    for m in recent:
        sender = m.get("sender", {})
        uid = sender.get("user_id", "?")
        nick = sender.get("card") or sender.get("nickname") or str(uid)
        t = str(m.get("time", ""))[:19]
        msg = str(m.get("message", ""))[:200]
        lines.append(f"  [{t}] {nick}(QQ{uid}): {msg}")

    return "\n".join(lines)

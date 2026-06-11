"""语义搜索工具 —— 按含义查找聊天记录。"""

import logging

from src.core.embedding import get_vector_store

logger = logging.getLogger("hikari.plugins.agent")


async def tool_search_semantic(
    group_id: int | None, user_id: int,
    query: str, top_k: int = 5,
) -> str:
    """语义搜索聊天记录（按含义而非关键词）。"""
    top_k = max(1, min(top_k, 15))
    store = get_vector_store()

    try:
        results = await store.search(group_id, user_id, query, top_k=top_k)
    except Exception as e:
        logger.error(f"语义搜索失败: {e}")
        return f"❌ 语义搜索失败: {e}"

    if not results:
        scope = f"群 {group_id}" if group_id else "私聊"
        return f"在 {scope} 的向量存储中未找到与「{query}」语义相关的消息"

    lines = [f"语义搜索「{query}」结果（{len(results)} 条）："]
    for i, r in enumerate(results):
        sim = r["similarity"]
        bar = "█" * max(1, int(sim * 10))
        lines.append(
            f"\n{i + 1}. [{r['time']}] {r['sender_name']}(QQ{r['sender_id']}):\n"
            f"   {r['text'][:200]}   ({bar} {sim})"
        )
    return "\n".join(lines)

"""AI 记忆管理工具 —— 清除/查看/回忆。"""

from typing import Optional

from src.core.embedding import get_memory_vector_store
from ..memory import get_memory


async def tool_manage_memory(
    user_id: int, group_id: Optional[int], action: str,
    query: str = "",
) -> str:
    """管理 AI 记忆。"""
    mem = get_memory()
    scope = f"群{group_id}" if group_id else "私聊"

    if action == "clear":
        await mem.clear(user_id, group_id)
        return f"✅ 已清除你在 {scope} 的 AI 记忆"

    elif action == "view":
        count = await mem.count(user_id, group_id)
        return f"📝 你在 {scope} 的 AI 记忆: {count} 条（{count // 2} 轮对话）"

    elif action == "recall":
        if not query:
            return "❌ 请提供要回忆的内容描述，如'之前讨论的技术栈是什么'"
        try:
            store = get_memory_vector_store()
            results = await store.search(user_id, group_id, query, top_k=5)
        except Exception as e:
            return f"❌ 记忆检索失败: {e}"

        if not results:
            return f"在 {scope} 的记忆中未找到与「{query}」相关的内容"

        lines = [f"记忆检索「{query}」（{len(results)} 条）："]
        for i, r in enumerate(results):
            lines.append(f"\n{i + 1}. [{r['date']}] {r['text'][:300]} (相关度 {r['similarity']})")
        return "\n".join(lines)

    return f"❌ 未知操作: {action}"

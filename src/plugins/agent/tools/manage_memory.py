"""AI 记忆管理工具。"""

from typing import Optional

from ..memory import get_memory


async def tool_manage_memory(
    user_id: int, group_id: Optional[int], action: str,
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
    return f"❌ 未知操作: {action}"

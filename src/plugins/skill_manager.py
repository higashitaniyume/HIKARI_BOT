"""技能管理模块 —— 允许用户切换 AI 人格。

命令：
    /skills         — 列出所有可用技能
    /skill <name>   — 切换到指定技能
    /skill off      — 恢复默认技能

技能定义存储在 data/skills/*.json，每个 JSON 描述一个人物提示词。
用户状态存储在 data/skills/user_state.json。
"""

from __future__ import annotations

import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Event,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.params import CommandArg

from src.core.config import (
    get_default_skill,
    get_user_skill,
    list_skills,
    set_user_skill,
)
from src.plugins.admin import WHITELIST

logger = logging.getLogger("hikari.plugins.skill_manager")

# ============================================================================
# /skills —— 列出所有技能
# ============================================================================

skills_cmd = on_command("skills", rule=WHITELIST, priority=10)


@skills_cmd.handle()
async def handle_list_skills(event: Event):
    """列出所有可用技能。"""
    all_skills = list_skills()
    if not all_skills:
        await skills_cmd.finish("📭 当前没有任何技能可用。")

    user_id = event.user_id
    current = get_user_skill(user_id)
    default_name = get_default_skill()

    lines: list[str] = ["🎭 可用技能列表："]
    for sk in all_skills:
        name = sk["name"]
        display = sk.get("display_name", name)
        desc = sk.get("description", "")
        # 如果 display_name 等于 name（kebab-case 格式名），不重复显示
        if display == name:
            show_name = name
        else:
            show_name = f"{display}（{name}）"
        badges: list[str] = []
        if name == default_name:
            badges.append("默认")
        if name == current:
            badges.append("当前使用")
        badge_str = f" [{', '.join(badges)}]" if badges else ""
        lines.append(f"  • {show_name}{badge_str}")
        if desc:
            desc_short = desc.replace("\n", " ")[:80]
            lines.append(f"    {desc_short}{'…' if len(desc) > 80 else ''}")

    lines.append("")
    lines.append("发送 /skill <名称> 切换技能，发送 /skill off 恢复默认。")
    await skills_cmd.finish("\n".join(lines))


# ============================================================================
# /skill —— 切换技能
# ============================================================================

skill_cmd = on_command("skill", rule=WHITELIST, priority=10)


@skill_cmd.handle()
async def handle_set_skill(event: Event, args: Message = CommandArg()):
    """切换用户的活跃技能。"""
    arg = str(args).strip().lower()
    user_id = event.user_id

    if not arg or arg == "off":
        # 恢复默认
        default_name = get_default_skill()
        try:
            set_user_skill(user_id, None)
        except Exception as e:
            logger.error(f"清除用户技能失败: {e}")
            await skill_cmd.finish("❌ 操作失败，请稍后再试~")
        if default_name:
            await skill_cmd.finish(
                f"✅ 已恢复默认技能（{default_name}）~"
            )
        else:
            await skill_cmd.finish("✅ 已清除技能设置~")

    # 切换到指定技能
    all_skills = list_skills()
    all_names = {s["name"]: s for s in all_skills}

    if arg not in all_names:
        suggestions = ", ".join(all_names.keys()) if all_names else "（暂无可用技能）"
        await skill_cmd.finish(
            f"❌ 技能 \"{arg}\" 不存在。\n可用技能: {suggestions}\n发送 /skills 查看详情。"
        )

    try:
        set_user_skill(user_id, arg)
    except ValueError as e:
        await skill_cmd.finish(f"❌ {e}")
    except Exception as e:
        logger.error(f"设置用户技能失败: {e}")
        await skill_cmd.finish("❌ 操作失败，请稍后再试~")

    display = all_names[arg].get("display_name", arg)
    await skill_cmd.finish(f"🎭 已切换到技能「{display}」~")

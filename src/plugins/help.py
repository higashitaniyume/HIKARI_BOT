"""帮助信息插件 —— @机器人 help/帮助 或私聊 /help 返回命令列表。"""

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.params import CommandArg
from nonebot.rule import Rule, to_me

# ============================================================================
# 帮助文本
# ============================================================================

HELP_TEXT = """╔════════════════════════════╗
║   HIKARI_BOT 命令列表     ║
╚════════════════════════════╝

📌 通用命令（白名单可用）
/chat <消息>  AI 对话
/clearmemory  清除 AI 记忆
/memory       查看记忆条数
/help 或 /帮助  显示本帮助

📌 媒体/文件命令（白名单可用）
/sendimg  <路径> <目标>
  发送图片
/sendvideo <路径> <目标>
  发送视频
/sendvoice <路径> <目标>
  发送语音
/sendfile <路径或URL> <目标>
  发送文件

📌 触发方式（白名单可用）
・群内 @机器人 + 消息
  → AI 对话
・群内 @机器人 + 媒体链接
  → 解析视频（目前支持 X/Twitter）
・私聊任意消息
  → AI 对话
・私聊发送媒体链接
  → 解析视频

📌 管理员命令（仅超级管理员）
/wl add user <QQ>
  添加用户白名单
/wl add group <群号>
  添加群白名单
/wl remove user <QQ>
  移除用户白名单
/wl remove group <群号>
  移除群白名单
/wl list  查看白名单
/wl status  查看当前会话状态

💡 目标格式
  QQ号  → 私聊
  group:群号 → 群聊"""


def _build_reply(event: Event) -> Message:
    """构建回复消息，群聊时 @发送者。"""
    if isinstance(event, GroupMessageEvent):
        return (
            MessageSegment.at(event.user_id)
            + MessageSegment.text("\n" + HELP_TEXT)
        )
    return MessageSegment.text(HELP_TEXT)


# ============================================================================
# 私聊：/help 或 /帮助
# ============================================================================

help_cmd = on_command("help", aliases={"帮助"}, priority=10)


@help_cmd.handle()
async def handle_help_cmd(event: Event):
    await help_cmd.finish(_build_reply(event))


# ============================================================================
# 群聊：@机器人 help 或 @机器人 帮助
# ============================================================================


def _is_help_msg(event: Event) -> bool:
    """检查消息文本是否为 help/帮助（@机器人 场景）。"""
    text = event.get_plaintext().strip().lower()
    return text in {"help", "帮助", "命令", "菜单", "功能"}


HELP_KEYWORD = Rule(_is_help_msg)

group_help = on_message(
    rule=to_me() & HELP_KEYWORD,
    priority=10,
    block=True,
)


@group_help.handle()
async def handle_group_help(bot: Bot, event: Event):
    await group_help.finish(_build_reply(event))

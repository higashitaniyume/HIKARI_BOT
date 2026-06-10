"""HIKARI_BOT 核心工具模块。

提供日志系统和消息存储能力，供插件和入口使用。
"""

from src.core.message_store import MessageStore, get_message_store

__all__ = ["MessageStore", "get_message_store"]

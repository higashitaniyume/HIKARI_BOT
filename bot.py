#!/usr/bin/env python3
"""HIKARI_BOT —— 基于 NoneBot2 + OneBot v11 的 QQ Bot 入口。

使用正向 WebSocket 连接 OneBot 服务端，接收事件并通过插件响应。
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from src.core.logger import setup_logging

# ── 日志系统（必须在 nonebot.init() 之前初始化） ─────────────────
setup_logging()

# 初始化 NoneBot
nonebot.init()

# 注册 OneBot V11 适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 加载内置插件（echo 等）和自定义插件
nonebot.load_builtin_plugins()
nonebot.load_plugins("src/plugins")

if __name__ == "__main__":
    nonebot.run()

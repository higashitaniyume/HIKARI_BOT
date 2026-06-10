#!/usr/bin/env python3
"""HIKARI_BOT —— 基于 NoneBot2 + OneBot v11 的 QQ Bot 入口。

使用正向 WebSocket 连接 OneBot 服务端，接收事件并通过插件响应。

配置：
    从项目根目录的 config.json 读取所有配置。
    设置 ENVIRONMENT=prod 时自动使用 config.prod.json。
    也可通过 HIKARI_CONFIG_PATH 直接指定配置文件路径。
"""

import json
import os

# ── 确定配置文件路径 ───────────────────────────────────────────────
if "HIKARI_CONFIG_PATH" not in os.environ:
    env = os.getenv("ENVIRONMENT", "")
    if env == "prod":
        os.environ["HIKARI_CONFIG_PATH"] = "config.prod.json"

# ── 加载 HIKARI 配置（读取 JSON） ────────────────────────────────────
from src.core.config import (
    NONEBOT_DRIVER,
    NONEBOT_WS_URLS,
    NONEBOT_ACCESS_TOKEN,
    NONEBOT_LOG_LEVEL,
)
from src.core.logger import setup_logging

# ── 为 NoneBot2 注入环境变量（必须在 nonebot.init() 之前） ──────────
os.environ["DRIVER"] = NONEBOT_DRIVER
os.environ["ONEBOT_V11_WS_URLS"] = json.dumps(NONEBOT_WS_URLS, ensure_ascii=False)
os.environ["ONEBOT_V11_ACCESS_TOKEN"] = NONEBOT_ACCESS_TOKEN
os.environ["LOG_LEVEL"] = NONEBOT_LOG_LEVEL

# ── 日志系统（必须在 nonebot.init() 之前初始化） ─────────────────────
log_level_map = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}
setup_logging(level=log_level_map.get(NONEBOT_LOG_LEVEL.upper(), 10))

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

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

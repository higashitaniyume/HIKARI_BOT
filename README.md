# HIKARI_BOT

基于 NoneBot2 + OneBot v11 的 QQ Bot。

## 项目结构

```
HIKARI_BOT/
├── .env                  # 开发环境配置
├── .env.prod             # 生产环境配置
├── bot.py                # Bot 入口
├── README.md
└── src/
    ├── core/
    │   └── config.py     # 统一配置管理（环境变量）
    └── plugins/
        ├── admin.py              # 管理员鉴权 & 白名单管理
        ├── ai_chat.py            # AI 聊天（DeepSeek）
        ├── video_parser.py       # 媒体解析（X/Twitter 等）
        ├── media_sender.py       # 媒体文件发送
        ├── file_sender.py        # 文件发送 & 超大媒体降级
        ├── message_collector.py  # 消息收集
        └── hello.py              # Hello World 示例
```

## 配置

- **协议**: OneBot v11 正向 WebSocket（客户端模式）
- **连接地址**: `ws://192.168.31.2:8082/onebot/v11/ws`
- **Access Token**: 已配置在 `.env` / `.env.prod` 中

### 环境变量

```bash
# DeepSeek API
DEEPSEEK_API_KEY=          # API 密钥（必填）
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_SYSTEM_PROMPT=你是一个可爱的QQ机器人。

# AI 记忆
MAX_MEMORY_MESSAGES=20     # 最大保留轮数
AI_MEMORY_DIR=data/ai_memory

# 白名单
WHITELIST_FILE=data/admin/whitelist.json

# Cobalt 视频解析
COBALT_API=http://192.168.31.2:9000/
```

## 如何使用

### 运行

```bash
# 开发环境
python bot.py

# 生产环境（自动加载 .env.prod）
ENVIRONMENT=prod python bot.py
```

### 可用命令

| 命令 | 说明 | 权限 |
| ------ | ------ | ------ |
| `/chat <消息>` | 与 AI 对话 | 白名单 |
| `/clearmemory` | 清除当前会话的 AI 记忆 | 白名单 |
| `/memory` | 查看当前会话的记忆条数 | 白名单 |
| `/wl add user <qq>` | 添加用户白名单 | 超级管理员 |
| `/wl add group <群号>` | 添加群白名单 | 超级管理员 |
| `/wl remove user <qq>` | 移除用户白名单 | 超级管理员 |
| `/wl remove group <群号>` | 移除群白名单 | 超级管理员 |
| `/wl list` | 查看白名单 | 超级管理员 |
| `/wl status` | 查看当前会话白名单状态 | 超级管理员 |

### 触发方式

- **群内 @机器人** — 触发 AI 回复（需在白名单内）
- **私聊任意消息** — 触发 AI 回复（需在白名单内）
- **发送媒体链接**（如 X/Twitter）— 自动解析视频

## 如何添加插件

在 `src/plugins/` 下创建 `.py` 文件即可，NoneBot 会自动加载。

示例：
```python
from nonebot import on_command

help_cmd = on_command("help")

@help_cmd.handle()
async def handle_help():
    await help_cmd.finish("这是 HIKARI_BOT，当前支持的命令：...")
```

## 文档

- [NoneBot2 文档](https://nonebot.dev/)
- [OneBot v11 协议](https://github.com/botuniverse/onebot-11)

# HIKARI_BOT

基于 NoneBot2 + OneBot v11 的 QQ Bot。

## 项目结构

```
HIKARI_BOT/
├── config.json           # 开发环境配置（JSON）
├── config.prod.json      # 生产环境配置（JSON）
├── bot.py                # Bot 入口
├── README.md
└── src/
    ├── core/
    │   └── config.py     # 统一配置管理（读取 JSON）
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

所有配置集中在项目根目录的 `config.json` 文件中。

- **协议**: OneBot v11 正向 WebSocket（客户端模式）
- **连接地址**: 配置在 `config.json` → `nonebot.onebot_v11_ws_urls`

### 配置文件结构

```json
{
    "nonebot": {
        "driver": "~websockets",
        "onebot_v11_ws_urls": ["ws://127.0.0.1:54258/"],
        "access_token": "your-token",
        "log_level": "INFO"
    },
    "deepseek": {
        "api_key": "sk-xxx",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "system_prompt": "你是一个可爱的QQ机器人。"
    },
    "ai_memory": {
        "max_messages": 20,
        "dir": "data/ai_memory"
    },
    "whitelist": {
        "file": "data/admin/whitelist.json"
    },
    "cobalt": {
        "api": "http://127.0.0.1:9000/"
    }
}
```

| 配置节 | 字段 | 说明 |
| ------ | ------ | ------ |
| `nonebot` | `driver` | NoneBot2 驱动 |
| | `onebot_v11_ws_urls` | OneBot 正向 WebSocket 地址 |
| | `access_token` | Access Token |
| | `log_level` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `deepseek` | `api_key` | DeepSeek API 密钥（必填） |
| | `base_url` | API 地址 |
| | `model` | 模型名称 |
| | `system_prompt` | 系统提示词 |
| `ai_memory` | `max_messages` | 最大保留消息条数 |
| | `dir` | 记忆存储目录 |
| `whitelist` | `file` | 白名单文件路径 |
| `cobalt` | `api` | Cobalt 视频解析 API 地址 |

## 如何使用

### 运行

```bash
# 开发环境（使用 config.json）
python bot.py

# 生产环境（使用 config.prod.json）
ENVIRONMENT=prod python bot.py

# 指定自定义配置文件
HIKARI_CONFIG_PATH=/path/to/my-config.json python bot.py
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

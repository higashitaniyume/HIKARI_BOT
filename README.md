# HIKARI_BOT

基于 NoneBot2 + OneBot v11 的智能 QQ Bot，集成 DeepSeek AI 对话、媒体解析、群聊记忆等功能。

## 项目结构

```
HIKARI_BOT/
├── bot.py                    # Bot 入口
├── bump_build.py             # 版本号递增脚本
├── version.json              # 版本信息
├── config.json               # 开发环境配置（gitignored）
├── config.prod.json          # 生产环境配置（gitignored）
├── config.example.json       # 配置模板（可提交）
├── pyproject.toml            # Python 项目配置（uv 包管理）
├── deploy.ps1                # 部署脚本（Windows PowerShell）
├── README.md
├── CLAUDE.md                 # Claude Code AI 助手指令
├── prompts/
│   └── hikari.txt            # AI 角色设定（系统提示词）
├── deploy/
│   └── hikari-bot.service    # systemd 服务文件
└── src/
    ├── core/
    │   ├── config.py         # 统一配置管理（读取 JSON）
    │   ├── logger.py         # 日志系统初始化
    │   ├── log_handler.py    # QQ 日志推送处理器
    │   └── message_store.py  # 消息持久化存储
    └── plugins/
        ├── agent/            # AI Agent 插件（核心）
        │   ├── __init__.py   # 消息入口 + Agent 主循环
        │   ├── client.py     # DeepSeek API 客户端
        │   ├── memory.py     # 两段式对话记忆管理
        │   └── tools.py      # Function calling 工具定义与实现
        ├── ai_chat.py        # AI 聊天（通过 /chat 命令显式调用）
        ├── admin.py          # 管理员鉴权 & 白名单管理
        ├── video_parser.py   # 媒体解析（X/Twitter 等）
        ├── media_sender.py   # 媒体文件发送（base64 编码）
        ├── file_sender.py    # 文件发送 & 超大媒体降级
        ├── message_collector.py  # 全量消息收集
        ├── help.py           # 帮助信息
        ├── hello.py          # 上线通知 + 版本播报
        └── poke.py           # 戳一戳响应
```

## 数据目录

```
data/
├── ai_memory/                # AI 对话记忆
│   ├── private/{uid}.json            # 私聊热记忆
│   ├── private/{uid}_memory.md       # 私聊冷记忆（长期）
│   ├── group/{gid}/{uid}.json        # 群聊热记忆
│   ├── group/{gid}/{uid}_memory.md   # 群聊冷记忆（长期）
│   └── group/{gid}/_group.md         # 群共享记忆
├── admin/
│   └── whitelist.json        # 白名单（热读写）
└── messages/                 # 全量消息存储
    ├── private/{uid}.json
    └── group/{gid}.json
```

## 配置

### 配置文件

开发环境使用 `config.json`，生产环境使用 `config.prod.json`。参考模板见 `config.example.json`。

```json
{
    "nonebot": {
        "driver": "~websockets",
        "onebot_v11_ws_urls": ["ws://127.0.0.1:54258/"],
        "access_token": "your-access-token",
        "log_level": "INFO"
    },
    "deepseek": {
        "api_key": "sk-your-deepseek-api-key",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat"
    },
    "prompt": {
        "file": "prompts/hikari.txt"
    },
    "ai_memory": {
        "max_messages": 40,
        "dir": "data/ai_memory"
    },
    "whitelist": {
        "file": "data/admin/whitelist.json"
    },
    "cobalt": {
        "api": "http://127.0.0.1:9000/"
    },
    "searxng": {
        "api": "http://127.0.0.1:54259/"
    }
}
```

| 配置节 | 字段 | 说明 |
|--------|------|------|
| `nonebot` | `driver` | NoneBot2 驱动（默认 `~websockets`） |
| `nonebot` | `onebot_v11_ws_urls` | OneBot 正向 WebSocket 地址列表 |
| `nonebot` | `access_token` | Access Token（OneBot 鉴权） |
| `nonebot` | `log_level` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `deepseek` | `api_key` | DeepSeek API 密钥（必填） |
| `deepseek` | `base_url` | API 端点地址 |
| `deepseek` | `model` | 模型名称（如 `deepseek-chat`） |
| `prompt` | `file` | AI 角色设定文件路径（相对于项目根目录） |
| `ai_memory` | `max_messages` | 最大保留消息条数 |
| `ai_memory` | `dir` | 记忆存储目录 |
| `whitelist` | `file` | 白名单 JSON 文件路径 |
| `cobalt` | `api` | Cobalt 视频解析 API 地址 |
| `searxng` | `api` | SearXNG 网页搜索 API 地址 |

### 环境变量

| 变量 | 说明 |
|------|------|
| `ENVIRONMENT` | 设为 `prod`（大小写不敏感）使用 `config.prod.json` |
| `HIKARI_CONFIG_PATH` | 直接指定配置文件路径（优先级最高） |

## 如何使用

### 运行

```bash
# 安装依赖
uv sync

# 开发环境（使用 config.json）
uv run python bot.py

# 生产环境（使用 config.prod.json）
ENVIRONMENT=prod uv run python bot.py

# 指定自定义配置文件
HIKARI_CONFIG_PATH=/path/to/config.json uv run python bot.py
```

### 可用命令

#### AI 对话（Agent 自动路由）

| 触发方式 | 说明 |
|----------|------|
| 群内 **@机器人 + 消息** | 触发 AI Agent（function calling 自动路由） |
| 私聊 **任意消息** | 触发 AI Agent |
| 发送 **媒体链接**（X/Twitter） | 自动解析并下载发送 |

Agent 内置以下能力，AI 会根据用户意图自动选择：

- 💬 聊天回复（默认 @发送者）
- 📥 媒体 URL 解析下载
- 🔍 网页搜索（通过 SearXNG）
- 📋 查看群成员信息
- 📝 搜索聊天记录
- ⏰ 查询当前时间
- 📎 发送文件/媒体
- 🧠 管理对话记忆
- 📨 主动发送消息给指定目标
- ⚙️ 白名单管理（仅超级管理员）

#### 手动命令

| 命令 | 说明 | 权限 |
|------|------|------|
| `/chat <消息>` | 通过旧版 AI 聊天接口对话 | 白名单 |
| `/help` 或 `/帮助` | 显示帮助信息 | 无限制 |
| `/clearmemory` | 清除当前会话的 AI 记忆 | 白名单 |
| `/memory` | 查看当前会话的记忆条数 | 白名单 |
| `/sendimg <路径> <目标>` | 发送图片 | 白名单 |
| `/sendvideo <路径> <目标>` | 发送视频 | 白名单 |
| `/sendvoice <路径> <目标>` | 发送语音 | 白名单 |
| `/sendfile <路径或URL> <目标>` | 发送文件 | 白名单 |
| `/wl add user <QQ>` | 添加用户白名单 | 超级管理员 |
| `/wl add group <群号>` | 添加群白名单 | 超级管理员 |
| `/wl remove user <QQ>` | 移除用户白名单 | 超级管理员 |
| `/wl remove group <群号>` | 移除群白名单 | 超级管理员 |
| `/wl list` | 查看白名单 | 超级管理员 |
| `/wl status` | 查看当前会话白名单状态 | 超级管理员 |

目标格式：`QQ号`（私聊）或 `group:群号`（群聊）。

### 部署

```bash
# Windows（PowerShell）
.\deploy.ps1              # 上传代码 + 安装依赖 + 重启服务
.\deploy.ps1 -Logs        # 查看实时日志
.\deploy.ps1 -Status      # 查看服务状态
```

生产环境通过 systemd 管理（`deploy/hikari-bot.service`），服务自动设置 `ENVIRONMENT=prod`。

## 插件说明

### Agent 插件（核心）

统一消息入口，通过 OpenAI function calling 让 AI 自行决定如何响应用户。支持：

- **群聊上下文**：自动注入最近消息和群成员映射
- **两段式记忆**：热记忆（最近对话）+ 冷记忆（长期归档 markdown）
- **群共享记忆**：每个群一份独立的共享记忆
- **自动记忆压缩**：Token 预算超限时 AI 自动摘要归档
- **频率限制**：每用户每会话 5 秒冷却

### 鉴权体系

- **超级管理员**：QQ `3433559280`（硬编码），永远通过所有鉴权
- **白名单**：JSON 文件热读写，修改后无需重启
- **群聊鉴权**：群在白名单中即可使用全部功能
- **Rule 复用**：其他插件通过 `from src.plugins.admin import WHITELIST, SUPER_ADMIN_RULE` 使用

### 安全机制

- **路径安全**：文件/媒体命令拒绝 `..` 路径遍历，限制在项目目录内
- **AI 文件访问控制**：Agent 只能读取 `downloads/`、`data/media/`、`data/files/` 白名单目录
- **AI 目标限制**：Agent 的 `send_to` 工具限制只能发送到当前会话（管理员不受限）
- **并发控制**：AI API 最大 3 并发（Semaphore），下载/解析有超时限制
- **日志推送**：WARNING 及以上日志自动推送到超级管理员 QQ（每分钟最多 10 条，去重）
- **消息轮转**：单个消息文件超过 5000 条自动归档，防止无限增长

## 开发

### 本地开发流程

```bash
# 1. 修改代码
# 2. 递增版本号（每次 commit 前必须执行）
uv run python bump_build.py

# 3. 提交
git add -A
git commit -m "feat: 描述你的变更"

# 4. 部署（可选）
.\deploy.ps1
```

### 添加新插件

在 `src/plugins/` 下创建 `.py` 文件即可，NoneBot2 会自动加载。

```python
from nonebot import on_command
from nonebot.rule import Rule
from src.plugins.admin import WHITELIST

my_cmd = on_command("mycommand", rule=WHITELIST, priority=10)

@my_cmd.handle()
async def handle_mycommand():
    await my_cmd.finish("Hello!")
```

### Agent 工具扩展

在 `src/plugins/agent/tools.py` 中：

1. 在 `TOOLS` 列表中添加 OpenAI function calling 定义
2. 实现 `_tool_xxx()` 函数
3. 在 `execute_tool()` 中注册分发

## 依赖

- **Python**: ≥3.11
- **包管理**: uv（`uv.lock` 锁定依赖版本）
- **框架**: NoneBot2 + OneBot v11 Adapter
- **AI**: OpenAI SDK → DeepSeek API
- **HTTP**: httpx（视频解析、网页搜索）

## 文档

- [NoneBot2 文档](https://nonebot.dev/)
- [OneBot v11 协议](https://github.com/botuniverse/onebot-11)
- [DeepSeek API 文档](https://platform.deepseek.com/api-docs/)

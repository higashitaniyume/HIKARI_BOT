# HIKARI_BOT

基于 NoneBot2 + OneBot v11 的智能 QQ Bot，集成 DeepSeek AI 对话、媒体解析、群聊记忆、可切换人物技能等功能。

> ⚠️ **免责声明**
>
> 本项目仅用于学习、研究 NoneBot2 / OneBot / AI Agent 的技术原理，**不建议自行部署**。
> 代码中包含大量硬编码配置（如超级管理员 QQ、服务器地址等），直接部署可能导致：
>
> - 与现有 OneBot 服务冲突
> - API 密钥泄露风险
> - 未经授权的消息处理行为
>
> 如果你对 QQ Bot 开发感兴趣，建议参考 [NoneBot2 官方文档](https://nonebot.dev/) 从头搭建自己的 Bot。

## 项目结构

```
HIKARI_BOT/
├── bot.py                    # Bot 入口
├── bump_build.py             # 版本号递增脚本
├── version.json              # 版本信息
├── config.example.json       # 配置模板
├── pyproject.toml            # Python 项目配置（uv 包管理）
├── deploy.ps1                # 部署脚本（Windows PowerShell）
├── README.md
├── CLAUDE.md                 # Claude Code 项目指令
├── prompts/
│   └── hikari.txt            # AI 角色设定（兜底提示词）
├── skills/                   # 可切换人物技能（Claude Code SKILL.md 格式）
│   ├── taffy/
│   │   └── SKILL.md          # 永雏塔菲人格
│   └── user_state.json       # 用户技能状态
├── deploy/
│   └── hikari-bot.service    # systemd 服务文件
└── src/
    ├── core/
    │   ├── config.py         # 统一配置管理 + 技能系统
    │   ├── logger.py         # 日志系统初始化
    │   ├── log_handler.py    # QQ 日志推送处理器
    │   └── message_store.py  # 消息持久化存储
    └── plugins/
        ├── agent/            # AI Agent 插件（核心入口，priority=3）
        │   ├── __init__.py   # 消息入口 + 群聊上下文 + 错误脱敏
        │   ├── client.py     # DeepSeek API 客户端 + Token 估算
        │   ├── memory.py     # 两段式对话记忆（热+冷）
        │   └── tools/        # Function calling 工具（已拆分为 11 个模块）
        ├── ai_chat.py        # 旧版 AI 聊天（/chat 命令 + 私聊兜底）
        ├── skill_manager.py  # 技能管理（/skill /skills 命令）
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
│   ├── private/{uid}_memory.md       # 私聊冷记忆（长期偏好/话题）
│   ├── group/{gid}/{uid}.json        # 群聊热记忆
│   ├── group/{gid}/{uid}_memory.md   # 群聊冷记忆
│   └── group/{gid}/_group.md         # 群共享记忆
├── admin/
│   └── whitelist.json        # 白名单（热读写）
└── messages/                 # 全量消息存储
    ├── private/{uid}.json
    └── group/{gid}.json
```

## 配置

### 配置文件

参考模板见 `config.example.json`：

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
    "skills": {
        "dir": "skills"
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
| `nonebot` | `onebot_v11_ws_urls` | OneBot 正向 WebSocket 地址 |
| `nonebot` | `access_token` | Access Token（OneBot 鉴权） |
| `nonebot` | `log_level` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `deepseek` | `api_key` | DeepSeek API 密钥 |
| `deepseek` | `base_url` | API 端点地址 |
| `deepseek` | `model` | 模型名称（如 `deepseek-chat`） |
| `prompt` | `file` | 兜底系统提示词文件路径 |
| `skills` | `dir` | 技能定义目录（默认 `skills`） |
| `ai_memory` | `max_messages` | 最大保留消息条数 |
| `ai_memory` | `dir` | 记忆存储目录 |
| `whitelist` | `file` | 白名单 JSON 文件路径 |
| `cobalt` | `api` | Cobalt 视频解析 API 地址 |
| `searxng` | `api` | SearXNG 网页搜索 API 地址 |

### 环境变量

| 变量 | 说明 |
|------|------|
| `ENVIRONMENT` | 设为 `prod` 使用 `config.prod.json` |
| `HIKARI_CONFIG_PATH` | 直接指定配置文件路径（优先级最高） |

## 命令列表

### Agent 自动路由（`@机器人` 或私聊任意消息）

AI 会根据用户意图自动选择合适的工具：

- 💬 聊天回复
- 🔍 网页搜索（SearXNG）
- 📥 媒体 URL 解析下载（X/Twitter）
- 📋 查看群成员信息 / QQ 用户名片
- 📝 搜索聊天记录
- 🧠 管理对话记忆
- 📎 发送文件/媒体
- ⚙️ 白名单管理（仅超级管理员）

### 手动命令

| 命令 | 说明 | 权限 |
|------|------|------|
| `/chat <消息>` | 旧版 AI 聊天接口 | 白名单 |
| `/help` 或 `/帮助` | 显示帮助信息 | 无限制 |
| `/clearmemory` | 清除当前会话的 AI 记忆 | 白名单 |
| `/memory` | 查看当前会话的记忆条数 | 白名单 |
| `/skills` | 列出所有可用人物技能 | 白名单 |
| `/skill <name>` | 切换到指定技能 | 白名单 |
| `/skill off` | 恢复默认技能 | 白名单 |
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

## 技能系统

Bot 支持加载 Claude Code **SKILL.md** 格式的人物技能，让 AI 切换不同人格说话。

### 目录结构

```text
skills/
├── taffy/
│   └── SKILL.md          ← 默认技能（永雏塔菲）
└── user_state.json       ← 用户技能状态
```

### SKILL.md 格式

```markdown
---
name: taffy
display_name: 永雏塔菲
description: 像永雏塔菲一样说话
default: true
---

# 角色设定
（正文即系统提示词）
```

### 添加新技能

将 nuwa-skill 生成的 `SKILL.md` 放入 `skills/<技能名>/` 目录即可，重启后自动生效。

Bot 还会自动发现 `.claude/skills/` 目录中的技能（无需手动复制）。

### 使用

```text
/skills          → 列出所有技能
/skill munger    → 切换到查理芒格人格
/skill off       → 恢复默认
```

## 插件说明

### Agent 插件（核心入口，priority=3, block=True）

统一消息入口，通过 OpenAI function calling 让 AI 自行决定如何响应用户。

- **群聊上下文**：自动注入最近消息和群成员映射
- **两段式记忆**：热记忆（最近对话）+ 冷记忆（长期归档 markdown）
- **群共享记忆**：每个群一份独立的共享记忆
- **自动记忆压缩**：Token 预算超限时 AI 自动摘要归档
- **错误信息脱敏**：非超级管理员永远看不到报错细节
- **频率限制**：每用户每会话 5 秒冷却

### 鉴权体系

- **超级管理员**：QQ `3433559280`（硬编码），永远通过所有鉴权
- **白名单**：JSON 文件热读写，修改后无需重启
- **群聊鉴权**：群在白名单中即可使用全部功能

### 安全机制

- **路径安全**：文件/媒体命令拒绝 `..` 路径遍历
- **并发控制**：AI API 最大 3 并发（Semaphore）
- **日志推送**：WARNING 及以上日志自动推送到超级管理员 QQ
- **消息轮转**：单个消息文件超过 5000 条自动归档

## 依赖

- **Python**: ≥3.11
- **包管理**: uv（`uv.lock` 锁定依赖）
- **框架**: NoneBot2 + OneBot v11 Adapter
- **AI**: OpenAI SDK → DeepSeek API
- **HTTP**: httpx（视频解析、网页搜索）

## 参考文档

- [NoneBot2 文档](https://nonebot.dev/)
- [OneBot v11 协议](https://github.com/botuniverse/onebot-11)
- [DeepSeek API 文档](https://platform.deepseek.com/api-docs/)

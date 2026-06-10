# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 递增版本号（每次 commit 前必须执行）
uv run python bump_build.py

# 开发环境运行（读取 config.json）
uv run python bot.py

# 生产环境运行（读取 config.prod.json）
ENVIRONMENT=prod uv run python bot.py

# 指定配置文件
HIKARI_CONFIG_PATH=/path/to/config.json uv run python bot.py

# 安装/同步依赖
uv sync

# 部署到服务器
.\deploy.ps1              # 上传 + uv sync + 重启服务
.\deploy.ps1 -Logs        # 实时日志
.\deploy.ps1 -Status      # 服务状态
```

## Architecture

HIKARI_BOT is a QQ bot built on **NoneBot2** with the **OneBot v11** adapter, connecting to a OneBot service (e.g. NapCat) via **forward WebSocket** (client mode — the bot initiates the connection outbound to the OneBot server).

### Config flow

1. `bot.py` determines which JSON config file to use (`config.json` by default, `config.prod.json` when `ENVIRONMENT=prod`).
2. `src/core/config.py` reads the JSON and exposes module-level constants (`DEEPSEEK_API_KEY`, `COBALT_API`, etc.). Plugin import interface is exactly these constants — changing the config source doesn't touch plugins.
3. `bot.py` injects NoneBot2-required values (`DRIVER`, `ONEBOT_V11_WS_URLS`, etc.) into `os.environ` **before** `nonebot.init()` because NoneBot2 reads them from env vars internally.
4. `ENVIRONMENT=prod` is set in the systemd service file (`deploy/hikari-bot.service`), not in config.

### Plugin system

All plugins live in `src/plugins/` and are auto-loaded by `nonebot.load_plugins("src/plugins")`. Each plugin is a standalone `.py` file.

**Authorization pattern**: Plugins use `WHITELIST` and `SUPER_ADMIN_RULE` from `src/plugins/admin.py` as NoneBot2 `Rule` objects. `SUPER_ADMIN` (QQ `3433559280`) is hardcoded in config and always passes. The whitelist is a JSON file that supports hot-reload (watches mtime).

**Event type isolation is critical**: When a matcher uses `block=True`, its rule must include an event-type check (`IS_GROUP` / `IS_PRIVATE`). Otherwise it will match the wrong event type and block lower-priority handlers from ever seeing the event. Do the isinstance check in the **rule**, not just the handler body.

### Key plugins

| Plugin | Purpose | How triggered |
|--------|---------|---------------|
| `ai_chat.py` | DeepSeek API chat with per-user memory | `/chat`, `@bot` in groups, any message in private |
| `video_parser.py` | Media URL parsing via Cobalt API | Auto-detect supported URLs (currently X/Twitter only) |
| `admin.py` | Whitelist management + auth rules | `/wl` commands (super admin only) |
| `message_collector.py` | Persist all messages to JSON | All messages, priority 80, non-blocking |
| `media_sender.py` | Send image/video/voice via base64 | `/sendimg`, `/sendvideo`, `/sendvoice`, or called by other plugins |
| `file_sender.py` | Send files via OneBot upload API | `/sendfile`, or called by video_parser for large media |
| `hello.py` | Online notification on connect | LifecycleMetaEvent |

### AI chat design (`ai_chat.py`)

- Uses OpenAI SDK pointed at DeepSeek API (`AsyncOpenAI`).
- **Per-user, per-context memory**: private chats and group chats have separate memory files under `data/ai_memory/private/{uid}.json` and `data/ai_memory/group/{gid}/{uid}.json`.
- Memory is trimmed by estimated token count (not message count), oldest (user, assistant) pairs dropped first.
- `MemoryManager` uses per-user `asyncio.Lock` (different users don't block each other) and in-memory cache (60s TTL).
- Rate limiting: 5s cooldown per user per context.
- API concurrency: max 3 simultaneous requests via `asyncio.Semaphore`.
- Retry: up to 3 attempts with exponential backoff for 429/5xx/timeout errors.

### Video parser flow

1. Extract URLs from message text via regex.
2. Check domain against `SUPPORTED_DOMAINS` dict (currently only X/Twitter).
3. POST to Cobalt API for resolution.
4. Response types: `redirect`/`tunnel` (single file), `picker` (multiple media).
5. Size tiering: HEAD check → if >100MB, send link only; if >QQ limit, use `file_sender`; otherwise download and send as base64 media.
6. Failed resolutions send a text error reply; successful ones send media directly (no text).

### Data directory structure

```
data/
├── admin/whitelist.json    # Managed by admin plugin
├── ai_memory/              # Per-user AI conversation history
│   ├── private/{uid}.json
│   └── group/{gid}/{uid}.json
└── messages/               # Collected by message_collector
    ├── private/{uid}.json
    └── group/{gid}.json
```

## Config file format

`config.example.json` is the tracked template. Real `config.json` / `config.prod.json` contain secrets and are **gitignored**. The file maps to `src/core/config.py` with dot-notation nesting:

```json
{
  "nonebot": { "driver", "onebot_v11_ws_urls", "access_token", "log_level" },
  "deepseek": { "api_key", "base_url", "model", "system_prompt" },
  "ai_memory": { "max_messages", "dir" },
  "whitelist": { "file" },
  "cobalt": { "api" }
}
```

## Dependencies

- Package manager: **uv** (not pip). Lock file: `uv.lock`.
- `httpx` is used directly in `video_parser.py` but not declared in `pyproject.toml` — it's pulled in transitively via other deps.
- `openai` SDK is used for DeepSeek API calls.

## Versioning & Commit Policy

**每次完成任务后必须 commit。每次 commit 前必须递增版本号。**

流程：
1. 任务完成后，先运行 `uv run python bump_build.py` 递增 `version.json` 中的 `build` 号。
2. 确认变更无误后，`git add` + `git commit`（commit message 描述本次变更）。
3. `version.json` 会被追踪在 git 中，bot 启动时通过 `hello.py` 向超级管理员 QQ 3433559280 发送版本号。

`version.json` 格式：
```json
{"version": "0.1.0", "build": 42}
```
`get_version()` 返回 `"v0.1.0 (build 42)"`。

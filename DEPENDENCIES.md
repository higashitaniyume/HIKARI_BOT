# HIKARI_BOT 依赖清单

> 生成日期: 2026-06-10
> Python 版本要求: `>=3.10, <4.0`
> 包管理器: uv (基于 `uv.lock`)

---

## 一、直接依赖（pyproject.toml 声明）

| 包名 | 版本约束 | 锁版本 |
|------|----------|--------|
| [nonebot2](https://github.com/nonebot/nonebot2) (含 fastapi 扩展) | `>=2.5.0` | 2.5.0 |
| [nonebot-adapter-onebot](https://github.com/nonebot/adapter-onebot) | `>=2.4.6` | 2.4.6 |
| [nonebot-plugin-docs](https://github.com/nonebot/plugin-docs) | `>=2.5.0` | 2.5.0 |
| [nonebot-plugin-sentry](https://github.com/nonebot/plugin-sentry) | `>=2.0.0` | 2.0.0 |
| [openai](https://github.com/openai/openai-python) | `>=1.0.0` | ⚠️ 未锁定 |

---

## 二、隐式直接依赖（代码中使用但未在 pyproject.toml 声明）

| 包名 | 用途 | 说明 |
|------|------|------|
| [httpx](https://github.com/encode/httpx) | `video_parser.py` 中发起 HTTP 请求到 Cobalt API | Dockerfile 中单独安装，pyproject.toml 未声明 |

---

## 三、全部锁定依赖树（来自 uv.lock）

### 顶层应用
| 包名 | 版本 |
|------|------|
| hikari-bot (本项目) | 0.1.0 |

### NoneBot2 生态
| 包名 | 版本 | 说明 |
|------|------|------|
| nonebot2 | 2.5.0 | 聊天机器人框架 |
| nonebot-adapter-onebot | 2.4.6 | OneBot v11 协议适配器 |
| nonebot-plugin-docs | 2.5.0 | 文档插件 |
| nonebot-plugin-sentry | 2.0.0 | Sentry 错误监控插件 |

### Web 框架 (FastAPI + Uvicorn)
| 包名 | 版本 |
|------|------|
| fastapi | 0.136.3 |
| starlette | 1.2.1 |
| uvicorn | 0.49.0 |
| httptools | 0.8.0 |
| uvloop | 0.22.1 |
| watchfiles | 1.2.0 |
| websockets | 16.0 |

### HTTP / 网络
| 包名 | 版本 |
|------|------|
| h11 | 0.16.0 |
| urllib3 | 2.7.0 |
| certifi | 2026.5.20 |
| idna | 3.18 |
| multidict | 6.7.1 |
| yarl | 1.24.2 |
| propcache | 0.5.2 |

### 数据验证与序列化
| 包名 | 版本 |
|------|------|
| pydantic | 2.13.4 |
| pydantic-core | 2.46.4 |
| msgpack | 1.1.2 |
| annotated-doc | 0.0.4 |
| annotated-types | 0.7.0 |
| typing-extensions | 4.15.0 |
| typing-inspection | 0.4.2 |

### 日志与监控
| 包名 | 版本 |
|------|------|
| loguru | 0.7.3 |
| sentry-sdk | 2.62.0 |

### 配置与工具
| 包名 | 版本 |
|------|------|
| click | 8.4.1 |
| python-dotenv | 1.2.2 |
| pyyaml | 6.0.3 |
| pygtrie | 2.5.0 |
| tomli | 2.4.1 |

### 异步支持
| 包名 | 版本 |
|------|------|
| anyio | 4.13.0 |
| exceptiongroup | 1.3.1 |

### Windows 平台特定
| 包名 | 版本 |
|------|------|
| colorama | 0.4.6 |
| win32-setctime | 1.2.0 |

---

## 四、未锁定依赖（需手动处理）

| 包名 | 用途 | 备注 |
|------|------|------|
| openai | AI 聊天（DeepSeek API） | pyproject.toml 已声明但 uv.lock 未包含 |
| httpx | 视频解析 HTTP 请求 | pyproject.toml 未声明，Dockerfile 中单独安装 |

---

## 五、Docker 部署依赖

- 基础镜像: `python:3.11-slim`
- `deploy/Dockerfile` 安装: `nonebot2[fastapi]>=2.5.0`, `nonebot-adapter-onebot>=2.4.6`, `openai>=1.0.0`, `httpx`
- `docker-compose.yml` 安装: `nonebot2[fastapi]`, `nonebot-adapter-onebot`, `openai`, `httpx`

---

## 六、外部服务依赖

| 服务 | 用途 | 环境变量 |
|------|------|----------|
| DeepSeek API | AI 对话 | `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL` |
| Cobalt API | 视频解析 | `COBALT_API` (默认 `http://192.168.31.2:9000/`) |
| OneBot 客户端 | QQ 消息收发 | 通过局域网连接 (如 `192.168.31.2:54258`) |
| Sentry | 错误监控 | nonebot-plugin-sentry |

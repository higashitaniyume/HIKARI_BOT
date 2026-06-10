# HIKARI_BOT

基于 NoneBot2 + OneBot v11 的 QQ Bot。

## 项目结构

```
HIKARI_BOT/
├── .env              # 开发环境配置
├── .env.prod         # 生产环境配置
├── bot.py            # Bot 入口
└── src/
    └── plugins/
        └── hello.py  # Hello World 插件
```

## 配置

- **协议**: OneBot v11 正向 WebSocket（客户端模式）
- **连接地址**: `ws://192.168.31.2:8082/onebot/v11/ws`
- **Access Token**: 已配置在 `.env` / `.env.prod` 中

## 如何运行

```bash
# 开发环境
python bot.py

# 生产环境 (自动加载 .env.prod)
ENVIRONMENT=prod python bot.py
```

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

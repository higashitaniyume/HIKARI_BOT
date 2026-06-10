"""统一配置管理 —— 从环境变量读取所有配置项。

NoneBot2 启动时自动加载 .env 文件到 os.environ，
因此直接用 os.getenv() 读取即可。
"""

import os

# ============================================================================
# 超级管理员（硬编码）
# ============================================================================

SUPER_ADMIN: int = 3433559280

# ============================================================================
# DeepSeek API
# ============================================================================

DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_SYSTEM_PROMPT: str = os.getenv(
    "DEEPSEEK_SYSTEM_PROMPT",
    "你是一个可爱的QQ机器人，名叫HIKARI。请用中文回复，语气活泼可爱。",
)

# ============================================================================
# AI 记忆
# ============================================================================

# 每个用户/群内用户最多保留的消息条数（不含 system prompt）
MAX_MEMORY_MESSAGES: int = int(os.getenv("MAX_MEMORY_MESSAGES", "20"))

# 记忆存储目录
AI_MEMORY_DIR: str = os.getenv("AI_MEMORY_DIR", "data/ai_memory")

# ============================================================================
# 管理员 / 白名单
# ============================================================================

WHITELIST_FILE: str = os.getenv("WHITELIST_FILE", "data/admin/whitelist.json")

# ============================================================================
# Cobalt 视频解析
# ============================================================================

COBALT_API: str = os.getenv("COBALT_API", "http://192.168.31.2:9000/")

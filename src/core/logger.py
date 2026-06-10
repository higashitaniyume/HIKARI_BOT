"""日志系统 —— 每日午夜自动轮转日志文件，保留最近 30 天。

使用方式：
    from src.core.logger import setup_logging
    setup_logging()          # 在 nonebot.init() 之前调用

    import logging
    logger = logging.getLogger("hikari.xxx")
    logger.info("...")
"""

import logging
import logging.handlers
from pathlib import Path
from datetime import datetime


# 日志格式：时间 | 级别 | 模块 | 消息
LOG_FORMAT = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logging(log_dir: str = "logs", *, level: int = logging.DEBUG) -> None:
    """初始化日志系统。

    - 控制台输出（INFO 及以上）
    - 文件输出（DEBUG 及以上），每日午夜轮转，保留 30 天

    Args:
        log_dir: 日志文件存放目录，默认为项目根目录下的 logs/
        level: 日志级别，默认 DEBUG
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # ─── 根 logger 配置 ───────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有 handler（避免重复添加）
    root.handlers.clear()

    # ─── 控制台 handler ───────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(LOG_FORMAT)
    root.addHandler(console)

    # ─── 文件 handler（每日轮转） ────────────────────────────────
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_path / "HIKARI_BOT.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(LOG_FORMAT)
    # 轮转后的文件后缀为 .YYYY-MM-DD
    file_handler.suffix = "%Y-%m-%d"
    root.addHandler(file_handler)

    # ─── 降低第三方库日志噪音 ───────────────────────────────────
    for lib in ("websockets", "httpx", "httpcore", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    # ─── 启动标记 ──────────────────────────────────────────────
    boot_logger = logging.getLogger("hikari")
    boot_logger.info("━" * 50)
    boot_logger.info("HIKARI_BOT 日志系统已就绪")
    boot_logger.info(f"日志目录: {log_path.resolve()}")
    boot_logger.info(f"当前文件: HIKARI_BOT_{datetime.now().strftime('%Y-%m-%d')}.log")
    boot_logger.info("━" * 50)

"""日志系统 —— 按日期分文件写入，启动时自动清理超过 30 天的旧日志。

使用方式：
    from src.core.logger import setup_logging
    setup_logging()          # 在 nonebot.init() 之前调用

    import logging
    logger = logging.getLogger("hikari.xxx")
    logger.info("...")
"""

import logging
import logging.handlers
import re
from pathlib import Path
from datetime import datetime, timedelta


# 日志格式：时间 | 级别 | 模块 | 消息
LOG_FORMAT = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 日志文件名匹配模式
_LOG_PATTERN = re.compile(r"HIKARI_BOT_(\d{4}-\d{2}-\d{2})\.log")


def _cleanup_old_logs(log_dir: Path, max_days: int = 30) -> None:
    """删除超过 max_days 天的旧日志文件。"""
    cutoff = datetime.now() - timedelta(days=max_days)
    for f in log_dir.iterdir():
        m = _LOG_PATTERN.match(f.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d")
            if file_date < cutoff:
                f.unlink()
        except ValueError:
            pass


def setup_logging(log_dir: str = "logs", *, level: int = logging.DEBUG) -> None:
    """初始化日志系统。

    - 控制台输出（INFO 及以上）
    - 文件输出（DEBUG 及以上），按日期分文件，保留 30 天
    - 每次启动时清理过期日志

    Args:
        log_dir: 日志文件存放目录，默认为项目根目录下的 logs/
        level: 日志级别，默认 DEBUG
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # ─── 清理旧日志 ──────────────────────────────────────────
    _cleanup_old_logs(log_path)

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

    # ─── 文件 handler（每日轮转 + 启动时补轮转）─────────────────────
    main_log = log_path / "HIKARI_BOT.log"

    # 启动时检查：把上次遗留的日志重命名为日期文件
    if main_log.exists():
        mtime = datetime.fromtimestamp(main_log.stat().st_mtime)
        old_date = mtime.strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        if old_date != today:
            archive = log_path / f"HIKARI_BOT_{old_date}.log"
            main_log.rename(archive)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(main_log),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(LOG_FORMAT)
    # 禁用自动添加的 .YYYY-MM-DD 后缀（我们自己管理）
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
    boot_logger.info(f"当前文件: {log_file.name}")
    boot_logger.info("━" * 50)

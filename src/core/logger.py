"""日志系统 —— 按日期自动分文件、保留最近 30 天。

每次写日志前检查当前日期：
    - 日期变了 → 自动切到新文件 HIKARI_BOT_YYYY-MM-DD.log
    - 启动时 → 清理超过 30 天的旧文件
"""

import logging
import re
from pathlib import Path
from datetime import datetime, timedelta


LOG_FORMAT = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_LOG_PATTERN = re.compile(r"HIKARI_BOT_(\d{4}-\d{2}-\d{2})\.log")


def _cleanup_old_logs(log_dir: Path, max_days: int = 30) -> None:
    cutoff = datetime.now() - timedelta(days=max_days)
    for f in log_dir.iterdir():
        m = _LOG_PATTERN.match(f.name)
        if not m:
            continue
        try:
            if datetime.strptime(m.group(1), "%Y-%m-%d") < cutoff:
                f.unlink()
        except ValueError:
            pass


class DailyRotatingFileHandler(logging.Handler):
    """每次 emit 时检查日期，跨天自动切换到新文件。"""

    def __init__(self, log_dir: Path, encoding: str = "utf-8"):
        super().__init__()
        self._log_dir = log_dir
        self._encoding = encoding
        self._current_date: str = ""
        self._file = None
        self._open_for_today()

    def _open_for_today(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today == self._current_date:
            return
        # 关旧文件
        if self._file:
            self._file.close()
        # 开新文件
        self._current_date = today
        path = self._log_dir / f"HIKARI_BOT_{today}.log"
        self._file = open(path, "a", encoding=self._encoding)
        self._file_path = str(path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._open_for_today()
            self._file.write(self.format(record) + "\n")
            self._file.flush()
        except Exception:
            pass  # 日志写失败不能崩 bot

    def close(self) -> None:
        if self._file:
            self._file.close()
        super().close()


def setup_logging(log_dir: str = "logs", *, level: int = logging.DEBUG) -> None:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 清理旧日志
    _cleanup_old_logs(log_path)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # 控制台
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(LOG_FORMAT)
    root.addHandler(console)

    # 文件（按日自动分割）
    file_handler = DailyRotatingFileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(LOG_FORMAT)
    root.addHandler(file_handler)

    for lib in ("websockets", "httpx", "httpcore", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    today = datetime.now().strftime("%Y-%m-%d")
    boot_logger = logging.getLogger("hikari")
    boot_logger.info("━" * 50)
    boot_logger.info("HIKARI_BOT 日志系统已就绪")
    boot_logger.info(f"日志目录: {log_path.resolve()}")
    boot_logger.info(f"当前文件: HIKARI_BOT_{today}.log")
    boot_logger.info("━" * 50)

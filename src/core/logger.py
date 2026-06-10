"""日志系统 —— 按日期自动分文件、按大小兜底分割、单独错误日志。

三层输出：
    控制台  → INFO 及以上（简洁格式）
    主文件  → DEBUG 及以上（详细格式，含文件名+行号）
    错误文件 → WARNING 及以上（方便快速排查）

轮转：
    主轮转：每次 emit 检查日期，跨天切 HIKARI_BOT_YYYY-MM-DD.log
    兜底：文件超过 50MB 自动切 *.log.1 / *.log.2 ...
    启动：清理 30 天前的旧日志
"""

import logging
import re
from pathlib import Path
from datetime import datetime, timedelta


# ── 格式 ─────────────────────────────────────────────────

# 控制台格式：简洁
_CONSOLE_FORMAT = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 文件格式：详细（含文件名、函数名、行号）
_FILE_FORMAT = logging.Formatter(
    fmt="%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | "
        "%(filename)s:%(lineno)d %(funcName)s() | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_LOG_PATTERN = re.compile(r"HIKARI_BOT_(\d{4}-\d{2}-\d{2})\.log")

# 单文件最大字节数（兜底轮转）
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


# ── 旧文件清理 ───────────────────────────────────────────

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


# ── 日期轮转 + 大小兜底 Handler ───────────────────────────

class DailyRotatingFileHandler(logging.Handler):
    """每次 emit 检查日期（主）+ 文件大小（兜底），自动切换。"""

    def __init__(self, log_dir: Path, suffix: str = "", encoding: str = "utf-8"):
        super().__init__()
        self._log_dir = log_dir
        self._suffix = suffix  # 如 "_error"
        self._encoding = encoding
        self._current_date: str = ""
        self._file = None
        self._open_for_today()

    def _today_filename(self) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return f"HIKARI_BOT{self._suffix}_{today}.log"

    def _open_for_today(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today == self._current_date:
            # 大小兜底
            if self._file and self._file.tell() > _MAX_FILE_BYTES:
                self._file.close()
                path = self._log_dir / self._today_filename()
                self._rotate_by_size(path)
                self._file = open(path, "a", encoding=self._encoding)
            return
        if self._file:
            self._file.close()
        self._current_date = today
        path = self._log_dir / self._today_filename()
        self._file = open(path, "a", encoding=self._encoding)

    @staticmethod
    def _rotate_by_size(path: Path) -> None:
        """文件过大时，旋转 *.log → *.log.1 → *.log.2 ...（最多保留 3 个）。"""
        max_backup = 3
        for i in range(max_backup, 0, -1):
            old = Path(str(path) + f".{i}")
            older = Path(str(path) + f".{i + 1}")
            if i == max_backup and older.exists():
                older.unlink()
            if old.exists():
                old.rename(older)
        if path.exists():
            path.rename(Path(str(path) + ".1"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._open_for_today()
            self._file.write(self.format(record) + "\n")
            self._file.flush()
        except Exception:
            pass

    def close(self) -> None:
        if self._file:
            self._file.close()
        super().close()


# ── 初始化 ───────────────────────────────────────────────

def setup_logging(log_dir: str = "logs", *, level: int = logging.DEBUG) -> None:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    _cleanup_old_logs(log_path)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # 控制台（INFO+，简洁）
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(_CONSOLE_FORMAT)
    root.addHandler(console)

    # 主日志文件（DEBUG+，详细，按日+按大小轮转）
    main_file = DailyRotatingFileHandler(log_path)
    main_file.setLevel(logging.DEBUG)
    main_file.setFormatter(_FILE_FORMAT)
    root.addHandler(main_file)

    # 错误日志文件（WARNING+，独立文件，方便快速排查）
    error_file = DailyRotatingFileHandler(log_path, suffix="_error")
    error_file.setLevel(logging.WARNING)
    error_file.setFormatter(_FILE_FORMAT)
    root.addHandler(error_file)

    # 降低第三方库噪音
    for lib in ("websockets", "httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    # NoneBot 框架本身保持 INFO（可以看到事件处理流程）
    logging.getLogger("nonebot").setLevel(logging.INFO)

    today = datetime.now().strftime("%Y-%m-%d")
    boot = logging.getLogger("hikari")
    boot.info("━" * 50)
    boot.info("HIKARI_BOT 日志系统已就绪 (level=%s)", logging.getLevelName(level))
    boot.info("日志目录: %s", log_path.resolve())
    boot.info("主日志: HIKARI_BOT_%s.log", today)
    boot.info("错误日志: HIKARI_BOT_error_%s.log", today)
    boot.info("━" * 50)

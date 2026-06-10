"""QQ 日志处理器 —— 将 WARNING 及以上级别的日志发送到超级管理员 QQ。

特性：
    - 线程安全：使用 threading.Lock 桥接同步日志 emit 和异步发送
    - 频率限制：每分钟最多 10 条
    - 去重：2 秒内相同消息不重复发送
    - 防递归：发送日志时产生的日志不会再次触发发送
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

from nonebot.adapters.onebot.v11 import Bot

logger = logging.getLogger("hikari.core.log_handler")


class QQLogHandler(logging.Handler):
    """将指定级别以上的日志通过 QQ 私聊发送给超级管理员。"""

    # 频率限制常量
    _MAX_PER_MINUTE = 10
    _DEDUP_WINDOW = 2.0  # 秒
    _DRAIN_INTERVAL = 0.5  # 秒

    def __init__(self, target_qq: int, level: int = logging.WARNING):
        super().__init__(level)
        self.target_qq = target_qq

        # 线程安全的消息缓冲
        self._pending: list[str] = []
        self._lock = threading.Lock()

        # Bot 引用和后台任务
        self._bot: Optional[Bot] = None
        self._task: Optional[asyncio.Task] = None
        self._started = False

        # 频率限制 / 去重
        self._sent_times: list[float] = []
        self._last_msg = ""
        self._last_msg_time = 0.0

        # 防递归标志
        self._sending = False

        self.setFormatter(logging.Formatter(
            fmt="[%(levelname)s] %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        ))

    # ── 公开 API ────────────────────────────────────────────────

    def set_bot(self, bot: Bot) -> None:
        """绑定 Bot 实例并启动后台发送任务。"""
        self._bot = bot
        if not self._started:
            self._started = True
            self._task = asyncio.create_task(self._drain())
            logger.info(f"QQ 日志处理器已启动 → QQ {self.target_qq}")

    # ── logging.Handler 接口 ────────────────────────────────────

    def emit(self, record: logging.LogRecord) -> None:
        """接收日志记录（同步，任意线程调用）。"""
        try:
            msg = self.format(record)
            now = time.monotonic()
            with self._lock:
                # 防递归：如果在发送中，丢弃
                if self._sending:
                    return
                # 去重
                if msg == self._last_msg and now - self._last_msg_time < self._DEDUP_WINDOW:
                    return
                self._last_msg = msg
                self._last_msg_time = now
                self._pending.append(msg)
        except Exception:
            pass  # 日志处理器自身错误不能影响主流程

    # ── 异步后台发送 ────────────────────────────────────────────

    async def _drain(self) -> None:
        """后台任务：定期从缓冲队列取出日志并发送。"""
        while True:
            try:
                with self._lock:
                    if self._pending:
                        msgs = self._pending[:]
                        self._pending.clear()
                    else:
                        msgs = []

                for msg in msgs:
                    await self._send(msg)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

            await asyncio.sleep(self._DRAIN_INTERVAL)

    async def _send(self, msg: str) -> None:
        """发送单条日志（含频率限制）。"""
        now = time.monotonic()

        # 频率限制
        self._sent_times = [t for t in self._sent_times if now - t < 60]
        if len(self._sent_times) >= self._MAX_PER_MINUTE:
            return
        self._sent_times.append(now)

        if not self._bot:
            return

        self._sending = True
        try:
            await self._bot.send_private_msg(
                user_id=self.target_qq,
                message=f"⚠ {msg}",
            )
        except Exception:
            pass
        finally:
            self._sending = False

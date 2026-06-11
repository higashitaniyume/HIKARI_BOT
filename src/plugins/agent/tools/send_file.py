"""媒体/文件发送工具（含路径安全校验）。"""

import logging
from pathlib import Path
from typing import Optional

from nonebot.adapters.onebot.v11 import Bot

from src.plugins.file_sender import get_file_sender

logger = logging.getLogger("hikari.plugins.agent")

_ALLOWED_DIRS: list[Path] = []


def _get_allowed_dirs() -> list[Path]:
    global _ALLOWED_DIRS
    if not _ALLOWED_DIRS:
        root = Path(__file__).resolve().parent.parent.parent.parent.parent.resolve()
        candidates = [root / "downloads", root / "data" / "media", root / "data" / "files"]
        _ALLOWED_DIRS = [d.resolve() if d.exists() else d for d in candidates]
        if not _ALLOWED_DIRS:
            (root / "downloads").mkdir(parents=True, exist_ok=True)
            _ALLOWED_DIRS = [root / "downloads"]
    return _ALLOWED_DIRS


def _resolve_safe_path(user_path: str) -> Optional[Path]:
    if not user_path or not user_path.strip():
        return None
    try:
        raw = Path(user_path)
        if ".." in user_path.replace("\\", "/").split("/"):
            return None
        root = Path(__file__).resolve().parent.parent.parent.parent.parent.resolve()
        resolved = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        for allowed in _get_allowed_dirs():
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue
        return None
    except (OSError, ValueError):
        return None


async def tool_send_media_or_file(bot: Bot, path_or_url: str, target: str) -> str:
    """发送媒体或文件（仅允许安全路径和 http(s) URL）。"""
    from urllib.parse import urlparse
    from src.plugins.media_sender import get_media_sender

    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        f_sender = get_file_sender()
        url_name = Path(urlparse(path_or_url).path).name or "file"
        ok = await f_sender.send_file(bot, target=target,
                                      file_path_or_url=path_or_url, filename=url_name)
        return f"✅ 文件已发送: {url_name} → {target}" if ok else f"❌ 文件发送失败: {url_name}"

    resolved = _resolve_safe_path(path_or_url)
    if resolved is None:
        logger.warning(f"拒绝不安全路径: {path_or_url}")
        return "❌ 安全限制：不允许访问该路径。请将文件放到 downloads/ 目录下再试。"

    if not resolved.exists():
        return f"❌ 文件不存在: {path_or_url}"

    # 拒绝过大文件（避免内存耗尽）
    MAX_BYTES_SEND = 50 * 1024 * 1024  # 50 MB
    try:
        fsize = resolved.stat().st_size
        if fsize > MAX_BYTES_SEND:
            size_mb = fsize / (1024 * 1024)
            return f"❌ 文件过大 ({size_mb:.1f} MB)，请改用 /sendfile 命令"
    except OSError as e:
        return f"❌ 无法读取文件信息: {e}"

    sender = get_media_sender()
    try:
        await sender.send_bytes(bot, target=target, data=resolved.read_bytes(), filename=resolved.name)
        return f"✅ 已发送: {resolved.name} → {target}"
    except Exception as e:
        f_sender = get_file_sender()
        ok = await f_sender.send_file(bot, target=target, file_path_or_url=str(resolved), filename=resolved.name)
        return f"✅ 文件已发送: {resolved.name} → {target}" if ok else f"❌ 发送失败: {e}"

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

LOGGER = logging.getLogger("minecraft_player_migrator.gui")


def configure_logging() -> Path:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "MinecraftPlayerMigrator" / "logs",
        Path.cwd() / "logs",
        Path(tempfile.gettempdir()) / "MinecraftPlayerMigrator" / "logs",
    ]
    if LOGGER.handlers:
        return Path(getattr(LOGGER.handlers[0], "baseFilename", candidates[0] / "minecraft_player_migrator.log"))
    for log_dir in candidates:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "minecraft_player_migrator.log"
            handler = logging.FileHandler(log_path, encoding="utf-8")
        except OSError:
            continue
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.DEBUG)
        LOGGER.propagate = False
        return log_path
    return Path(tempfile.gettempdir()) / "MinecraftPlayerMigrator.log"


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "game process is still running" in lowered or (
        "minecraft" in lowered and "running" in lowered
    ):
        return "Minecraft 正在运行。\n\n请先关闭 Minecraft 后再重试。"
    if "insufficient free space" in lowered:
        return "磁盘空间不足，无法创建完整 world 备份。"
    if isinstance(exc, PermissionError):
        return "没有足够的文件权限。\n\n请关闭占用存档的程序，或选择有写入权限的目录。"
    if isinstance(exc, FileNotFoundError):
        return "找不到需要的存档文件。\n\n请确认选择的是有效的 world 文件夹或服务器 ZIP。"
    if "zip" in lowered or "unsafe" in lowered:
        return "无法安全解析服务器 ZIP。\n\nZIP 可能损坏，或包含不安全的路径。"
    if "uuid" in lowered or "player" in lowered or "nbt" in lowered:
        return "无法解析服务器人物数据。\n\n请在高级模式中查看详细日志。"
    if "version" in lowered or "版本" in lowered or "mismatch" in lowered:
        return "来源与目标 Minecraft 版本不兼容，已阻止迁移。"
    return "操作失败。\n\n请检查路径、权限和日志中的详细信息。"


def error_code(exc: Exception) -> str:
    """Return a stable user-facing code while keeping traceback details in logs."""

    text = str(exc).lower()
    if "running" in text and "game" in text or "minecraft" in text and "运行" in text:
        return "GAME_RUNNING"
    if "insufficient free space" in text or "磁盘空间" in text:
        return "DISK_SPACE"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, FileNotFoundError):
        return "WORLD_NOT_FOUND"
    if "zip" in text or "unsafe" in text:
        return "ZIP_INVALID"
    if "version" in text or "版本" in text or "mismatch" in text:
        return "VERSION_BLOCKED"
    if "uuid" in text:
        return "UUID_INVALID"
    if "nbt" in text or "player" in text:
        return "NBT_INVALID"
    return "MIGRATION_FAILED"

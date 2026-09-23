from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def ensure_free_space(path: Path, required_bytes: int, multiplier: float = 1.2) -> None:
    usage = shutil.disk_usage(path)
    if usage.free < int(required_bytes * multiplier):
        raise OSError(
            f"Insufficient free space: need at least {int(required_bytes * multiplier)} bytes, "
            f"available {usage.free} bytes"
        )


def make_unique_backup_path(
    world: Path, prefix: str = "world_backup_before_player_migration"
) -> Path:
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d_%H%M%S")
    parent = world.parent
    candidate = parent / f"{prefix}_{stamp}"
    index = 1
    while candidate.exists():
        candidate = parent / f"{prefix}_{stamp}_{index:02d}"
        index += 1
    return candidate


def create_full_backup(
    world: Path,
    destination: Path | None = None,
    *,
    prefix: str = "world_backup_before_player_migration",
) -> Path:
    world = world.resolve()
    if not world.is_dir():
        raise FileNotFoundError(world)
    destination = destination or make_unique_backup_path(world, prefix)
    if destination.exists():
        raise FileExistsError(destination)
    ensure_free_space(world, directory_size(world))
    shutil.copytree(world, destination, copy_function=shutil.copy2, symlinks=False)
    return destination


def backup_file(path: Path, suffix: str) -> Path:
    destination = path.with_name(path.name + suffix)
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copy2(path, destination)
    return destination


def atomic_write(path: Path, data: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass

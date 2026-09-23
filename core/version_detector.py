from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .nbt_codec import load_nbt_bytes, nbt_root
from .zip_reader import WorldSource, resolve_world_source


class VersionStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class MinecraftVersionInfo:
    display_version: str | None = None
    data_version: int | None = None
    source: str = "unknown"
    confidence: str = "low"
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings))


@dataclass(frozen=True, slots=True)
class VersionCompatibility:
    status: VersionStatus
    source: MinecraftVersionInfo
    target: MinecraftVersionInfo
    warnings: tuple[str, ...] = ()

    @property
    def is_match(self) -> bool:
        return self.status is VersionStatus.MATCH


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def detect_world_version(world_or_source: WorldSource | str | Path) -> MinecraftVersionInfo:
    """Read level.dat Data.Version.Name first and DataVersion second.

    A missing or malformed version is deliberately represented as UNKNOWN;
    callers must not guess a compatible version from a filename or folder name.
    """

    try:
        if isinstance(world_or_source, WorldSource):
            source = world_or_source
        else:
            candidate = Path(world_or_source).expanduser().resolve()
            # Version detection only needs level.dat.  Keeping this independent
            # from playerdata also lets the detector diagnose incomplete worlds.
            if candidate.is_dir() and (candidate / "level.dat").is_file():
                source = WorldSource(candidate)
            else:
                source = resolve_world_source(candidate)
    except FileNotFoundError as exc:
        return MinecraftVersionInfo(
            source="level.dat",
            confidence="low",
            warnings=(f"无法读取 Minecraft 版本：{exc}",),
        )
    warnings: list[str] = []
    try:
        parsed = load_nbt_bytes(source.read_bytes("level.dat"))
        root = nbt_root(parsed)
        data = root.get("Data")
        if not isinstance(data, dict):
            raise TypeError("level.dat does not contain Data")
        version = data.get("Version")
        display_version = None
        if isinstance(version, dict):
            display_version = _as_text(version.get("Name"))
        data_version = _as_int(data.get("DataVersion"))
        if display_version:
            confidence = "high" if data_version is not None else "medium"
            source_name = "level.dat -> Data -> Version -> Name"
        elif data_version is not None:
            confidence = "medium"
            source_name = "level.dat -> Data -> DataVersion"
            warnings.append("level.dat 缺少 Version.Name，使用 DataVersion 辅助判断")
        else:
            confidence = "low"
            source_name = "level.dat"
            warnings.append("无法从 level.dat 识别 Minecraft 版本")
        return MinecraftVersionInfo(
            display_version=display_version,
            data_version=data_version,
            source=source_name,
            confidence=confidence,
            warnings=tuple(warnings),
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return MinecraftVersionInfo(
            source="level.dat",
            confidence="low",
            warnings=(f"无法读取 Minecraft 版本：{exc}",),
        )


def compare_minecraft_versions(
    source: MinecraftVersionInfo | WorldSource | str | Path,
    target: MinecraftVersionInfo | WorldSource | str | Path,
) -> VersionCompatibility:
    """Compare two version records without guessing missing information."""

    source_info = source if isinstance(source, MinecraftVersionInfo) else detect_world_version(source)
    target_info = target if isinstance(target, MinecraftVersionInfo) else detect_world_version(target)
    warnings = tuple(dict.fromkeys((*source_info.warnings, *target_info.warnings)))

    source_name = source_info.display_version
    target_name = target_info.display_version
    source_data = source_info.data_version
    target_data = target_info.data_version
    if source_name is not None and target_name is not None:
        if source_name != target_name:
            status = VersionStatus.MISMATCH
        elif source_data is not None and target_data is not None and source_data != target_data:
            status = VersionStatus.CONFLICT
        else:
            status = VersionStatus.MATCH
    elif source_name is None and target_name is None and source_data is not None and target_data is not None:
        status = VersionStatus.MATCH if source_data == target_data else VersionStatus.MISMATCH
    else:
        status = VersionStatus.UNKNOWN
    return VersionCompatibility(status, source_info, target_info, warnings)

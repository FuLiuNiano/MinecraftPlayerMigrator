from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ItemPreview:
    slot: int | None
    item_id: str
    count: int


@dataclass(slots=True)
class PlayerInfo:
    uuid: str
    username: str | None
    source_file: str
    inventory_count: int
    inventory_preview: list[ItemPreview] = field(default_factory=list)
    xp_level: int | None = None
    xp_total: int | None = None
    position: tuple[float, float, float] | None = None
    dimension: str | None = None
    health: float | None = None
    ender_items_count: int = 0
    has_forge_caps: bool = False
    has_curios: bool = False
    raw: Any = None


@dataclass(slots=True)
class IdentityEvidence:
    uuid: str
    level_dat_match: bool = False
    recent_playerdata_match: bool = False
    launcher_arg_match: bool = False
    usercache_match: bool = False
    username: str | None = None
    details: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(
            (
                self.level_dat_match,
                self.recent_playerdata_match,
                self.launcher_arg_match,
                self.usercache_match,
            )
        )

    @property
    def confidence(self) -> str:
        if self.score >= 3:
            return "高"
        if self.score == 2:
            return "中"
        return "低"

    @property
    def evidence_labels(self) -> list[str]:
        labels: list[str] = []
        if self.level_dat_match:
            labels.append("level.dat")
        if self.recent_playerdata_match:
            labels.append("最近 playerdata")
        if self.launcher_arg_match:
            labels.append("启动参数")
        if self.usercache_match:
            labels.append("usernamecache/usercache")
        return labels


@dataclass(slots=True)
class MigrationPlan:
    source_world: Any
    target_world: Path
    source_uuid: str
    target_uuid: str
    files_to_modify: tuple[str, ...] = (
        "playerdata/<target_uuid>.dat",
        "level.dat",
    )


@dataclass(slots=True)
class VerificationResult:
    target_uuid: str
    inventory_count: int
    xp_level: int | None
    xp_total: int | None
    position: tuple[float, float, float] | None
    has_forge_caps: bool
    has_curios: bool
    playerdata_leveldat_equal: bool
    required_items_present: dict[str, bool] = field(default_factory=dict)

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .models import IdentityEvidence
from .version_detector import MinecraftVersionInfo, VersionStatus


class ModuleStatus(StrEnum):
    """Status shared by compatibility scanning, planning, and verification."""

    SUCCESS = "SUCCESS"
    SKIPPED_NOT_FOUND = "SKIPPED_NOT_FOUND"
    SKIPPED_UNSUPPORTED = "SKIPPED_UNSUPPORTED"
    FAILED = "FAILED"
    READY = "READY"
    BLOCKED = "BLOCKED"


@dataclass(slots=True)
class ModuleReport:
    name: str
    display_name: str
    status: ModuleStatus = ModuleStatus.READY
    source_paths: tuple[str, ...] = ()
    target_paths: tuple[str, ...] = ()
    counts: dict[str, int | float | str | bool] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error: str | None = None
    will_overwrite: bool = False
    enabled: bool = True
    optional: bool = True

    def __post_init__(self) -> None:
        self.source_paths = tuple(str(path) for path in self.source_paths)
        self.target_paths = tuple(str(path) for path in self.target_paths)
        self.warnings = tuple(str(warning) for warning in self.warnings)
        self.counts = dict(self.counts)

    @property
    def available(self) -> bool:
        return self.status in {ModuleStatus.READY, ModuleStatus.SUCCESS}


@dataclass(slots=True)
class CompatibilityReport:
    source_label: str
    target_world: Path
    source_uuid: str
    target_uuid: str
    source_username: str | None = None
    target_username: str | None = None
    identity_evidence: tuple[IdentityEvidence, ...] = ()
    modules: dict[str, ModuleReport] = field(default_factory=dict)
    unsupported: tuple[str, ...] = ()
    blocking_issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    world_size: int = 0
    free_space: int = 0
    required_backup_space: int = 0
    source_version: MinecraftVersionInfo | None = None
    target_version: MinecraftVersionInfo | None = None
    version_status: VersionStatus | None = None
    # The scanner is read-only, but the resolved source is needed by the plan
    # builder.  It is intentionally kept out of UI-facing serialization.
    source: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.target_world = Path(self.target_world)
        self.identity_evidence = tuple(self.identity_evidence)
        self.modules = dict(self.modules)
        self.unsupported = tuple(str(item) for item in self.unsupported)
        self.blocking_issues = tuple(str(item) for item in self.blocking_issues)
        self.warnings = tuple(str(item) for item in self.warnings)

    @property
    def can_migrate(self) -> bool:
        player = self.modules.get("player")
        return (
            self.version_status is VersionStatus.MATCH
            and not self.blocking_issues
            and player is not None
            and player.available
        )

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings) or any(module.warnings for module in self.modules.values())

    @property
    def enabled_modules(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, module in self.modules.items()
            if module.enabled and module.available
        )


@dataclass(slots=True)
class FullMigrationOptions:
    player: bool = True
    advancements: bool = True
    stats: bool = True
    ftb_quests: bool = True
    ftb_team: bool = True
    waystones: bool = True
    endinglib: bool = True
    cosarmor: bool = True

    def __post_init__(self) -> None:
        if not self.player:
            raise ValueError("Player migration is mandatory and cannot be disabled")

    def enabled(self, module_name: str) -> bool:
        if not hasattr(self, module_name):
            raise KeyError(f"Unknown migration module: {module_name}")
        return bool(getattr(self, module_name))


@dataclass(slots=True)
class FullMigrationPlan:
    source: Any
    target_world: Path
    source_uuid: str
    target_uuid: str
    source_username: str | None
    target_username: str | None
    options: FullMigrationOptions
    compatibility_report: CompatibilityReport
    planned_files: tuple[Path, ...] = ()
    overwrite_files: tuple[Path, ...] = ()
    new_files: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()
    backup_prefix: str = "world_backup_before_full_migration"

    def __post_init__(self) -> None:
        self.target_world = Path(self.target_world)
        self.planned_files = tuple(Path(path) for path in self.planned_files)
        self.overwrite_files = tuple(Path(path) for path in self.overwrite_files)
        self.new_files = tuple(Path(path) for path in self.new_files)
        self.warnings = tuple(str(warning) for warning in self.warnings)


@dataclass(slots=True)
class FullVerificationResult:
    player_ok: bool = False
    player_leveldat_equal: bool = False
    advancements_ok: bool = False
    stats_ok: bool = False
    ftb_quests_ok: bool = False
    ftb_team_ok: bool = False
    waystones_ok: bool = False
    endinglib_ok: bool = False
    cosarmor_ok: bool = False
    source_identity_residuals: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.source_identity_residuals = tuple(self.source_identity_residuals)
        self.errors = tuple(self.errors)
        self.warnings = tuple(self.warnings)

    @property
    def success(self) -> bool:
        return not self.errors and all(
            (
                self.player_ok,
                self.player_leveldat_equal,
                self.advancements_ok,
                self.stats_ok,
                self.ftb_quests_ok,
                self.ftb_team_ok,
                self.waystones_ok,
                self.endinglib_ok,
                self.cosarmor_ok,
            )
        )


@dataclass(slots=True)
class FullMigrationResult:
    success: bool
    backup_path: Path | None = None
    modified_files: tuple[Path, ...] = ()
    created_files: tuple[Path, ...] = ()
    module_results: tuple[ModuleReport, ...] = ()
    verification: FullVerificationResult = field(default_factory=FullVerificationResult)
    rollback_performed: bool = False
    rollback_success: bool = False
    log_path: Path | None = None

    def __post_init__(self) -> None:
        self.modified_files = tuple(Path(path) for path in self.modified_files)
        self.created_files = tuple(Path(path) for path in self.created_files)
        self.module_results = tuple(self.module_results)

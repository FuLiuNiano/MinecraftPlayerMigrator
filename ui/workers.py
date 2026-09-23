from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.compatibility_scanner import scan_compatibility
from core.full_migration import execute_full_migration
from core.full_models import FullMigrationPlan
from core.identity_detector import detect_local_identities
from core.player_scanner import load_name_cache, load_source_names, scan_players
from core.zip_reader import WorldSource, resolve_world_source


class ScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(object)

    def __init__(
        self, source_path: Path, target_path: Path | None, instance_path: Path | None
    ) -> None:
        super().__init__()
        self.source_path = source_path
        self.target_path = target_path
        self.instance_path = instance_path

    def run(self) -> None:
        try:
            source = resolve_world_source(self.source_path)
            names = load_source_names(source)
            names.update(load_name_cache(self.instance_path))
            players = scan_players(source, names)
            evidence = (
                detect_local_identities(self.target_path, self.instance_path)
                if self.target_path is not None
                else []
            )
            self.completed.emit((source, players, evidence))
        except Exception as exc:  # noqa: BLE001 - worker boundary forwards to GUI
            self.failed.emit(exc)


class CompatibilityWorker(QThread):
    """Build the read-only v1.1 compatibility report off the GUI thread."""

    completed = Signal(object)
    failed = Signal(object)

    def __init__(
        self,
        source: WorldSource,
        target_path: Path,
        source_uuid: str,
        instance_path: Path | None,
        *,
        source_username: str | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.target_path = target_path
        self.source_uuid = source_uuid
        self.instance_path = instance_path
        self.source_username = source_username

    def run(self) -> None:
        try:
            evidence = detect_local_identities(self.target_path, self.instance_path)
            if not evidence:
                raise ValueError("没有检测到目标 world 的本地玩家 UUID")
            top_score = evidence[0].score
            if top_score <= 0 or sum(item.score == top_score for item in evidence) != 1:
                raise ValueError("目标 world 的本地 UUID 证据无法唯一确认")
            target_uuid = evidence[0].uuid
            report = scan_compatibility(
                self.source,
                self.source_uuid,
                self.target_path,
                target_uuid,
                evidence,
                source_username=self.source_username,
                target_username=evidence[0].username,
            )
            self.completed.emit(report)
        except Exception as exc:  # noqa: BLE001 - worker boundary forwards to GUI
            self.failed.emit(exc)


class FullMigrationWorker(QThread):
    """Run the public full transaction API without file logic in the GUI."""

    stage_changed = Signal(str)
    progress_changed = Signal(int)
    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, plan: FullMigrationPlan) -> None:
        super().__init__()
        self.plan = plan

    def run(self) -> None:
        try:
            def progress_callback(stage: str, percent: int) -> None:
                self.stage_changed.emit(stage)
                self.progress_changed.emit(int(percent))

            result = execute_full_migration(self.plan, progress_callback=progress_callback)
            if result.success:
                self.completed.emit(result)
            else:
                # A transaction failure is a structured result so the GUI can
                # show rollback status without exposing a traceback.
                self.failed.emit(result)
        except Exception as exc:  # noqa: BLE001 - worker boundary forwards to GUI
            self.failed.emit(exc)

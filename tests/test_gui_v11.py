from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtCore")

from PySide6.QtWidgets import QApplication

from core.full_models import CompatibilityReport, FullMigrationResult, ModuleReport, ModuleStatus
from core.models import IdentityEvidence
from core.version_detector import MinecraftVersionInfo, VersionStatus
from tests.helpers import SOURCE_UUID, TARGET_UUID
from ui.errors import error_code, friendly_error
from ui.main_window import MainWindow
from ui.workers import FullMigrationWorker


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_full_worker_emits_real_progress_and_completion(qt_app, monkeypatch) -> None:
    stages: list[str] = []
    progress: list[int] = []
    completed: list[object] = []

    def fake_execute(plan, progress_callback=None):
        assert progress_callback is not None
        progress_callback("CHECKING_GAME", 0)
        progress_callback("COMMITTING", 85)
        return FullMigrationResult(success=True)

    monkeypatch.setattr("ui.workers.execute_full_migration", fake_execute)
    worker = FullMigrationWorker(object())
    worker.stage_changed.connect(stages.append)
    worker.progress_changed.connect(progress.append)
    worker.completed.connect(completed.append)
    worker.run()

    assert stages == ["CHECKING_GAME", "COMMITTING"]
    assert progress == [0, 85]
    assert len(completed) == 1
    assert completed[0].success is True


def test_full_worker_emits_structured_rollback_failure(qt_app, monkeypatch) -> None:
    failures: list[object] = []

    def fake_execute(plan, progress_callback=None):
        return FullMigrationResult(success=False, rollback_performed=True, rollback_success=True)

    monkeypatch.setattr("ui.workers.execute_full_migration", fake_execute)
    worker = FullMigrationWorker(object())
    worker.failed.connect(failures.append)
    worker.run()

    assert len(failures) == 1
    assert failures[0].success is False
    assert failures[0].rollback_success is True


def test_gui_is_v11_and_has_five_pages(qt_app) -> None:
    window = MainWindow()
    assert "Minecraft Player Migrator" in window.windowTitle()
    assert "1.1.0" in window.windowTitle()
    assert window.pages.count() == 5
    assert window.source_edit.acceptDrops() is True
    assert window.target_edit.acceptDrops() is True
    window.close()


def test_gui_blocks_non_match_report(qt_app, tmp_path: Path) -> None:
    player = ModuleReport(
        name="player",
        display_name="人物数据",
        status=ModuleStatus.READY,
        optional=False,
    )
    report = CompatibilityReport(
        source_label="source.zip",
        target_world=tmp_path / "target",
        source_uuid=SOURCE_UUID,
        target_uuid=TARGET_UUID,
        identity_evidence=(IdentityEvidence(uuid=TARGET_UUID),),
        modules={"player": player},
        blocking_issues=("Minecraft 版本无法安全匹配：MISMATCH",),
        source_version=MinecraftVersionInfo(display_version="1.20.1"),
        target_version=MinecraftVersionInfo(display_version="1.21.1"),
        version_status=VersionStatus.MISMATCH,
    )
    window = MainWindow()
    window._compatibility_completed(report)
    assert window.start_button.isEnabled() is False
    assert "MISMATCH" in window.migration_preview.toPlainText()
    window.close()


def test_gui_does_not_write_files_directly() -> None:
    source = Path("ui/main_window.py").read_text(encoding="utf-8")
    assert "write_bytes" not in source
    assert "write_text" not in source
    assert "execute_migration" not in source


def test_gui_error_message_hides_traceback() -> None:
    exc = RuntimeError("internal traceback: secret implementation detail")
    message = friendly_error(exc)
    assert "traceback" not in message.lower()
    assert error_code(exc) == "MIGRATION_FAILED"

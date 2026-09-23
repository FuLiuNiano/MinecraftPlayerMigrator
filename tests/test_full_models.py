from __future__ import annotations

import pytest

from core.full_models import (
    CompatibilityReport,
    FullMigrationOptions,
    ModuleReport,
    ModuleStatus,
)
from core.version_detector import VersionStatus


def test_full_models_expose_required_statuses_and_properties() -> None:
    modules = {
        "player": ModuleReport("player", "人物数据", optional=False),
        "stats": ModuleReport("stats", "Stats", warnings=("已有目标文件",)),
    }
    report = CompatibilityReport(
        source_label="source",
        target_world="target",
        source_uuid="00000000-0000-0000-0000-000000000001",
        target_uuid="00000000-0000-0000-0000-000000000002",
        modules=modules,
        warnings=("磁盘空间充足",),
        version_status=VersionStatus.MATCH,
    )

    assert {status.value for status in ModuleStatus} == {
        "SUCCESS",
        "SKIPPED_NOT_FOUND",
        "SKIPPED_UNSUPPORTED",
        "FAILED",
        "READY",
        "BLOCKED",
    }
    assert report.can_migrate is True
    assert report.has_warnings is True
    assert report.enabled_modules == ("player", "stats")


def test_player_option_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="cannot be disabled"):
        FullMigrationOptions(player=False)


def test_blocking_report_cannot_migrate() -> None:
    report = CompatibilityReport(
        source_label="source",
        target_world="target",
        source_uuid="00000000-0000-0000-0000-000000000001",
        target_uuid="00000000-0000-0000-0000-000000000002",
        modules={
            "player": ModuleReport("player", "人物数据", status=ModuleStatus.BLOCKED, optional=False)
        },
        blocking_issues=("身份冲突",),
    )

    assert report.can_migrate is False

from __future__ import annotations

from collections import namedtuple
from pathlib import Path

import core.compatibility_scanner as scanner_module
from core.compatibility_scanner import scan_compatibility
from core.full_migration import FullMigrationPlanError, build_full_plan
from core.models import IdentityEvidence
from tests.helpers import SOURCE_UUID, TARGET_UUID, write_world
from tests.test_compatibility_scanner import _evidence, _worlds
from tests.test_progress_migration import _write_progress_source


def test_build_full_plan_classifies_overwrites_and_new_files(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    plan = build_full_plan(report)

    assert plan.source == report.source
    assert target / "playerdata" / f"{TARGET_UUID}.dat" in plan.overwrite_files
    assert target / "level.dat" in plan.overwrite_files
    assert target / "advancements" / f"{TARGET_UUID}.json" in plan.new_files
    assert target / "stats" / f"{TARGET_UUID}.json" in plan.new_files
    assert target / "data" / "waystones.dat" in plan.new_files
    assert plan.backup_prefix == "world_backup_before_full_migration"


def test_plan_skips_unsupported_modules(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    quest_path = source / "ftbquests" / f"{SOURCE_UUID}.snbt"
    quest_path.write_text('{ uuid: "bad", name: "unsupported" }', encoding="utf-8")
    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    plan = build_full_plan(report)

    assert not any(path.parts[-2:] == ("ftbquests", f"{TARGET_UUID}.snbt") for path in plan.planned_files)
    assert any("SKIPPED_UNSUPPORTED" in warning for warning in plan.warnings)


def test_plan_blocks_on_identity_conflict(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    conflicting = _evidence() + (
        IdentityEvidence(
            uuid="11111111-1111-1111-1111-111111111111",
            level_dat_match=True,
            recent_playerdata_match=True,
            launcher_arg_match=True,
            usercache_match=True,
        ),
    )
    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, conflicting)

    try:
        build_full_plan(report)
    except FullMigrationPlanError as exc:
        assert "冲突" in str(exc)
    else:  # pragma: no cover - assertion form keeps the expected failure explicit.
        raise AssertionError("expected FullMigrationPlanError")


def test_plan_blocks_on_low_disk_space(tmp_path: Path, monkeypatch) -> None:
    source, target = _worlds(tmp_path)
    disk_usage = namedtuple("disk_usage", "total used free")
    monkeypatch.setattr(scanner_module, "directory_size", lambda _path: 1_000)
    monkeypatch.setattr(scanner_module.shutil, "disk_usage", lambda _path: disk_usage(10_000, 9_900, 100))

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.can_migrate is False
    assert any("磁盘空间不足" in issue for issue in report.blocking_issues)


def test_same_source_and_target_uuid_is_allowed_when_target_identity_matches(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    write_world(source, SOURCE_UUID)
    write_world(target, SOURCE_UUID, target=True)
    _write_progress_source(source)

    report = scan_compatibility(source, SOURCE_UUID, target, SOURCE_UUID, _evidence(SOURCE_UUID))
    plan = build_full_plan(report)

    assert report.can_migrate is True
    assert plan.source_uuid == plan.target_uuid == SOURCE_UUID

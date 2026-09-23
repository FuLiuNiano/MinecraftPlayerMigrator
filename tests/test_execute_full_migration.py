from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core import full_migration
from core.compatibility_scanner import scan_compatibility
from core.full_migration import FullMigrationPlanError, build_full_plan, execute_full_migration
from core.full_models import FullVerificationResult
from core.nbt_codec import deep_compare_nbt, load_nbt_file, nbt_root, player_from_level
from core.snbt_codec import parse_snbt
from tests.helpers import SOURCE_UUID, TARGET_UUID, write_world
from tests.test_compatibility_scanner import _evidence
from tests.test_progress_migration import _make_worlds, _write_progress_source


def _plan(tmp_path: Path, *, same_uuid: bool = False):
    if same_uuid:
        source = tmp_path / "source"
        target = tmp_path / "target"
        write_world(source, SOURCE_UUID)
        write_world(target, SOURCE_UUID, target=True)
        _write_progress_source(source)
        report = scan_compatibility(
            source,
            SOURCE_UUID,
            target,
            SOURCE_UUID,
            _evidence(SOURCE_UUID),
        )
    else:
        source, target = _make_worlds(tmp_path)
        report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())
    return build_full_plan(report), source, target


def _hashes(paths: tuple[Path, ...]) -> dict[Path, str]:
    return {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def test_execute_full_migration_commits_all_modules_once(tmp_path: Path) -> None:
    plan, source, target = _plan(tmp_path)
    stages: list[tuple[str, int]] = []

    result = execute_full_migration(plan, lambda stage, percent: stages.append((stage, percent)))

    assert result.success is True
    assert result.backup_path is not None and result.backup_path.is_dir()
    assert result.rollback_performed is False
    assert result.verification.success is True
    assert len(result.modified_files) == 9
    assert next(stage for stage, _ in stages) == "CHECKING_GAME"
    assert next(stage for stage, _ in reversed(stages)) == "DONE"
    assert "COMMITTING" in [stage for stage, _ in stages]
    assert "VERIFYING" in [stage for stage, _ in stages]
    assert result.log_path is not None and result.log_path.is_file()
    assert source.is_dir()

    player = nbt_root(load_nbt_file(target / "playerdata" / f"{TARGET_UUID}.dat"))
    level_player = player_from_level(load_nbt_file(target / "level.dat"))
    assert deep_compare_nbt(player, level_player) is None
    assert len(player["Inventory"]) == 1
    assert int(player["XpLevel"]) == 64
    assert parse_snbt((target / "ftbquests" / f"{TARGET_UUID}.snbt").read_text())


def test_execute_full_migration_supports_same_source_and_target_uuid(tmp_path: Path) -> None:
    plan, _, target = _plan(tmp_path, same_uuid=True)

    result = execute_full_migration(plan)

    assert result.success is True
    assert result.verification.success is True
    assert (target / "playerdata" / f"{SOURCE_UUID}.dat").is_file()


@pytest.mark.parametrize("failure_at", [1, 4, 9])
def test_commit_failure_rolls_back_every_formal_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_at: int
) -> None:
    plan, _, target = _plan(tmp_path)
    before = _hashes(plan.planned_files)
    real_replace = full_migration.os.replace
    calls = 0

    def fail_at(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError(f"commit failure {failure_at}")
        real_replace(source, destination)

    monkeypatch.setattr(full_migration.os, "replace", fail_at)
    result = execute_full_migration(plan)

    assert result.success is False
    assert result.rollback_performed is True
    assert result.rollback_success is True
    assert any(f"commit failure {failure_at}" in item for item in result.verification.errors)
    assert _hashes(tuple(before)) == before
    for path in plan.new_files:
        assert not path.exists()
    assert list(target.rglob("*.migrator.*.tmp")) == []
    assert result.backup_path is not None and result.backup_path.is_dir()


def test_backup_failure_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, _, _ = _plan(tmp_path)
    before = _hashes(plan.planned_files)

    def fail_backup(*_args, **_kwargs):
        raise OSError("simulated backup failure")

    monkeypatch.setattr(full_migration, "create_full_backup", fail_backup)
    result = execute_full_migration(plan)

    assert result.success is False
    assert result.backup_path is None
    assert result.rollback_performed is False
    assert _hashes(tuple(before)) == before


def test_player_temp_write_failure_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _ = _plan(tmp_path)
    before = _hashes(plan.planned_files)

    def fail_write(_self, _path: Path, _payload: bytes) -> None:
        raise OSError("simulated player temp write failure")

    monkeypatch.setattr(full_migration.FullMigrationTransaction, "_write_temp", fail_write)
    result = execute_full_migration(plan)

    assert result.success is False
    assert result.rollback_success is True
    assert any("player temp write failure" in item for item in result.verification.errors)
    assert _hashes(tuple(before)) == before


def test_advancements_temp_write_failure_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _ = _plan(tmp_path)
    before = _hashes(plan.planned_files)
    real_write = full_migration.FullMigrationTransaction._write_temp
    calls = 0

    def fail_advancements(self, path: Path, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:  # playerdata, level.dat, advancements
            raise OSError("simulated advancements temp write failure")
        real_write(self, path, payload)

    monkeypatch.setattr(full_migration.FullMigrationTransaction, "_write_temp", fail_advancements)
    result = execute_full_migration(plan)

    assert result.success is False
    assert result.rollback_success is True
    assert any("advancements temp write failure" in item for item in result.verification.errors)
    assert _hashes(tuple(before)) == before


def test_level_temp_validation_failure_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _ = _plan(tmp_path)
    before = _hashes(plan.planned_files)

    def fail_level(_payload: bytes, _uuid: str) -> None:
        raise ValueError("simulated level temp validation failure")

    monkeypatch.setattr(full_migration, "_validate_level_payload", fail_level)
    result = execute_full_migration(plan)

    assert result.success is False
    assert result.rollback_success is True
    assert any("level temp validation failure" in item for item in result.verification.errors)
    assert _hashes(tuple(before)) == before


def test_quest_temp_parse_failure_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _ = _plan(tmp_path)
    before = _hashes(plan.planned_files)
    real_validate = full_migration._validate_payload_by_path

    def fail_quest(path: Path, payload: bytes) -> None:
        if path.parent.name == "ftbquests":
            raise ValueError("simulated Quest temp parse failure")
        real_validate(path, payload)

    monkeypatch.setattr(full_migration, "_validate_payload_by_path", fail_quest)
    result = execute_full_migration(plan)

    assert result.success is False
    assert result.rollback_success is True
    assert any("Quest temp parse failure" in item for item in result.verification.errors)
    assert _hashes(tuple(before)) == before


def test_final_verifier_failure_rolls_back_all_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _ = _plan(tmp_path)
    before = _hashes(plan.planned_files)
    monkeypatch.setattr(
        full_migration,
        "verify_full_migration",
        lambda _plan: FullVerificationResult(errors=("forced verifier failure",)),
    )

    result = execute_full_migration(plan)

    assert result.success is False
    assert result.rollback_performed is True
    assert result.rollback_success is True
    assert any("forced verifier failure" in item for item in result.verification.errors)
    assert _hashes(tuple(before)) == before
    for path in plan.new_files:
        assert not path.exists()


def test_rollback_failure_is_reported_without_hiding_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _ = _plan(tmp_path)
    real_replace = full_migration.os.replace
    calls = 0

    def fail_second(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("primary commit failure")
        real_replace(source, destination)

    def fail_restore(_path: Path, _payload: bytes) -> None:
        raise OSError("rollback restore failure")

    monkeypatch.setattr(full_migration.os, "replace", fail_second)
    monkeypatch.setattr(full_migration, "atomic_write", fail_restore)
    result = execute_full_migration(plan)

    assert result.success is False
    assert result.rollback_performed is True
    assert result.rollback_success is False
    assert any("primary commit failure" in item for item in result.verification.errors)
    assert any("rollback restore failure" in item for item in result.verification.errors)


def test_cleanup_failure_does_not_hide_primary_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _ = _plan(tmp_path)

    def fail_replace(*_args, **_kwargs) -> None:
        raise OSError("primary commit failure")

    def fail_unlink(path: Path, *, missing_ok: bool = False) -> None:
        raise OSError("temporary cleanup failure")

    monkeypatch.setattr(full_migration.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    result = execute_full_migration(plan)

    assert result.success is False
    assert result.rollback_success is False
    assert any("primary commit failure" in item for item in result.verification.errors)
    assert any("temporary cleanup failure" in item for item in result.verification.errors)


def test_target_without_playerdata_remains_safely_blocked(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    write_world(source, SOURCE_UUID)
    write_world(target, TARGET_UUID, target=True)
    (target / "playerdata" / f"{TARGET_UUID}.dat").unlink()
    _write_progress_source(source)

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.can_migrate is False
    with pytest.raises(FullMigrationPlanError):
        build_full_plan(report)

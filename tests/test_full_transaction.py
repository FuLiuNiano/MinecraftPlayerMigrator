from __future__ import annotations

from pathlib import Path

import pytest

from core import full_migration
from core.full_migration import FullMigrationTransaction


def test_transaction_restores_existing_and_removes_new_files(tmp_path: Path) -> None:
    world = tmp_path / "world"
    world.mkdir()
    existing = world / "existing.json"
    existing.write_bytes(b"before")
    new_file = world / "new.json"
    transaction = FullMigrationTransaction(world)

    transaction.stage(existing, b"{}", "player")
    transaction.stage(new_file, b"{}", "stats")
    transaction.commit()

    assert existing.read_bytes() == b"{}"
    assert new_file.read_bytes() == b"{}"
    assert transaction.overwritten_files == [existing]
    assert transaction.created_files == [new_file]

    # Simulate a later failure after both formal replacements.
    errors = transaction.rollback()
    assert errors == ()
    assert transaction.rollback_success is True
    assert existing.read_bytes() == b"before"
    assert not new_file.exists()
    assert list(world.glob("*.tmp")) == []


def test_transaction_uses_same_directory_temporary_files(tmp_path: Path) -> None:
    world = tmp_path / "world"
    world.mkdir()
    destination = world / "data.json"
    transaction = FullMigrationTransaction(world)
    pending = transaction.stage(destination, b"{}", "stats")

    assert pending.temp_path.parent == destination.parent
    assert pending.temp_path.is_file()
    transaction.cleanup_temp()
    assert not pending.temp_path.exists()


def test_transaction_rejects_destination_outside_world(tmp_path: Path) -> None:
    world = tmp_path / "world"
    world.mkdir()
    transaction = FullMigrationTransaction(world)

    with pytest.raises(ValueError, match="越过 world"):
        transaction.stage(tmp_path / "outside.dat", b"x", "test")


def test_transaction_records_rollback_restore_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = tmp_path / "world"
    world.mkdir()
    destination = world / "existing.json"
    destination.write_bytes(b"before")
    transaction = FullMigrationTransaction(world)
    transaction.stage(destination, b"{}", "player")
    transaction.commit()

    def fail_restore(_path: Path, _payload: bytes) -> None:
        raise OSError("simulated restore failure")

    monkeypatch.setattr(full_migration, "atomic_write", fail_restore)
    errors = transaction.rollback()

    assert transaction.rollback_performed is True
    assert transaction.rollback_success is False
    assert any("simulated restore failure" in item for item in errors)
    assert destination.read_bytes() == b"{}"


def test_transaction_cleanup_failure_is_reported_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = tmp_path / "world"
    world.mkdir()
    transaction = FullMigrationTransaction(world)
    pending = transaction.stage(world / "data.json", b"{}", "stats")

    def fail_unlink(path: Path, *, missing_ok: bool = False) -> None:
        raise OSError("simulated temp cleanup failure")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    errors = transaction.cleanup_temp()

    assert pending.temp_path.exists()
    assert any("simulated temp cleanup failure" in item for item in errors)

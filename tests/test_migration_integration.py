from pathlib import Path

import pytest

import core.migration as migration_module
from core.migration import build_plan, execute_migration
from core.nbt_codec import load_nbt_file, nbt_root, player_from_level
from core.verifier import verify_player_files
from core.zip_reader import resolve_world_source
from tests.helpers import SOURCE_UUID, TARGET_UUID, write_world


def test_real_migration_flow_uses_only_synthetic_world(tmp_path: Path) -> None:
    source_world = tmp_path / "source_world"
    target_world = tmp_path / "target_world"
    write_world(source_world, SOURCE_UUID)
    write_world(target_world, TARGET_UUID, target=True)

    source_before = (source_world / "playerdata" / f"{SOURCE_UUID}.dat").read_bytes()
    plan = build_plan(resolve_world_source(source_world), target_world, SOURCE_UUID, TARGET_UUID)
    result = execute_migration(plan, required_items=("test:sword",))

    verification = verify_player_files(
        result.target_player_path, result.level_path, ("test:sword",)
    )
    assert result.backup_path.is_dir()
    assert (result.backup_path / "region" / "r.0.0.mca").read_bytes() == b"test-region"
    assert result.target_backup_path.is_file()
    assert verification.target_uuid == TARGET_UUID
    assert verification.inventory_count == 1
    assert verification.xp_level == 64
    assert verification.playerdata_leveldat_equal is True
    assert verification.has_forge_caps is True
    assert verification.has_curios is True
    assert verification.required_items_present == {"test:sword": True}
    assert (source_world / "playerdata" / f"{SOURCE_UUID}.dat").read_bytes() == source_before

    target_player = load_nbt_file(result.target_player_path)
    level_player = player_from_level(load_nbt_file(result.level_path))
    assert nbt_root(target_player)["UnknownModData"]["Nested"]["key"] == "must-survive"
    assert nbt_root(target_player)["ForgeCaps"]["test:unknown_capability"]["Nested"] == "keep-me"
    assert level_player["ForgeCaps"]["curios:inventory"]["Curios"][0]["Identifier"] == "ring"


def test_second_write_failure_restores_both_formal_files(tmp_path: Path, monkeypatch) -> None:
    source_world = tmp_path / "source_world"
    target_world = tmp_path / "target_world"
    write_world(source_world, SOURCE_UUID)
    write_world(target_world, TARGET_UUID, target=True)
    target_player_path = target_world / "playerdata" / f"{TARGET_UUID}.dat"
    level_path = target_world / "level.dat"
    target_before = target_player_path.read_bytes()
    level_before = level_path.read_bytes()
    plan = build_plan(resolve_world_source(source_world), target_world, SOURCE_UUID, TARGET_UUID)

    real_atomic_write = migration_module.recovery_atomic_write

    def fail_on_level(path, data):
        if Path(path).name == "level.dat":
            raise OSError("simulated level.dat write failure")
        return real_atomic_write(path, data)

    monkeypatch.setattr(migration_module, "atomic_write", fail_on_level)
    with pytest.raises(OSError, match="simulated level.dat write failure"):
        execute_migration(plan)

    assert target_player_path.read_bytes() == target_before
    assert level_path.read_bytes() == level_before
    assert list(tmp_path.glob("world_backup_before_player_migration_*"))
    assert not list(target_world.rglob("*.tmp"))

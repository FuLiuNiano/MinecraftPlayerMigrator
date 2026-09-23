from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from core.migration import build_plan, execute_migration
from core.nbt_codec import (
    deep_compare_nbt,
    get_inventory,
    get_player_uuid,
    load_nbt_bytes,
    load_nbt_file,
    nbt_root,
    player_from_level,
    replace_player_uuid,
    serialize_nbt,
)
from core.player_scanner import load_source_names, scan_players
from core.verifier import verify_player_files
from core.zip_reader import resolve_world_source

REAL_ZIP = os.environ.get("MIGRATOR_REAL_ZIP")
REAL_WORLD = os.environ.get("MIGRATOR_REAL_WORLD")
REAL_SOURCE_UUID = os.environ.get("MIGRATOR_REAL_SOURCE_UUID")
REAL_TARGET_UUID = os.environ.get("MIGRATOR_REAL_TARGET_UUID")
REAL_USERNAME = os.environ.get("MIGRATOR_REAL_USERNAME")


pytestmark = pytest.mark.real_data


@pytest.mark.skipif(
    not all((REAL_ZIP, REAL_SOURCE_UUID, REAL_TARGET_UUID, REAL_USERNAME)),
    reason="real ZIP, UUID and username environment variables are required",
)
def test_real_zip_scan_and_source_identity() -> None:
    source_uuid = str(REAL_SOURCE_UUID)
    source = resolve_world_source(Path(REAL_ZIP))
    names = load_source_names(source)
    players = scan_players(source, names)
    source_player = next(player for player in players if player.uuid == source_uuid)

    assert source.root_prefix == "world/"
    assert f"{source_uuid}.dat" in source.list_player_files()
    assert names[source_uuid] == REAL_USERNAME
    assert source_player.username == REAL_USERNAME
    assert source_player.inventory_count == 38
    assert source_player.xp_level == 64
    assert source_player.position == pytest.approx((144.08895257394968, 88, 309.63401836798727))
    assert source_player.has_forge_caps is True
    assert source_player.has_curios is True
    ids = {item.item_id for item in source_player.inventory_preview}
    assert {
        "soulsweapons:darkin_blade",
        "slashblade:slashblade",
        "minecraft:diamond_pickaxe",
        "cataclysm:the_annihilator",
        "minecraft:diamond_shovel",
        "minecraft:golden_carrot",
        "explorerscompass:explorerscompass",
        "celestial_artifacts:backtrack_mirror",
    }.issubset(ids)


@pytest.mark.skipif(
    not all((REAL_ZIP, REAL_SOURCE_UUID, REAL_TARGET_UUID)),
    reason="real ZIP and UUID environment variables are required",
)
def test_real_player_round_trip_preserves_structure_and_types() -> None:
    source_uuid = str(REAL_SOURCE_UUID)
    source = resolve_world_source(Path(REAL_ZIP))
    original = nbt_root(load_nbt_bytes(source.read_bytes(f"playerdata/{source_uuid}.dat")))
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "player.dat"
        path.write_bytes(serialize_nbt(original))
        round_trip = nbt_root(load_nbt_file(path))
    assert deep_compare_nbt(original, round_trip) is None
    assert len(original) == len(round_trip)
    assert len(get_inventory(original)) == len(get_inventory(round_trip)) == 38
    assert deep_compare_nbt(original["ForgeCaps"], round_trip["ForgeCaps"]) is None
    assert get_player_uuid(original) == get_player_uuid(round_trip) == source_uuid


@pytest.mark.skipif(
    not all((REAL_ZIP, REAL_WORLD, REAL_SOURCE_UUID, REAL_TARGET_UUID)),
    reason="real ZIP/world and UUID environment variables are required",
)
def test_real_isolated_migration_uses_formal_flow() -> None:
    source_uuid = str(REAL_SOURCE_UUID)
    target_uuid = str(REAL_TARGET_UUID)
    source = resolve_world_source(Path(REAL_ZIP))
    target_world = Path(REAL_WORLD)
    target_player_path = target_world / "playerdata" / f"{target_uuid}.dat"
    level_path = target_world / "level.dat"
    target_before = nbt_root(load_nbt_file(target_player_path))
    level_before = level_path.read_bytes()
    source_player = nbt_root(load_nbt_bytes(source.read_bytes(f"playerdata/{source_uuid}.dat")))

    result = execute_migration(
        build_plan(source, target_world, source_uuid, target_uuid),
        required_items=(
            "soulsweapons:darkin_blade",
            "slashblade:slashblade",
            "cataclysm:the_annihilator",
        ),
    )
    migrated = nbt_root(load_nbt_file(result.target_player_path))
    level_player = player_from_level(load_nbt_file(result.level_path))
    expected = replace_player_uuid(source_player, target_uuid)

    assert result.backup_path.is_dir()
    assert result.target_backup_path.is_file()
    assert deep_compare_nbt(expected, migrated) is None
    assert deep_compare_nbt(migrated, level_player) is None
    assert deep_compare_nbt(target_before, nbt_root(load_nbt_file(result.target_backup_path))) is None
    # A disposable target may already contain the migrated semantic state.
    # The formal-flow assertions above still prove backup, serialization, and equality.
    assert level_path.read_bytes() != level_before or deep_compare_nbt(target_before, expected) is None
    verification = verify_player_files(result.target_player_path, result.level_path)
    assert verification.target_uuid == target_uuid
    assert verification.inventory_count == 38
    assert verification.xp_level == 64
    assert verification.position == pytest.approx((144.08895257394968, 88, 309.63401836798727))
    assert verification.has_forge_caps is True
    assert verification.has_curios is True

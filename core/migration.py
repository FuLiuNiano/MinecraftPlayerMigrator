from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .backup import atomic_write, backup_file, create_full_backup
from .backup import atomic_write as recovery_atomic_write
from .models import MigrationPlan
from .nbt_codec import (
    clone_compound,
    get_player_uuid,
    load_nbt_bytes,
    player_from_level,
    replace_player_uuid,
    serialize_nbt,
    set_level_player,
)
from .process_check import assert_game_closed
from .uuid_utils import normalize_uuid
from .verifier import verify_player_files
from .zip_reader import WorldSource


@dataclass(slots=True)
class MigrationResult:
    backup_path: Path
    target_backup_path: Path
    target_player_path: Path
    level_path: Path
    verification: object


def build_plan(
    source_world: WorldSource,
    target_world: str | Path,
    source_uuid: str,
    target_uuid: str,
) -> MigrationPlan:
    return MigrationPlan(
        source_world,
        Path(target_world).expanduser().resolve(),
        normalize_uuid(source_uuid),
        normalize_uuid(target_uuid),
    )


def execute_migration(
    plan: MigrationPlan, *, required_items: tuple[str, ...] = ()
) -> MigrationResult:
    assert_game_closed()
    target_world = plan.target_world
    target_path = target_world / "playerdata" / f"{plan.target_uuid}.dat"
    target_dat_old = target_world / "playerdata" / f"{plan.target_uuid}.dat_old"
    level_path = target_world / "level.dat"
    if not target_path.is_file() or not level_path.is_file():
        raise FileNotFoundError("Target world must contain level.dat and target playerdata")
    target_backup_candidate = target_path.with_name(target_path.name + ".before-migration.bak")
    dat_old_backup_candidate = target_dat_old.with_name(
        target_dat_old.name + ".before-migration.bak"
    )
    if target_backup_candidate.exists() or dat_old_backup_candidate.exists():
        raise FileExistsError(
            "A target pre-migration backup already exists; refusing to overwrite it"
        )

    source_bytes = plan.source_world.read_bytes(f"playerdata/{plan.source_uuid}.dat")
    source_file = load_nbt_bytes(source_bytes)
    source_player = clone_compound(source_file)
    source_identity = get_player_uuid(source_player)
    if source_identity is None:
        raise ValueError("Source Player NBT has no recognizable UUID")
    if source_identity != plan.source_uuid:
        raise ValueError(
            f"Source filename and Player.UUID disagree: file={plan.source_uuid}, nbt={source_identity}"
        )
    migrated_player = replace_player_uuid(source_player, plan.target_uuid)

    level_before = load_nbt_bytes(level_path.read_bytes())
    migrated_level = set_level_player(level_before, migrated_player)

    # Validate serialized temporary payloads before any formal replacement.
    player_payload = serialize_nbt(migrated_player, gzipped=True)
    parsed_player = load_nbt_bytes(player_payload)
    if get_player_uuid(parsed_player) != plan.target_uuid:
        raise ValueError("Temporary playerdata UUID verification failed")
    level_payload = serialize_nbt(migrated_level, gzipped=True)
    parsed_level = load_nbt_bytes(level_payload)
    parsed_level_player = player_from_level(parsed_level)
    if get_player_uuid(parsed_level_player) != plan.target_uuid:
        raise ValueError("Temporary level.dat Player UUID verification failed")

    backup_path = create_full_backup(target_world, prefix="world_backup_before_player_migration")
    target_backup_path = backup_file(target_path, ".before-migration.bak")
    if target_dat_old.exists():
        backup_file(target_dat_old, ".before-migration.bak")

    try:
        atomic_write(target_path, player_payload)
        atomic_write(level_path, level_payload)

        verification = verify_player_files(target_path, level_path, required_items)
        if verification.target_uuid != plan.target_uuid:
            raise ValueError("Post-migration target UUID verification failed")
        if not verification.playerdata_leveldat_equal:
            raise ValueError("Post-migration playerdata and level.dat Player differ")
        missing_items = [
            name for name, present in verification.required_items_present.items() if not present
        ]
        if missing_items:
            raise ValueError(
                f"Post-migration required items are missing: {', '.join(missing_items)}"
            )
    except Exception:
        # The full world backup remains the durable recovery point. Restore both
        # formal files as well so a failed second replacement cannot leave a
        # half-migrated world in place.
        recovery_atomic_write(
            target_path,
            target_path.with_suffix(target_path.suffix + ".before-migration.bak").read_bytes(),
        )
        recovery_atomic_write(level_path, (backup_path / "level.dat").read_bytes())
        raise

    return MigrationResult(backup_path, target_backup_path, target_path, level_path, verification)

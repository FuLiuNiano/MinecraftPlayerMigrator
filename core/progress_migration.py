from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nbtlib import Compound, File
from nbtlib.tag import Array, IntArray

from .backup import atomic_write, create_full_backup
from .nbt_codec import (
    deep_compare_nbt,
    load_nbt_bytes,
    nbt_root,
    serialize_nbt,
)
from .process_check import assert_game_closed
from .snbt_codec import SnbtDocument, direct_compound_entries, parse_snbt
from .uuid_utils import normalize_uuid, uuid_to_int_array
from .zip_reader import WorldSource, resolve_world_source


@dataclass(frozen=True, slots=True)
class ProgressMigrationOptions:
    advancements: bool = True
    stats: bool = True
    ftb_quests: bool = True
    ftb_team: bool = True
    waystones: bool = True
    endinglib: bool = True
    cosmetic_armor: bool = True


@dataclass(frozen=True, slots=True)
class WaystoneOwnership:
    index: int
    name: str
    dimension: str
    position: tuple[int, int, int]
    waystone_uid: tuple[int, ...]
    owner_uid: tuple[int, ...]


@dataclass(slots=True)
class ProgressMigrationResult:
    backup_path: Path
    modified_files: tuple[Path, ...]
    advancements_total: int | None = None
    advancements_completed: int | None = None
    advancements_incomplete: int | None = None
    stats_categories: int | None = None
    stats_records: int | None = None
    ftb_quest_task_progress: int | None = None
    ftb_quest_started: int | None = None
    ftb_quest_completed: int | None = None
    ftb_quest_claimed_rewards: int | None = None
    ftb_team_owner: str | None = None
    ftb_team_rank: str | None = None
    waystones: tuple[WaystoneOwnership, ...] = ()
    endinglib_records: int = 0
    source_identity_residuals: tuple[str, ...] = ()


@dataclass(slots=True)
class _PreparedProgress:
    payloads: list[tuple[Path, bytes]] = field(default_factory=list)
    advancements_total: int | None = None
    advancements_completed: int | None = None
    advancements_incomplete: int | None = None
    stats_categories: int | None = None
    stats_records: int | None = None
    ftb_quest_task_progress: int | None = None
    ftb_quest_started: int | None = None
    ftb_quest_completed: int | None = None
    ftb_quest_claimed_rewards: int | None = None
    ftb_team_owner: str | None = None
    ftb_team_rank: str | None = None
    waystones: tuple[WaystoneOwnership, ...] = ()
    endinglib_records: int = 0
    source_identity_residuals: tuple[str, ...] = ()


class ProgressMigrationTransaction:
    """Tracks formal writes and restores original bytes if any write fails."""

    def __init__(self, world: Path) -> None:
        self.world = world.resolve()
        self._originals: dict[Path, bytes | None] = {}
        self._modified: list[Path] = []

    @property
    def modified_files(self) -> tuple[Path, ...]:
        return tuple(self._modified)

    def write(self, relative: Path, payload: bytes) -> None:
        path = (self.world / relative).resolve()
        if path not in self._originals:
            self._originals[path] = path.read_bytes() if path.is_file() else None
        atomic_write(path, payload)
        if path not in self._modified:
            self._modified.append(path)

    def rollback(self) -> None:
        errors: list[BaseException] = []
        for path, original in reversed(tuple(self._originals.items())):
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, original)
            except OSError as exc:  # pragma: no cover - only reached on disk failure
                errors.append(exc)
        if errors:
            raise OSError(f"Progress migration rollback failed for {len(errors)} file(s)") from errors[0]


def _source(source_world: WorldSource | str | Path) -> WorldSource:
    return source_world if isinstance(source_world, WorldSource) else resolve_world_source(source_world)


def _copy_json_payload(source: WorldSource, relative: str) -> tuple[bytes, Any]:
    payload = source.read_bytes(relative)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON source: {relative}") from exc
    return payload, value


def _advance_summary(value: dict[str, Any]) -> tuple[int, int, int]:
    entries = [item for item in value.values() if isinstance(item, dict)]
    total = len(entries)
    completed = sum(1 for item in entries if item.get("done") is True)
    return total, completed, total - completed


def _stats_summary(value: dict[str, Any]) -> tuple[int, int]:
    stats = value.get("stats")
    if not isinstance(stats, dict):
        raise TypeError("Stats JSON does not contain a stats object")
    return len(stats), sum(len(entries) for entries in stats.values() if isinstance(entries, dict))


def _field_string(document: SnbtDocument, key: str) -> str:
    return document.string_value(document.root_field(key))


def _quest_player_name(source_name: str, source_compact: str, target_compact: str) -> str:
    if "#" not in source_name:
        raise ValueError("FTB Quests player name has no UUID suffix")
    prefix, suffix = source_name.rsplit("#", 1)
    if not suffix or not source_compact.startswith(suffix.lower()):
        raise ValueError("FTB Quests player name does not match the source UUID")
    return f"{prefix}#{target_compact[: len(suffix)]}"


def _migrate_quests(
    source: WorldSource, source_uuid: str, target_uuid: str
) -> tuple[bytes, tuple[int, int, int, int]]:
    relative = f"ftbquests/{source_uuid}.snbt"
    source_text = source.read_text(relative)
    document = parse_snbt(source_text)
    source_compact = source_uuid.replace("-", "")
    target_compact = target_uuid.replace("-", "")

    uuid_field = document.root_field("uuid")
    if document.string_value(uuid_field) != source_compact:
        raise ValueError("FTB Quests source uuid does not match the source player")
    name_field = document.root_field("name")
    source_name = document.string_value(name_field)
    target_name = _quest_player_name(source_name, source_compact, target_compact)
    document.replace_string(uuid_field, source_compact, target_compact)
    document.replace_string(name_field, source_name, target_name)

    counts: list[int] = []
    for section in ("task_progress", "started", "completed"):
        counts.append(len(direct_compound_entries(document, section)))

    claimed_entries = direct_compound_entries(document, "claimed_rewards")
    claimed_rewrites = 0
    for entry in claimed_entries:
        if entry.key.startswith(source_compact + ":"):
            document.replace_key(entry, entry.key, target_compact + entry.key[len(source_compact) :])
            claimed_rewrites += 1
        elif source_compact in entry.key:
            raise ValueError(f"Unexpected source UUID occurrence in Quest key: {entry.key}")
    if claimed_rewrites != len(claimed_entries):
        raise ValueError(
            f"FTB Quests claimed reward identity mismatch: "
            f"{claimed_rewrites} of {len(claimed_entries)}"
        )

    migrated_text = document.render()
    migrated = parse_snbt(migrated_text)
    if migrated.string_value(migrated.root_field("uuid")) != target_compact:
        raise ValueError("FTB Quests target uuid validation failed")
    if migrated.string_value(migrated.root_field("name")) != target_name:
        raise ValueError("FTB Quests target name validation failed")
    if source_uuid != target_uuid and any(
        entry.key.startswith(source_compact + ":")
        for entry in direct_compound_entries(migrated, "claimed_rewards")
    ):
        raise ValueError("FTB Quests still contains the source player in claimed rewards")
    return migrated_text.encode("utf-8"), (*counts, claimed_rewrites)


def _migrate_team(
    source: WorldSource, source_uuid: str, target_uuid: str
) -> tuple[bytes, str, str]:
    relative = f"ftbteams/player/{source_uuid}.snbt"
    document = parse_snbt(source.read_text(relative))
    if _field_string(document, "id") != source_uuid:
        raise ValueError("FTB Team source id does not match the source player")
    if _field_string(document, "type") != "player":
        raise ValueError("FTB Team source is not a personal player team")
    owner = _field_string(document, "player_name")
    ranks = document.compound_value(document.root_field("ranks"))
    source_rank = next((item for item in ranks.fields if item.key == source_uuid), None)
    if source_rank is None:
        raise ValueError("FTB Team source owner rank is missing")
    rank_value = document.string_value(source_rank)
    document.replace_string(document.root_field("id"), source_uuid, target_uuid)
    document.replace_key(source_rank, source_uuid, target_uuid)
    migrated_text = document.render()
    migrated = parse_snbt(migrated_text)
    if _field_string(migrated, "id") != target_uuid:
        raise ValueError("FTB Team target id validation failed")
    migrated_ranks = migrated.compound_value(migrated.root_field("ranks"))
    if not any(item.key == target_uuid for item in migrated_ranks.fields):
        raise ValueError("FTB Team target owner rank validation failed")
    if source_uuid != target_uuid and any(item.key == source_uuid for item in migrated_ranks.fields):
        raise ValueError("FTB Team still contains the source owner rank")
    return migrated_text.encode("utf-8"), owner, rank_value


def _array_equals(value: object, expected: tuple[int, ...]) -> bool:
    return isinstance(value, Array) and tuple(int(item) for item in value) == expected


def _tag_tuple(value: object) -> tuple[int, ...]:
    return tuple(int(item) for item in value) if isinstance(value, Array) else ()


def _waystone_snapshot(index: int, waystone: Compound) -> WaystoneOwnership:
    block_pos = waystone.get("BlockPos", {})
    return WaystoneOwnership(
        index=index,
        name=str(waystone.get("Name", "")),
        dimension=str(waystone.get("World", "")),
        position=(
            int(block_pos.get("X", 0)),
            int(block_pos.get("Y", 0)),
            int(block_pos.get("Z", 0)),
        ),
        waystone_uid=_tag_tuple(waystone.get("WaystoneUid")),
        owner_uid=_tag_tuple(waystone.get("OwnerUid")),
    )


def _migrate_waystones(
    source: WorldSource, source_uuid: str, target_uuid: str
) -> tuple[bytes, tuple[WaystoneOwnership, ...]]:
    source_file = load_nbt_bytes(source.read_bytes("data/waystones.dat"))
    source_root = nbt_root(source_file)
    migrated_root = copy.deepcopy(source_root)
    source_ints = tuple(int(item) for item in uuid_to_int_array(source_uuid))
    target_array = uuid_to_int_array(target_uuid)
    data = migrated_root.get("data")
    waystones = data.get("Waystones") if isinstance(data, Compound) else None
    if not isinstance(waystones, list):
        raise TypeError("Waystones NBT does not contain data.Waystones")

    snapshots: list[WaystoneOwnership] = []
    for index, waystone in enumerate(waystones):
        if not isinstance(waystone, Compound):
            continue
        owner = waystone.get("OwnerUid")
        if _array_equals(owner, source_ints):
            snapshots.append(_waystone_snapshot(index, waystone))
            waystone["OwnerUid"] = IntArray(target_array)
    if not snapshots:
        raise ValueError("No Waystones owned by the source player were found")

    payload = serialize_nbt(File({"": migrated_root}), gzipped=bool(getattr(source_file, "gzipped", True)))
    parsed = nbt_root(load_nbt_bytes(payload))
    if deep_compare_nbt(migrated_root, parsed):
        raise ValueError("Waystones NBT round-trip validation failed")
    return payload, tuple(snapshots)


def _migrate_endinglib(
    source: WorldSource, source_uuid: str, target_uuid: str
) -> tuple[bytes, int]:
    source_file = load_nbt_bytes(source.read_bytes("data/endinglib_saved_data.dat"))
    source_root = nbt_root(source_file)
    migrated_root = copy.deepcopy(source_root)
    source_ints = tuple(int(item) for item in uuid_to_int_array(source_uuid))
    target_array = uuid_to_int_array(target_uuid)
    data = migrated_root.get("data")
    changed = 0
    if not isinstance(data, Compound):
        raise TypeError("Ending Library data compound is missing")
    for section in ("PlayersDisabledOverlays", "PlayersDisabledInputPermissions"):
        entries = data.get(section)
        if not isinstance(entries, list):
            raise TypeError(f"Ending Library section is missing: {section}")
        for entry in entries:
            if isinstance(entry, Compound) and _array_equals(entry.get("id"), source_ints):
                entry["id"] = IntArray(target_array)
                changed += 1
    if changed != 2:
        raise ValueError(f"Expected two Ending Library source records, found {changed}")
    payload = serialize_nbt(File({"": migrated_root}), gzipped=bool(getattr(source_file, "gzipped", True)))
    parsed = nbt_root(load_nbt_bytes(payload))
    if deep_compare_nbt(migrated_root, parsed):
        raise ValueError("Ending Library NBT round-trip validation failed")
    return payload, changed


def _prepare(
    source: WorldSource,
    target_world: Path,
    source_uuid: str,
    target_uuid: str,
    options: ProgressMigrationOptions,
) -> _PreparedProgress:
    prepared = _PreparedProgress()
    if options.advancements:
        payload, value = _copy_json_payload(
            source, f"advancements/{source_uuid}.json"
        )
        total, completed, incomplete = _advance_summary(value)
        prepared.payloads.append((Path("advancements") / f"{target_uuid}.json", payload))
        prepared.advancements_total = total
        prepared.advancements_completed = completed
        prepared.advancements_incomplete = incomplete
    if options.stats:
        payload, value = _copy_json_payload(source, f"stats/{source_uuid}.json")
        categories, records = _stats_summary(value)
        prepared.payloads.append((Path("stats") / f"{target_uuid}.json", payload))
        prepared.stats_categories = categories
        prepared.stats_records = records
    if options.ftb_quests:
        payload, counts = _migrate_quests(source, source_uuid, target_uuid)
        prepared.payloads.append((Path("ftbquests") / f"{target_uuid}.snbt", payload))
        (
            prepared.ftb_quest_task_progress,
            prepared.ftb_quest_started,
            prepared.ftb_quest_completed,
            prepared.ftb_quest_claimed_rewards,
        ) = counts
    if options.ftb_team:
        payload, owner, rank = _migrate_team(source, source_uuid, target_uuid)
        prepared.payloads.append(
            (Path("ftbteams") / "player" / f"{target_uuid}.snbt", payload)
        )
        prepared.ftb_team_owner = owner
        prepared.ftb_team_rank = rank
    if options.waystones:
        payload, records = _migrate_waystones(source, source_uuid, target_uuid)
        prepared.payloads.append((Path("data") / "waystones.dat", payload))
        prepared.waystones = records
    if options.endinglib:
        payload, records = _migrate_endinglib(source, source_uuid, target_uuid)
        prepared.payloads.append((Path("data") / "endinglib_saved_data.dat", payload))
        prepared.endinglib_records = records
    if options.cosmetic_armor:
        relative = f"playerdata/{source_uuid}.cosarmor"
        if source.exists(relative):
            payload = source.read_bytes(relative)
            load_nbt_bytes(payload)  # validate before any formal write
            prepared.payloads.append(
                (Path("playerdata") / f"{target_uuid}.cosarmor", payload)
            )
    if not target_world.is_dir() or not (target_world / "level.dat").is_file():
        raise FileNotFoundError("Target isolated world must contain level.dat")
    return prepared


def _validate_formal_outputs(
    target_world: Path,
    prepared: _PreparedProgress,
    source_uuid: str,
    target_uuid: str,
) -> tuple[str, ...]:
    """Re-read every formal output and report only identity-field leftovers."""

    residuals: list[str] = []
    source_compact = source_uuid.replace("-", "")
    for relative, expected in prepared.payloads:
        path = target_world / relative
        actual = path.read_bytes()
        if actual != expected:
            raise ValueError(f"Formal output changed during write: {relative}")
        suffix = path.suffix.lower()
        if suffix == ".json":
            json.loads(actual.decode("utf-8"))
        elif suffix == ".snbt":
            parse_snbt(actual.decode("utf-8"))
        else:
            load_nbt_bytes(actual)

    quest_path = target_world / "ftbquests" / f"{target_uuid}.snbt"
    if quest_path.is_file():
        quest = parse_snbt(quest_path.read_text(encoding="utf-8"))
        if source_uuid != target_uuid and quest.string_value(quest.root_field("uuid")) == source_compact:
            residuals.append("ftbquests.uuid")
        if source_uuid != target_uuid and any(
            item.key.startswith(source_compact + ":")
            for item in direct_compound_entries(quest, "claimed_rewards")
        ):
            residuals.append("ftbquests.claimed_rewards")

    team_path = target_world / "ftbteams" / "player" / f"{target_uuid}.snbt"
    if team_path.is_file():
        team = parse_snbt(team_path.read_text(encoding="utf-8"))
        if source_uuid != target_uuid and team.string_value(team.root_field("id")) == source_uuid:
            residuals.append("ftbteams.player.id")
        ranks = team.compound_value(team.root_field("ranks"))
        if source_uuid != target_uuid and any(item.key == source_uuid for item in ranks.fields):
            residuals.append("ftbteams.player.ranks")

    waystones_path = target_world / "data" / "waystones.dat"
    if waystones_path.is_file():
        root = nbt_root(load_nbt_bytes(waystones_path.read_bytes()))
        data = root.get("data")
        entries = data.get("Waystones", []) if isinstance(data, Compound) else []
        source_ints = tuple(int(item) for item in uuid_to_int_array(source_uuid))
        if source_uuid != target_uuid and any(
            isinstance(item, Compound) and _array_equals(item.get("OwnerUid"), source_ints)
            for item in entries
        ):
            residuals.append("waystones.OwnerUid")

    ending_path = target_world / "data" / "endinglib_saved_data.dat"
    if ending_path.is_file():
        root = nbt_root(load_nbt_bytes(ending_path.read_bytes()))
        data = root.get("data")
        source_ints = tuple(int(item) for item in uuid_to_int_array(source_uuid))
        if source_uuid != target_uuid and isinstance(data, Compound) and any(
            isinstance(item, Compound) and _array_equals(item.get("id"), source_ints)
            for section in ("PlayersDisabledOverlays", "PlayersDisabledInputPermissions")
            for item in data.get(section, [])
        ):
            residuals.append("endinglib.id")
    return tuple(residuals)


def execute_progress_migration(
    source_world: WorldSource | str | Path,
    target_world: str | Path,
    source_uuid: str,
    target_uuid: str,
    *,
    options: ProgressMigrationOptions | None = None,
    backup_prefix: str = "world_backup_before_full_progress_migration",
) -> ProgressMigrationResult:
    """Migrate selected player-progress files into an isolated world transactionally."""

    assert_game_closed()
    source = _source(source_world)
    target = Path(target_world).expanduser().resolve()
    source_uuid = normalize_uuid(source_uuid)
    target_uuid = normalize_uuid(target_uuid)
    if not source.is_zip and source.location.resolve() == target:
        raise ValueError("Source and target world must be different")
    options = options or ProgressMigrationOptions()
    prepared = _prepare(source, target, source_uuid, target_uuid, options)

    backup_path = create_full_backup(target, prefix=backup_prefix)
    transaction = ProgressMigrationTransaction(target)
    try:
        for relative, payload in prepared.payloads:
            transaction.write(relative, payload)
        residuals = _validate_formal_outputs(target, prepared, source_uuid, target_uuid)
        if residuals:
            raise ValueError(f"Source player identity remains in migrated fields: {', '.join(residuals)}")
    except Exception:
        transaction.rollback()
        raise

    return ProgressMigrationResult(
        backup_path=backup_path,
        modified_files=transaction.modified_files,
        advancements_total=prepared.advancements_total,
        advancements_completed=prepared.advancements_completed,
        advancements_incomplete=prepared.advancements_incomplete,
        stats_categories=prepared.stats_categories,
        stats_records=prepared.stats_records,
        ftb_quest_task_progress=prepared.ftb_quest_task_progress,
        ftb_quest_started=prepared.ftb_quest_started,
        ftb_quest_completed=prepared.ftb_quest_completed,
        ftb_quest_claimed_rewards=prepared.ftb_quest_claimed_rewards,
        ftb_team_owner=prepared.ftb_team_owner,
        ftb_team_rank=prepared.ftb_team_rank,
        waystones=prepared.waystones,
        endinglib_records=prepared.endinglib_records,
        source_identity_residuals=residuals,
    )

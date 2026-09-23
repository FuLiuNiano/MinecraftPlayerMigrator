from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from nbtlib.tag import IntArray

from .full_models import FullMigrationPlan, FullVerificationResult, ModuleStatus
from .nbt_codec import (
    deep_compare_nbt,
    get_inventory,
    get_player_uuid,
    has_curios,
    load_nbt_file,
    nbt_root,
    player_from_level,
)
from .snbt_codec import SnbtParseError, direct_compound_entries, parse_snbt
from .uuid_utils import uuid_to_int_array


def _selected(plan: FullMigrationPlan, name: str) -> bool:
    if name == "player":
        return True
    module = plan.compatibility_report.modules.get(name)
    return bool(
        plan.options.enabled(name)
        and module is not None
        and module.status in {ModuleStatus.READY, ModuleStatus.SUCCESS}
    )


def _count(value: object, key: str) -> int | None:
    raw = value.get(key) if isinstance(value, dict) else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _target_path(plan: FullMigrationPlan, relative: str) -> Path:
    return plan.target_world / relative


def _error(errors: list[str], module: str, message: str) -> None:
    errors.append(f"{module}: {message}")


def _verify_player(plan: FullMigrationPlan, errors: list[str]) -> tuple[bool, bool]:
    module = plan.compatibility_report.modules.get("player")
    if module is None:
        _error(errors, "player", "兼容性报告缺少人物模块")
        return False, False
    player_path = _target_path(plan, f"playerdata/{plan.target_uuid}.dat")
    level_path = _target_path(plan, "level.dat")
    try:
        player = nbt_root(load_nbt_file(player_path))
        level_player = player_from_level(load_nbt_file(level_path))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        _error(errors, "player", f"目标 NBT 无法解析：{exc}")
        return False, False

    player_equal = deep_compare_nbt(player, level_player) is None
    if not player_equal:
        _error(errors, "player", "level.dat Data.Player 与 playerdata 不一致")
    if get_player_uuid(player) != plan.target_uuid:
        _error(errors, "player", "playerdata UUID 不等于目标 UUID")
    if get_player_uuid(level_player) != plan.target_uuid:
        _error(errors, "player", "level.dat Data.Player UUID 不等于目标 UUID")

    counts = module.counts
    inventory_expected = _count(counts, "inventory")
    if inventory_expected is not None and len(get_inventory(player)) != inventory_expected:
        _error(
            errors,
            "player",
            f"Inventory 数量不一致：实际 {len(get_inventory(player))}，预期 {inventory_expected}",
        )
    if (
        "xp_level" in counts
        and counts["xp_level"] != "未知"
        and int(player.get("XpLevel", -1)) != int(counts["xp_level"])
    ):
        _error(errors, "player", "XpLevel 不一致")
    if (
        "xp_total" in counts
        and counts["xp_total"] != "未知"
        and int(player.get("XpTotal", -1)) != int(counts["xp_total"])
    ):
        _error(errors, "player", "XpTotal 不一致")
    if counts.get("forge_caps") is True and "ForgeCaps" not in player:
        _error(errors, "player", "ForgeCaps 丢失")
    if counts.get("curios") is True and not has_curios(player):
        _error(errors, "player", "Curios capability 丢失")
    return not any(item.startswith("player:") for item in errors), player_equal


def _verify_advancements(plan: FullMigrationPlan, errors: list[str]) -> bool:
    if not _selected(plan, "advancements"):
        return True
    module = plan.compatibility_report.modules["advancements"]
    path = _target_path(plan, f"advancements/{plan.target_uuid}.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        entries = [item for item in value.values() if isinstance(item, dict)]
        actual = {"total": len(entries), "completed": sum(item.get("done") is True for item in entries)}
        actual["unfinished"] = actual["total"] - actual["completed"]
        for key, value in actual.items():
            expected = _count(module.counts, key)
            if expected is not None and value != expected:
                _error(errors, "advancements", f"{key} 不一致：实际 {value}，预期 {expected}")
        return not any(item.startswith("advancements:") for item in errors)
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        _error(errors, "advancements", f"JSON 无法解析：{exc}")
        return False


def _verify_stats(plan: FullMigrationPlan, errors: list[str]) -> bool:
    if not _selected(plan, "stats"):
        return True
    module = plan.compatibility_report.modules["stats"]
    path = _target_path(plan, f"stats/{plan.target_uuid}.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        stats = value["stats"]
        actual = {
            "DataVersion": value.get("DataVersion", "未知"),
            "categories": len(stats),
            "records": sum(len(entries) for entries in stats.values()),
        }
        for key, value in actual.items():
            expected = module.counts.get(key)
            if expected is not None and value != expected:
                _error(errors, "stats", f"{key} 不一致：实际 {value}，预期 {expected}")
        return not any(item.startswith("stats:") for item in errors)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        _error(errors, "stats", f"JSON 无法解析：{exc}")
        return False


def _snbt_value(document: Any, key: str) -> str:
    return document.string_value(document.root_field(key))


def _verify_quests(plan: FullMigrationPlan, errors: list[str], residuals: list[str]) -> bool:
    if not _selected(plan, "ftb_quests"):
        return True
    module = plan.compatibility_report.modules["ftb_quests"]
    path = _target_path(plan, f"ftbquests/{plan.target_uuid}.snbt")
    try:
        document = parse_snbt(path.read_text(encoding="utf-8"))
        expected_fields = (
            "uuid",
            "name",
            "task_progress",
            "started",
            "completed",
            "claimed_rewards",
            "player_data",
        )
        for field in expected_fields:
            document.root_field(field)
        target_compact = plan.target_uuid.replace("-", "")
        source_compact = plan.source_uuid.replace("-", "")
        if _snbt_value(document, "uuid").lower() != target_compact:
            _error(errors, "ftb_quests", "root.uuid 不等于目标 compact UUID")
            residuals.append("ftbquests.uuid")
        quest_name = _snbt_value(document, "name")
        if "#" not in quest_name or not quest_name.rsplit("#", 1)[1].lower().startswith(target_compact[:8]):
            _error(errors, "ftb_quests", "name 不包含目标玩家短 UUID")
        for key in ("task_progress", "started", "completed", "claimed_rewards"):
            actual = len(direct_compound_entries(document, key))
            expected = _count(module.counts, key)
            if expected is not None and actual != expected:
                _error(errors, "ftb_quests", f"{key} 数量不一致：实际 {actual}，预期 {expected}")
        for entry in direct_compound_entries(document, "claimed_rewards"):
            if not entry.key.startswith(target_compact + ":"):
                _error(errors, "ftb_quests", "claimed_rewards 存在非目标玩家前缀")
            if plan.source_uuid != plan.target_uuid and entry.key.startswith(source_compact + ":"):
                residuals.append("ftbquests.claimed_rewards")
        if (
            plan.source_uuid != plan.target_uuid
            and source_compact in path.read_text(encoding="utf-8")
            and _snbt_value(document, "uuid").lower() == source_compact
        ):
            # This is intentionally scoped to player identity fields above; the
            # whole world is never scanned for the source UUID.
            residuals.append("ftbquests.uuid")
        return not any(item.startswith("ftb_quests:") for item in errors)
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, SnbtParseError) as exc:
        _error(errors, "ftb_quests", f"SNBT 无法验证：{exc}")
        return False


def _verify_team(plan: FullMigrationPlan, errors: list[str], residuals: list[str]) -> bool:
    if not _selected(plan, "ftb_team"):
        return True
    module = plan.compatibility_report.modules["ftb_team"]
    path = _target_path(plan, f"ftbteams/player/{plan.target_uuid}.snbt")
    try:
        document = parse_snbt(path.read_text(encoding="utf-8"))
        target_uuid = _snbt_value(document, "id")
        if target_uuid != plan.target_uuid:
            _error(errors, "ftb_team", "个人 Team id 不等于目标 UUID")
            residuals.append("ftbteams.player.id")
        if _snbt_value(document, "type") != "player":
            _error(errors, "ftb_team", "目标 Team 不是个人 Team")
        owner = _snbt_value(document, "player_name")
        expected_owner = module.counts.get("owner")
        if expected_owner is not None and owner != expected_owner:
            _error(errors, "ftb_team", "Team owner 不一致")
        ranks = document.compound_value(document.root_field("ranks"))
        if len(ranks.fields) != 1 or ranks.fields[0].key != plan.target_uuid:
            _error(errors, "ftb_team", "目标 Team 成员或 rank key 不正确")
            residuals.append("ftbteams.player.ranks")
        elif document.string_value(ranks.fields[0]) != "owner":
            _error(errors, "ftb_team", "目标玩家 rank 不是 owner")
        if plan.source_uuid != plan.target_uuid and any(
            field.key == plan.source_uuid for field in ranks.fields
        ):
            residuals.append("ftbteams.player.ranks")
        return not any(item.startswith("ftb_team:") for item in errors)
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, SnbtParseError) as exc:
        _error(errors, "ftb_team", f"SNBT 无法验证：{exc}")
        return False


def _waystone_records(root: Any) -> list[Any]:
    data = root.get("data")
    entries = data.get("Waystones") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise TypeError("缺少 data.Waystones")
    return entries


def _verify_waystones(plan: FullMigrationPlan, errors: list[str], residuals: list[str]) -> bool:
    if not _selected(plan, "waystones"):
        return True
    module = plan.compatibility_report.modules["waystones"]
    path = _target_path(plan, "data/waystones.dat")
    try:
        target_entries = _waystone_records(nbt_root(load_nbt_file(path)))
        expected_total = _count(module.counts, "total")
        if expected_total is not None and len(target_entries) != expected_total:
            _error(errors, "waystones", "Waystone 总数发生变化")
        source_entries = None
        if plan.source is not None and plan.source.exists("data/waystones.dat"):
            source_entries = _waystone_records(
                nbt_root(load_nbt_file_from_bytes(plan.source.read_bytes("data/waystones.dat")))
            )
        source_ints = tuple(int(value) for value in uuid_to_int_array(plan.source_uuid))
        target_ints = tuple(int(value) for value in uuid_to_int_array(plan.target_uuid))
        owned = 0
        for index, target_entry in enumerate(target_entries):
            owner = target_entry.get("OwnerUid") if isinstance(target_entry, dict) else None
            if isinstance(owner, IntArray) and tuple(int(value) for value in owner) == target_ints:
                if source_entries is not None and index < len(source_entries):
                    source_entry = source_entries[index]
                    source_owner = source_entry.get("OwnerUid")
                    if isinstance(source_owner, IntArray) and tuple(int(value) for value in source_owner) == source_ints:
                        expected_entry = copy.deepcopy(source_entry)
                        expected_entry["OwnerUid"] = IntArray(target_ints)
                        if deep_compare_nbt(expected_entry, target_entry):
                            _error(errors, "waystones", f"第 {index} 个 Waystone 非 OwnerUid 字段发生变化")
                owned += 1
            elif (
                plan.source_uuid != plan.target_uuid
                and isinstance(owner, IntArray)
                and tuple(int(value) for value in owner) == source_ints
            ):
                residuals.append(f"waystones.OwnerUid[{index}]")
        expected_owned = _count(module.counts, "owned")
        if expected_owned is not None and owned != expected_owned:
            _error(errors, "waystones", f"目标 OwnerUid 数量不一致：实际 {owned}，预期 {expected_owned}")
        return not any(item.startswith("waystones:") for item in errors)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        _error(errors, "waystones", f"NBT 无法验证：{exc}")
        return False


def _verify_endinglib(plan: FullMigrationPlan, errors: list[str], residuals: list[str]) -> bool:
    if not _selected(plan, "endinglib"):
        return True
    module = plan.compatibility_report.modules["endinglib"]
    path = _target_path(plan, "data/endinglib_saved_data.dat")
    try:
        root = nbt_root(load_nbt_file(path))
        data = root["data"]
        source_ints = tuple(int(value) for value in uuid_to_int_array(plan.source_uuid))
        target_ints = tuple(int(value) for value in uuid_to_int_array(plan.target_uuid))
        total = 0
        for section in ("PlayersDisabledOverlays", "PlayersDisabledInputPermissions"):
            entries = data[section]
            count = 0
            for index, entry in enumerate(entries):
                identifier = entry.get("id")
                if isinstance(identifier, IntArray) and tuple(int(value) for value in identifier) == target_ints:
                    count += 1
                elif (
                    plan.source_uuid != plan.target_uuid
                    and isinstance(identifier, IntArray)
                    and tuple(int(value) for value in identifier) == source_ints
                ):
                    residuals.append(f"endinglib.{section}[{index}]")
            expected = _count(module.counts, section)
            if expected is not None and count != expected:
                _error(errors, "endinglib", f"{section} 数量不一致：实际 {count}，预期 {expected}")
            total += count
        expected_total = _count(module.counts, "total")
        if expected_total is not None and total != expected_total:
            _error(errors, "endinglib", f"总迁移数量不一致：实际 {total}，预期 {expected_total}")
        return not any(item.startswith("endinglib:") for item in errors)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        _error(errors, "endinglib", f"NBT 无法验证：{exc}")
        return False


def _verify_cosarmor(plan: FullMigrationPlan, errors: list[str]) -> bool:
    if not _selected(plan, "cosarmor"):
        return True
    path = _target_path(plan, f"playerdata/{plan.target_uuid}.cosarmor")
    try:
        load_nbt_file(path)
        return True
    except (OSError, KeyError, TypeError, ValueError) as exc:
        _error(errors, "cosarmor", f"Cosmetic Armor 无法解析：{exc}")
        return False


def load_nbt_file_from_bytes(data: bytes):
    """Local helper kept here to avoid changing the verified NBT codec."""

    from .nbt_codec import load_nbt_bytes

    return load_nbt_bytes(data)


def verify_full_migration(plan: FullMigrationPlan) -> FullVerificationResult:
    """Verify selected complete-migration outputs without writing any file."""

    errors: list[str] = []
    warnings: list[str] = []
    residuals: list[str] = []
    player_ok, player_equal = _verify_player(plan, errors)
    advancements_ok = _verify_advancements(plan, errors)
    stats_ok = _verify_stats(plan, errors)
    ftb_quests_ok = _verify_quests(plan, errors, residuals)
    ftb_team_ok = _verify_team(plan, errors, residuals)
    waystones_ok = _verify_waystones(plan, errors, residuals)
    endinglib_ok = _verify_endinglib(plan, errors, residuals)
    cosarmor_ok = _verify_cosarmor(plan, errors)
    for item in residuals:
        if item not in errors:
            errors.append(f"source identity remains: {item}")
    if plan.compatibility_report.warnings:
        warnings.extend(plan.compatibility_report.warnings)
    return FullVerificationResult(
        player_ok=player_ok,
        player_leveldat_equal=player_equal,
        advancements_ok=advancements_ok,
        stats_ok=stats_ok,
        ftb_quests_ok=ftb_quests_ok,
        ftb_team_ok=ftb_team_ok,
        waystones_ok=waystones_ok,
        endinglib_ok=endinglib_ok,
        cosarmor_ok=cosarmor_ok,
        source_identity_residuals=tuple(dict.fromkeys(residuals)),
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


verify_full_plan = verify_full_migration

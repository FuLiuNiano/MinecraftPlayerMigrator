from __future__ import annotations

import json
import math
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nbtlib.tag import IntArray

from .backup import directory_size
from .full_models import CompatibilityReport, ModuleReport, ModuleStatus
from .models import IdentityEvidence
from .nbt_codec import (
    get_dimension,
    get_ender_count,
    get_inventory,
    get_player_uuid,
    get_position,
    has_curios,
    load_nbt_bytes,
    load_nbt_file,
    nbt_root,
    player_from_level,
)
from .player_scanner import load_source_names
from .snbt_codec import SnbtParseError, direct_compound_entries, parse_snbt
from .uuid_utils import normalize_uuid, uuid_to_int_array
from .version_detector import (
    VersionStatus,
    compare_minecraft_versions,
    detect_world_version,
)
from .zip_reader import WorldSource, resolve_world_source

MODULE_DISPLAY_NAMES = {
    "player": "人物数据",
    "advancements": "Advancements",
    "stats": "Stats",
    "ftb_quests": "FTB Quests",
    "ftb_team": "FTB Teams",
    "waystones": "Waystones",
    "endinglib": "Ending Library",
    "cosarmor": "Cosmetic Armor",
}

_MODULE_ORDER = tuple(MODULE_DISPLAY_NAMES)


def _source_path(source: WorldSource, relative: str) -> str:
    return f"{source.label.rstrip('/')}/{relative}"


def _target_path(target: Path, relative: str) -> str:
    return str(target / relative)


def _ready_or_missing(
    *,
    name: str,
    source: WorldSource,
    target: Path,
    relative: str,
    target_relative: str | None = None,
    counts: dict[str, int | float | str | bool],
    optional: bool = True,
) -> ModuleReport:
    source_path = _source_path(source, relative)
    target_path = target / (target_relative or relative)
    if not source.exists(relative):
        return ModuleReport(
            name=name,
            display_name=MODULE_DISPLAY_NAMES[name],
            status=ModuleStatus.SKIPPED_NOT_FOUND,
            source_paths=(source_path,),
            target_paths=(str(target_path),),
            counts=counts,
            enabled=False,
            optional=optional,
        )
    return ModuleReport(
        name=name,
        display_name=MODULE_DISPLAY_NAMES[name],
        status=ModuleStatus.READY,
        source_paths=(source_path,),
        target_paths=(str(target_path),),
        counts=counts,
        will_overwrite=target_path.is_file(),
        enabled=True,
        optional=optional,
    )


def _failed_report(
    name: str,
    source: WorldSource,
    target: Path,
    relative: str,
    error: str,
    *,
    status: ModuleStatus = ModuleStatus.FAILED,
    warning: str | None = None,
    optional: bool = True,
    target_relative: str | None = None,
) -> ModuleReport:
    warnings = (warning,) if warning else ()
    return ModuleReport(
        name=name,
        display_name=MODULE_DISPLAY_NAMES[name],
        status=status,
        source_paths=(_source_path(source, relative),),
        target_paths=(str(target / (target_relative or relative)),),
        warnings=warnings,
        error=error,
        enabled=False,
        optional=optional,
    )


def _json_report(
    source: WorldSource,
    target: Path,
    module_name: str,
    relative: str,
    target_relative: str,
    counts: dict[str, int | float | str | bool] | None = None,
) -> ModuleReport:
    report = _ready_or_missing(
        name=module_name,
        source=source,
        target=target,
        relative=relative,
        target_relative=target_relative,
        counts=counts or {},
    )
    if report.status is not ModuleStatus.READY:
        return report
    try:
        value = json.loads(source.read_text(relative))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _failed_report(
            module_name,
            source,
            target,
            relative,
            f"JSON 无法解析：{exc}",
            target_relative=target_relative,
        )
    if not isinstance(value, dict):
        return _failed_report(
            module_name,
            source,
            target,
            relative,
            "JSON 根节点不是对象",
            target_relative=target_relative,
        )

    if module_name == "advancements":
        entries = [item for item in value.values() if isinstance(item, dict)]
        report.counts.update(
            total=len(entries),
            completed=sum(item.get("done") is True for item in entries),
        )
        report.counts["unfinished"] = report.counts["total"] - report.counts["completed"]
    else:
        stats = value.get("stats")
        if not isinstance(stats, dict) or any(
            not isinstance(entries, dict) for entries in stats.values()
        ):
            return _failed_report(
                module_name,
                source,
                target,
                relative,
                "Stats JSON 缺少有效的 stats 对象",
                target_relative=target_relative,
            )
        report.counts.update(
            DataVersion=value.get("DataVersion", "未知"),
            categories=len(stats),
            records=sum(len(entries) for entries in stats.values()),
        )
    return report


def _field_string(document: Any, key: str) -> str:
    return document.string_value(document.root_field(key))


def _quest_report(
    source: WorldSource,
    target: Path,
    source_uuid: str,
    target_uuid: str,
    source_username: str | None,
) -> ModuleReport:
    relative = f"ftbquests/{source_uuid}.snbt"
    missing = _ready_or_missing(
        name="ftb_quests",
        source=source,
        target=target,
        relative=relative,
        target_relative=f"ftbquests/{target_uuid}.snbt",
        counts={},
    )
    if missing.status is not ModuleStatus.READY:
        return missing

    try:
        document = parse_snbt(source.read_text(relative))
        required = (
            "uuid",
            "name",
            "task_progress",
            "started",
            "completed",
            "claimed_rewards",
            "player_data",
        )
        for field in required:
            document.root_field(field)
        compact_uuid = source_uuid.replace("-", "")
        if _field_string(document, "uuid").lower() != compact_uuid:
            raise ValueError("FTB Quests uuid 不是源玩家 compact UUID")
        quest_name = _field_string(document, "name")
        if "#" not in quest_name:
            raise ValueError("FTB Quests name 不符合 username#shortuuid 格式")
        username, short_uuid = quest_name.rsplit("#", 1)
        if not username or not short_uuid or not compact_uuid.startswith(short_uuid.lower()):
            raise ValueError("FTB Quests name 中的短 UUID 与源玩家不匹配")
        if source_username and username != source_username:
            raise ValueError("FTB Quests name 用户名与身份信息不匹配")

        counts = {
            key: len(direct_compound_entries(document, key))
            for key in ("task_progress", "started", "completed", "claimed_rewards")
        }
        for entry in direct_compound_entries(document, "claimed_rewards"):
            if not entry.key.startswith(compact_uuid + ":"):
                raise ValueError("无法确认 claimed_rewards 的玩家 UUID 前缀结构")

        report = _ready_or_missing(
            name="ftb_quests",
            source=source,
            target=target,
            relative=relative,
            target_relative=f"ftbquests/{target_uuid}.snbt",
            counts=counts,
        )
        report.warnings = (f"检测到玩家任务数据：{username}#{short_uuid}",)
        return report
    except (KeyError, TypeError, ValueError, SnbtParseError) as exc:
        return _failed_report(
            "ftb_quests",
            source,
            target,
            relative,
            f"未验证的 FTB Quests 玩家结构：{exc}",
            status=ModuleStatus.SKIPPED_UNSUPPORTED,
            warning="检测到未验证的 FTB Quests 数据结构，已跳过任务迁移。",
            target_relative=f"ftbquests/{target_uuid}.snbt",
        )


def _team_report(
    source: WorldSource,
    target: Path,
    source_uuid: str,
    target_uuid: str,
    source_username: str | None,
) -> ModuleReport:
    relative = f"ftbteams/player/{source_uuid}.snbt"
    missing = _ready_or_missing(
        name="ftb_team",
        source=source,
        target=target,
        relative=relative,
        target_relative=f"ftbteams/player/{target_uuid}.snbt",
        counts={},
    )
    if missing.status is not ModuleStatus.READY:
        return missing

    try:
        document = parse_snbt(source.read_text(relative))
        team_id = _field_string(document, "id")
        team_type = _field_string(document, "type")
        player_name = _field_string(document, "player_name")
        ranks = document.compound_value(document.root_field("ranks"))
        if team_id != source_uuid:
            raise ValueError("个人 Team id 与源玩家 UUID 不匹配")
        if team_type != "player":
            return _failed_report(
                "ftb_team",
                source,
                target,
                relative,
                "检测到共享或多人 FTB Team",
                status=ModuleStatus.SKIPPED_UNSUPPORTED,
                warning="检测到多人 FTB Team，v1.1 不自动迁移。",
                target_relative=f"ftbteams/player/{target_uuid}.snbt",
            )
        if len(ranks.fields) != 1 or ranks.fields[0].key != source_uuid:
            return _failed_report(
                "ftb_team",
                source,
                target,
                relative,
                "个人 Team 成员数量不是 1",
                status=ModuleStatus.SKIPPED_UNSUPPORTED,
                warning="检测到多人 FTB Team，v1.1 不自动迁移。",
                target_relative=f"ftbteams/player/{target_uuid}.snbt",
            )
        rank = document.string_value(ranks.fields[0])
        if rank != "owner":
            raise ValueError("个人 Team 的源玩家 rank 不是 owner")
        if source_username and player_name != source_username:
            raise ValueError("FTB Team 用户名与身份信息不匹配")
        report = _ready_or_missing(
            name="ftb_team",
            source=source,
            target=target,
            relative=relative,
            target_relative=f"ftbteams/player/{target_uuid}.snbt",
            counts={"members": 1, "owner": player_name, "rank": rank},
        )
        return report
    except (KeyError, TypeError, ValueError, SnbtParseError) as exc:
        return _failed_report(
            "ftb_team",
            source,
            target,
            relative,
            f"未验证的 FTB Teams 个人 Team 结构：{exc}",
            target_relative=f"ftbteams/player/{target_uuid}.snbt",
        )


def _waystones_report(
    source: WorldSource,
    target: Path,
    source_uuid: str,
) -> ModuleReport:
    relative = "data/waystones.dat"
    missing = _ready_or_missing(
        name="waystones",
        source=source,
        target=target,
        relative=relative,
        counts={},
    )
    if missing.status is not ModuleStatus.READY:
        return missing
    try:
        root = nbt_root(load_nbt_bytes(source.read_bytes(relative)))
        data = root.get("data")
        waystones = data.get("Waystones") if isinstance(data, dict) else None
        if not isinstance(waystones, list):
            raise TypeError("缺少 data.Waystones 列表")
        source_ints = tuple(int(value) for value in uuid_to_int_array(source_uuid))
        owned = 0
        for waystone in waystones:
            owner = waystone.get("OwnerUid") if isinstance(waystone, dict) else None
            # Unowned waystones legitimately omit OwnerUid.  Only an existing
            # owner tag with an unexpected type is unsupported.
            if owner is None:
                continue
            if not isinstance(owner, IntArray):
                raise TypeError("Waystones OwnerUid 不是已验证的 IntArray 类型")
            if tuple(int(value) for value in owner) == source_ints:
                owned += 1
        if owned == 0:
            return _failed_report(
                "waystones",
                source,
                target,
                relative,
                "没有发现属于源玩家的 Waystone",
                status=ModuleStatus.SKIPPED_NOT_FOUND,
            )
        return _ready_or_missing(
            name="waystones",
            source=source,
            target=target,
            relative=relative,
            counts={"total": len(waystones), "owned": owned},
        )
    except (OSError, TypeError, ValueError) as exc:
        return _failed_report("waystones", source, target, relative, f"Waystones 无法安全分析：{exc}")


def _endinglib_report(
    source: WorldSource,
    target: Path,
    source_uuid: str,
) -> ModuleReport:
    relative = "data/endinglib_saved_data.dat"
    missing = _ready_or_missing(
        name="endinglib",
        source=source,
        target=target,
        relative=relative,
        counts={},
    )
    if missing.status is not ModuleStatus.READY:
        return missing
    try:
        root = nbt_root(load_nbt_bytes(source.read_bytes(relative)))
        data = root.get("data")
        if not isinstance(data, dict):
            raise TypeError("缺少 Ending Library data compound")
        source_ints = tuple(int(value) for value in uuid_to_int_array(source_uuid))
        counts: dict[str, int] = {}
        total = 0
        for section in ("PlayersDisabledOverlays", "PlayersDisabledInputPermissions"):
            entries = data.get(section)
            if not isinstance(entries, list):
                raise TypeError(f"缺少 Ending Library 区段：{section}")
            for entry in entries:
                identifier = entry.get("id") if isinstance(entry, dict) else None
                if not isinstance(identifier, IntArray):
                    raise TypeError(f"Ending Library {section}.id 不是已验证的 IntArray 类型")
            matched = sum(
                tuple(int(value) for value in entry["id"]) == source_ints for entry in entries
            )
            counts[section] = matched
            total += matched
        if total == 0:
            return _failed_report(
                "endinglib",
                source,
                target,
                relative,
                "没有发现属于源玩家的 Ending Library 设置",
                status=ModuleStatus.SKIPPED_NOT_FOUND,
            )
        counts["total"] = total
        return _ready_or_missing(
            name="endinglib",
            source=source,
            target=target,
            relative=relative,
            counts=counts,
        )
    except (OSError, TypeError, ValueError) as exc:
        return _failed_report(
            "endinglib", source, target, relative, f"Ending Library 无法安全分析：{exc}"
        )


def _cosarmor_report(
    source: WorldSource,
    target: Path,
    source_uuid: str,
    target_uuid: str,
) -> ModuleReport:
    relative = f"playerdata/{source_uuid}.cosarmor"
    missing = _ready_or_missing(
        name="cosarmor",
        source=source,
        target=target,
        relative=relative,
        target_relative=f"playerdata/{target_uuid}.cosarmor",
        counts={},
    )
    if missing.status is not ModuleStatus.READY:
        return missing
    try:
        load_nbt_bytes(source.read_bytes(relative))
    except (OSError, ValueError, TypeError) as exc:
        return _failed_report(
            "cosarmor",
            source,
            target,
            relative,
            f"Cosmetic Armor 无法解析：{exc}",
            target_relative=f"playerdata/{target_uuid}.cosarmor",
        )
    return _ready_or_missing(
        name="cosarmor",
        source=source,
        target=target,
        relative=relative,
        target_relative=f"playerdata/{target_uuid}.cosarmor",
        counts={"parseable": True},
    )


def _identity_state(
    evidence: tuple[IdentityEvidence, ...], target_uuid: str
) -> tuple[list[str], list[str]]:
    blocking: list[str] = []
    warnings: list[str] = []
    if not evidence:
        blocking.append("没有检测到本地 UUID 证据")
        return blocking, warnings
    top_score = max(item.score for item in evidence)
    top = [item for item in evidence if item.score == top_score]
    top_uuids = {item.uuid for item in top}
    if top_score <= 0:
        blocking.append("本地 UUID 证据可信度为零")
    if len(top_uuids) > 1:
        blocking.append("本地 UUID 多证据存在冲突")
    if target_uuid not in top_uuids:
        blocking.append("指定的目标 UUID 不是最高可信度身份证据")
    if top_score == 2:
        warnings.append("本地 UUID 证据可信度为中，请在迁移前再次确认目标世界")
    return blocking, warnings


def _target_username(
    evidence: tuple[IdentityEvidence, ...], target_uuid: str
) -> str | None:
    for item in evidence:
        if item.uuid == target_uuid and item.username:
            return item.username
    return None


def scan_compatibility(
    source_world: WorldSource | str | Path,
    source_uuid: str,
    target_world: str | Path,
    target_uuid: str,
    identity_evidence: Sequence[IdentityEvidence] | IdentityEvidence | None = None,
    *,
    source_username: str | None = None,
    target_username: str | None = None,
) -> CompatibilityReport:
    """Build a read-only compatibility report for a complete migration."""

    source = (
        source_world
        if isinstance(source_world, WorldSource)
        else resolve_world_source(source_world)
    )
    source_uuid = normalize_uuid(source_uuid)
    target_uuid = normalize_uuid(target_uuid)
    target = Path(target_world).expanduser().resolve()
    source_version = detect_world_version(source)
    target_version = detect_world_version(target)
    version_compatibility = compare_minecraft_versions(source_version, target_version)
    if isinstance(identity_evidence, IdentityEvidence):
        evidence = (identity_evidence,)
    elif identity_evidence is None:
        evidence = ()
    else:
        evidence = tuple(identity_evidence)

    if source_username is None:
        try:
            source_username = load_source_names(source).get(source_uuid)
        except (OSError, ValueError, TypeError):
            source_username = None
    if target_username is None:
        target_username = _target_username(evidence, target_uuid)

    modules: dict[str, ModuleReport] = {}
    blocking, warnings = _identity_state(evidence, target_uuid) if identity_evidence is not None else ([], [])
    if version_compatibility.status is not VersionStatus.MATCH:
        blocking.append(
            "Minecraft 版本无法安全匹配："
            f"{version_compatibility.status.value}"
        )
    warnings.extend(version_compatibility.warnings)

    if not target.is_dir():
        blocking.append(f"目标 world 不存在：{target}")
        world_size = 0
        free_space = 0
    else:
        try:
            world_size = directory_size(target)
            free_space = shutil.disk_usage(target).free
        except OSError as exc:
            world_size = 0
            free_space = 0
            blocking.append(f"无法读取目标 world 大小或磁盘空间：{exc}")
    required_backup_space = math.ceil(world_size * 1.2)
    if target.is_dir() and free_space < required_backup_space:
        blocking.append(
            f"磁盘空间不足：需要至少 {required_backup_space} bytes，可用 {free_space} bytes"
        )

    if not source.is_zip and source.location.resolve() == target:
        blocking.append("源 world 与目标 world 相同，禁止生成迁移计划")

    player_relative = f"playerdata/{source_uuid}.dat"
    target_player_relative = f"playerdata/{target_uuid}.dat"
    player_source_path = _source_path(source, player_relative)
    player_target_path = target / target_player_relative
    level_path = target / "level.dat"
    player_warnings: list[str] = []
    player_counts: dict[str, int | float | str | bool] = {}
    player_status = ModuleStatus.READY
    player_error: str | None = None

    try:
        if not source.exists(player_relative):
            raise FileNotFoundError("源玩家 playerdata 不存在")
        source_player = nbt_root(load_nbt_bytes(source.read_bytes(player_relative)))
        if get_player_uuid(source_player) != source_uuid:
            raise ValueError("源文件名与 Player.UUID 不一致")
        inventory = get_inventory(source_player)
        player_counts.update(
            inventory=len(inventory),
            xp_level=int(source_player["XpLevel"]) if "XpLevel" in source_player else "未知",
            xp_total=int(source_player["XpTotal"]) if "XpTotal" in source_player else "未知",
            ender_items=get_ender_count(source_player),
            forge_caps="ForgeCaps" in source_player,
            curios=has_curios(source_player),
            position=repr(get_position(source_player)),
            dimension=get_dimension(source_player) or "未知",
        )
        if not level_path.is_file():
            raise FileNotFoundError("目标 level.dat 不存在")
        if not player_target_path.is_file():
            raise FileNotFoundError("目标 playerdata 不存在")
        target_player = nbt_root(load_nbt_file(player_target_path))
        target_level_player = player_from_level(load_nbt_file(level_path))
        if get_player_uuid(target_player) != target_uuid:
            blocking.append("目标 playerdata 的 UUID 与目标 UUID 不一致")
            player_warnings.append("目标 playerdata 当前身份与指定目标 UUID 不一致")
        if get_player_uuid(target_level_player) != target_uuid:
            blocking.append("目标 level.dat Data.Player 的 UUID 与目标 UUID 不一致")
            player_warnings.append("目标 level.dat Data.Player 当前身份与指定目标 UUID 不一致")
        from .nbt_codec import deep_compare_nbt

        if deep_compare_nbt(target_player, target_level_player):
            player_warnings.append("目标 level.dat Data.Player 与目标 playerdata 当前不一致")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        player_status = ModuleStatus.BLOCKED
        player_error = str(exc)
        blocking.append(f"人物数据不可迁移：{exc}")

    modules["player"] = ModuleReport(
        name="player",
        display_name=MODULE_DISPLAY_NAMES["player"],
        status=player_status,
        source_paths=(player_source_path,),
        target_paths=(str(player_target_path), str(level_path)),
        counts=player_counts,
        warnings=tuple(player_warnings),
        error=player_error,
        will_overwrite=player_target_path.is_file() or level_path.is_file(),
        enabled=player_status is ModuleStatus.READY,
        optional=False,
    )

    modules["advancements"] = _json_report(
        source,
        target,
        "advancements",
        f"advancements/{source_uuid}.json",
        f"advancements/{target_uuid}.json",
    )
    modules["stats"] = _json_report(
        source,
        target,
        "stats",
        f"stats/{source_uuid}.json",
        f"stats/{target_uuid}.json",
    )
    modules["ftb_quests"] = _quest_report(source, target, source_uuid, target_uuid, source_username)
    modules["ftb_team"] = _team_report(source, target, source_uuid, target_uuid, source_username)
    modules["waystones"] = _waystones_report(source, target, source_uuid)
    modules["endinglib"] = _endinglib_report(source, target, source_uuid)
    modules["cosarmor"] = _cosarmor_report(source, target, source_uuid, target_uuid)

    unsupported: list[str] = []
    for name, module in modules.items():
        if module.status is ModuleStatus.SKIPPED_UNSUPPORTED:
            unsupported.append(name)
        if module.status is ModuleStatus.FAILED:
            blocking.append(f"{module.display_name} 分析失败：{module.error or '未知错误'}")
        if module.status is ModuleStatus.BLOCKED and name != "player":
            blocking.append(f"{module.display_name} 被阻止：{module.error or '未知原因'}")
        warnings.extend(module.warnings)

    return CompatibilityReport(
        source_label=source.label,
        target_world=target,
        source_uuid=source_uuid,
        target_uuid=target_uuid,
        source_username=source_username,
        target_username=target_username,
        identity_evidence=evidence,
        modules=modules,
        unsupported=tuple(unsupported),
        blocking_issues=tuple(dict.fromkeys(blocking)),
        warnings=tuple(dict.fromkeys(warnings)),
        world_size=world_size,
        free_space=free_space,
        required_backup_space=required_backup_space,
        source_version=source_version,
        target_version=target_version,
        version_status=version_compatibility.status,
        source=source,
    )


build_compatibility_report = scan_compatibility
scan_world_compatibility = scan_compatibility

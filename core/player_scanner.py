from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from .models import ItemPreview, PlayerInfo
from .nbt_codec import (
    get_dimension,
    get_ender_count,
    get_inventory,
    get_player_uuid,
    get_position,
    has_curios,
    item_count,
    item_id,
    item_slot,
    load_nbt_bytes,
    nbt_root,
)
from .uuid_utils import normalize_uuid
from .zip_reader import WorldSource

UUID_NAME_RE = re.compile(r'(?im)(?:^|[,{]\s*)(?:player_name|name)\s*:\s*"(?P<name>[^"]+)"')
UUID_FIELD_RE = re.compile(r'(?im)(?:^|[,{]\s*)(?:id|uuid)\s*:\s*"?(?P<uuid>[0-9a-f-]{32,36})"?')


def load_name_cache(instance_dir: Path | None) -> dict[str, str]:
    if instance_dir is None:
        return {}
    result: dict[str, str] = {}
    usercache = instance_dir / "usercache.json"
    if usercache.is_file():
        try:
            for record in json.loads(usercache.read_text(encoding="utf-8")):
                if record.get("uuid") and record.get("name"):
                    try:
                        result[normalize_uuid(str(record["uuid"]))] = str(record["name"])
                    except ValueError:
                        continue
        except (OSError, ValueError, TypeError):
            pass
    usernamecache = instance_dir / "usernamecache.json"
    if usernamecache.is_file():
        try:
            for uuid, name in json.loads(usernamecache.read_text(encoding="utf-8")).items():
                try:
                    result.setdefault(normalize_uuid(str(uuid)), str(name))
                except ValueError:
                    continue
        except (OSError, ValueError, TypeError):
            pass
    return result


def load_source_names(source: WorldSource) -> dict[str, str]:
    """Read explicit player names from source-side FTB data when available."""
    result: dict[str, str] = {}
    for filename in source.list_files("ftbteams/player", ".snbt"):
        try:
            text = source.read_text(f"ftbteams/player/{filename}")
        except (OSError, KeyError):
            continue
        uuid_match = UUID_FIELD_RE.search(text)
        name_match = re.search(r'(?im)\bplayer_name\s*:\s*"([^"]+)"', text)
        if uuid_match and name_match:
            try:
                result[normalize_uuid(uuid_match.group("uuid"))] = name_match.group(1)
            except ValueError:
                continue

    for filename in source.list_files("ftbquests", ".snbt"):
        try:
            text = source.read_text(f"ftbquests/{filename}")
        except (OSError, KeyError):
            continue
        uuid_match = UUID_FIELD_RE.search(text)
        name_match = UUID_NAME_RE.search(text)
        if uuid_match and name_match:
            try:
                result.setdefault(
                    normalize_uuid(uuid_match.group("uuid")), name_match.group("name")
                )
            except ValueError:
                continue
    return result


def scan_players(source: WorldSource, names: Mapping[str, str] | None = None) -> list[PlayerInfo]:
    names = names or {}
    players: list[PlayerInfo] = []
    for filename in source.list_player_files():
        uuid_from_filename = filename.removesuffix(".dat")
        player_file = load_nbt_bytes(source.read_bytes(f"playerdata/{filename}"))
        player = nbt_root(player_file)
        player_uuid = get_player_uuid(player) or uuid_from_filename
        inventory = get_inventory(player)
        previews = [
            ItemPreview(item_slot(item), item_id(item), item_count(item)) for item in inventory[:30]
        ]
        players.append(
            PlayerInfo(
                uuid=player_uuid,
                username=names.get(player_uuid),
                source_file=source.label + f"/playerdata/{filename}",
                inventory_count=len(inventory),
                inventory_preview=previews,
                xp_level=int(player["XpLevel"]) if "XpLevel" in player else None,
                xp_total=int(player["XpTotal"]) if "XpTotal" in player else None,
                position=get_position(player),
                dimension=get_dimension(player),
                health=float(player["Health"]) if "Health" in player else None,
                ender_items_count=get_ender_count(player),
                has_forge_caps="ForgeCaps" in player,
                has_curios=has_curios(player),
                raw=player,
            )
        )
    return players

from __future__ import annotations

from pathlib import Path

from .models import VerificationResult
from .nbt_codec import (
    compound_digest,
    get_inventory,
    get_player_uuid,
    get_position,
    has_curios,
    item_id,
    load_nbt_bytes,
    nbt_root,
    player_from_level,
)


def verify_player_files(
    playerdata_path: Path,
    level_path: Path,
    required_items: tuple[str, ...] = (),
) -> VerificationResult:
    player_file = load_nbt_bytes(playerdata_path.read_bytes())
    level_file = load_nbt_bytes(level_path.read_bytes())
    player = nbt_root(player_file)
    level_player = player_from_level(level_file)
    inventory = get_inventory(player)
    ids = {item_id(item) for item in inventory}
    result = VerificationResult(
        target_uuid=get_player_uuid(player) or "",
        inventory_count=len(inventory),
        xp_level=int(player["XpLevel"]) if "XpLevel" in player else None,
        xp_total=int(player["XpTotal"]) if "XpTotal" in player else None,
        position=get_position(player),
        has_forge_caps="ForgeCaps" in player,
        has_curios=has_curios(player),
        playerdata_leveldat_equal=compound_digest(player) == compound_digest(level_player),
        required_items_present={item: item in ids for item in required_items},
    )
    return result

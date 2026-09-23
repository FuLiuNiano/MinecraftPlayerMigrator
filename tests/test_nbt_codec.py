from copy import deepcopy

from nbtlib import Compound, File
from nbtlib.tag import Byte, Int, List, String

from core.nbt_codec import (
    compound_digest,
    get_player_uuid,
    load_nbt_bytes,
    nbt_root,
    player_from_level,
    replace_player_uuid,
    serialize_nbt,
    set_level_player,
)
from core.uuid_utils import uuid_to_int_array

SOURCE = "11111111-1111-4111-8111-111111111111"
TARGET = "22222222-2222-4222-8222-222222222222"


def make_player() -> Compound:
    return Compound(
        {
            "UUID": uuid_to_int_array(SOURCE),
            "Inventory": List[Compound](
                [Compound({"id": String("minecraft:bread"), "Count": Byte(3)})]
            ),
            "ForgeCaps": Compound({"example:capability": Compound({"value": Int(1)})}),
        }
    )


def test_uuid_change_keeps_unknown_compounds() -> None:
    source = make_player()
    migrated = replace_player_uuid(source, TARGET)
    assert get_player_uuid(migrated) == TARGET
    assert migrated["Inventory"][0]["id"] == "minecraft:bread"
    assert migrated["ForgeCaps"]["example:capability"]["value"] == 1
    assert get_player_uuid(source) == SOURCE


def test_level_player_matches_playerdata_after_round_trip() -> None:
    migrated = replace_player_uuid(make_player(), TARGET)
    level = File({"Data": Compound({"Player": deepcopy(migrated)})})
    level_with_player = set_level_player(level, migrated)
    player_file = File(deepcopy(migrated))
    parsed_player = load_nbt_bytes(serialize_nbt(player_file))
    parsed_level = load_nbt_bytes(serialize_nbt(level_with_player))
    assert get_player_uuid(parsed_player) == TARGET
    assert get_player_uuid(player_from_level(parsed_level)) == TARGET
    assert compound_digest(parsed_player) == compound_digest(player_from_level(parsed_level))


def test_gzip_nbt_round_trip_preserves_forge_caps_and_unknown_data() -> None:
    source = make_player()
    payload = serialize_nbt(File(source), gzipped=True)
    parsed = load_nbt_bytes(payload)
    assert parsed.gzipped is True
    assert nbt_root(parsed)["ForgeCaps"]["example:capability"]["value"] == 1
    assert nbt_root(parsed)["Inventory"][0]["id"] == "minecraft:bread"

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from nbtlib import Compound, File
from nbtlib.tag import Byte, Double, Float, Int, List, String

from core.nbt_codec import serialize_nbt
from core.uuid_utils import uuid_to_int_array

SOURCE_UUID = "11111111-1111-4111-8111-111111111111"
TARGET_UUID = "22222222-2222-4222-8222-222222222222"
TEST_USERNAME = "TestPlayer"


def make_player(uuid: str, *, inventory_count: int = 1) -> Compound:
    inventory = List[Compound](
        [
            Compound(
                {
                    "id": String("test:sword"),
                    "Count": Byte(inventory_count),
                    "Slot": Byte(0),
                }
            )
        ]
    )
    curios_item = Compound(
        {
            "id": String("test:curio"),
            "Count": Byte(1),
            "Slot": Byte(0),
        }
    )
    curios_slot = Compound(
        {
            "Identifier": String("ring"),
            "StacksHandler": Compound(
                {
                    "Stacks": Compound({"Items": List[Compound]([curios_item])}),
                }
            ),
        }
    )
    return Compound(
        {
            "UUID": uuid_to_int_array(uuid),
            "Inventory": inventory,
            "EnderItems": List[Compound]([]),
            "Pos": List[Double]([Double(144.089), Double(88), Double(309.634)]),
            "Dimension": String("minecraft:overworld"),
            "XpLevel": Int(64),
            "XpP": Float(0.5),
            "XpTotal": Int(73224),
            "Health": Float(20),
            "foodLevel": Int(20),
            "ForgeCaps": Compound(
                {
                    "curios:inventory": Compound({"Curios": List[Compound]([curios_slot])}),
                    "test:unknown_capability": Compound({"Nested": String("keep-me")}),
                }
            ),
            "UnknownModData": Compound({"Nested": Compound({"key": String("must-survive")})}),
        }
    )


def make_level(player: Compound) -> File:
    return File(
        {
            "Data": Compound(
                {
                    "Player": deepcopy(player),
                    "Version": Compound({"Name": String("1.20.1"), "Id": Int(3465)}),
                    "DataVersion": Int(3465),
                    "TestWorldMarker": String("preserve-world-data"),
                }
            )
        }
    )


def write_world(world: Path, player_uuid: str, *, target: bool = False) -> None:
    playerdata = world / "playerdata"
    playerdata.mkdir(parents=True, exist_ok=True)
    player = make_player(player_uuid, inventory_count=2 if target else 1)
    (playerdata / f"{player_uuid}.dat").write_bytes(serialize_nbt(File(player)))
    (world / "level.dat").write_bytes(serialize_nbt(make_level(player)))
    (world / "region").mkdir(exist_ok=True)
    (world / "region" / "r.0.0.mca").write_bytes(b"test-region")

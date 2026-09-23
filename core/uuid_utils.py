from __future__ import annotations

import struct
import uuid as uuid_module
from collections.abc import Iterable

from nbtlib.tag import IntArray, Long, String


def normalize_uuid(value: str | uuid_module.UUID) -> str:
    if isinstance(value, uuid_module.UUID):
        return str(value)
    text = str(value).strip().replace("-", "")
    if len(text) != 32:
        raise ValueError(f"Invalid UUID: {value!r}")
    return str(uuid_module.UUID(text))


def uuid_to_int_array(value: str | uuid_module.UUID) -> IntArray:
    parsed = uuid_module.UUID(normalize_uuid(value))
    return IntArray(struct.unpack(">4i", parsed.bytes))


def int_array_to_uuid(values: Iterable[int]) -> str:
    numbers = tuple(int(value) for value in values)
    if len(numbers) != 4:
        raise ValueError("Minecraft UUID IntArray must contain four integers")
    return str(uuid_module.UUID(bytes=struct.pack(">4i", *numbers)))


def uuid_to_most_least(value: str | uuid_module.UUID) -> tuple[Long, Long]:
    parsed = uuid_module.UUID(normalize_uuid(value))
    most = int.from_bytes(parsed.bytes[:8], "big", signed=True)
    least = int.from_bytes(parsed.bytes[8:], "big", signed=True)
    return Long(most), Long(least)


def most_least_to_uuid(most: int, least: int) -> str:
    raw = int(most).to_bytes(8, "big", signed=True) + int(least).to_bytes(8, "big", signed=True)
    return str(uuid_module.UUID(bytes=raw))


def uuid_tag_to_string(player: dict) -> str | None:
    tag = player.get("UUID")
    if isinstance(tag, IntArray):
        return int_array_to_uuid(tag)
    if isinstance(tag, String):
        return normalize_uuid(str(tag))
    if "UUIDMost" in player and "UUIDLeast" in player:
        return most_least_to_uuid(player["UUIDMost"], player["UUIDLeast"])
    return None


def set_player_uuid(player: dict, value: str | uuid_module.UUID) -> None:
    normalized = normalize_uuid(value)
    uuid_ints = uuid_to_int_array(normalized)
    uuid_tag = player.get("UUID")

    if isinstance(uuid_tag, IntArray):
        player["UUID"] = uuid_ints
    elif isinstance(uuid_tag, String):
        player["UUID"] = String(normalized)
    elif uuid_tag is not None:
        raise TypeError(f"Unsupported Player.UUID NBT type: {type(uuid_tag).__name__}")

    if "UUIDMost" in player or "UUIDLeast" in player:
        if "UUIDMost" not in player or "UUIDLeast" not in player:
            raise ValueError("Player UUIDMost/UUIDLeast pair is incomplete")
        most, least = uuid_to_most_least(normalized)
        player["UUIDMost"] = type(player["UUIDMost"])(most)
        player["UUIDLeast"] = type(player["UUIDLeast"])(least)

    if uuid_tag is None and "UUIDMost" not in player and "UUIDLeast" not in player:
        raise KeyError("Player UUID field was not found")

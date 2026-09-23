from __future__ import annotations

import copy
import gzip
import hashlib
import io
from collections.abc import Mapping, Sequence
from pathlib import Path

from nbtlib import Compound, File
from nbtlib.tag import Array, Int

from .uuid_utils import set_player_uuid, uuid_tag_to_string


def load_nbt_bytes(data: bytes) -> File:
    """Parse compressed or uncompressed big-endian NBT bytes."""
    gzipped = data[:2] == b"\x1f\x8b"
    raw = gzip.decompress(data) if gzipped else data
    parsed = File.parse(io.BytesIO(raw))
    # nbtlib represents a parsed root compound as File({"": Compound(...)}),
    # while File(Compound(...)) is commonly used by callers when constructing
    # synthetic fixtures.  Keep the parsed representation canonical so root
    # access is not accidentally mistaken for a player field named "".
    result = File({"": copy.deepcopy(parsed.root)})
    result.gzipped = gzipped
    return result


def load_nbt_file(path: Path) -> File:
    return load_nbt_bytes(path.read_bytes())


def nbt_root(value: File | Compound) -> Compound:
    """Return the actual root Compound for parsed and constructed File values."""
    if isinstance(value, File):
        if "" in value:
            root = value[""]
        else:
            root = Compound(value)
    else:
        root = value
    if not isinstance(root, Compound):
        raise TypeError("NBT root is not a Compound")
    return root


def serialize_nbt(value: File | Compound, *, gzipped: bool = True) -> bytes:
    raw_stream = io.BytesIO()
    File({"": clone_compound(nbt_root(value))}).write(raw_stream)
    raw = raw_stream.getvalue()
    return gzip.compress(raw, mtime=0) if gzipped else raw


def clone_compound(value: Compound | File) -> Compound:
    return Compound(copy.deepcopy(dict(nbt_root(value))))


def clone_player_from_file(player_file: File) -> Compound:
    return clone_compound(player_file)


def _tag_type_name(value: object) -> str:
    """Return the concrete NBT tag type, including List element type."""
    value_type = type(value)
    if isinstance(value, list) and hasattr(value, "subtype"):
        subtype = value.subtype
        subtype_name = getattr(subtype, "__name__", str(subtype))
        return f"{value_type.__name__}[{subtype_name}]"
    return value_type.__name__


def deep_compare_nbt(left: object, right: object, path: str = "root") -> str | None:
    """Return the first structural NBT difference, or ``None`` when equal.

    The comparison deliberately checks concrete tag classes, List element types,
    Compound keys, scalar values, and array contents.  The returned path is
    intended for user-facing diagnostics, for example
    ``ForgeCaps/curios:inventory/Curios/0``.
    """
    if _tag_type_name(left) != _tag_type_name(right):
        return f"{path}: tag type {_tag_type_name(left)} != {_tag_type_name(right)}"

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        left_keys = list(left.keys())
        right_keys = list(right.keys())
        missing = [key for key in left_keys if key not in right]
        if missing:
            return f"{path}: missing key in right: {missing[0]}"
        extra = [key for key in right_keys if key not in left]
        if extra:
            return f"{path}: unexpected key in right: {extra[0]}"
        for key in left_keys:
            difference = deep_compare_nbt(left[key], right[key], f"{path}/{key}")
            if difference:
                return difference
        return None

    if isinstance(left, Array) and isinstance(right, Array):
        if list(left) != list(right):
            return f"{path}: array value {list(left)!r} != {list(right)!r}"
        return None

    if isinstance(left, Sequence) and not isinstance(left, (str, bytes, bytearray)):
        if len(left) != len(right):
            return f"{path}: length {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            difference = deep_compare_nbt(left_item, right_item, f"{path}/{index}")
            if difference:
                return difference
        return None

    if left != right:
        return f"{path}: value {left!r} != {right!r}"
    return None


def player_from_level(level_file: File) -> Compound:
    data = nbt_root(level_file).get("Data")
    if not isinstance(data, Compound):
        raise TypeError("level.dat does not contain a Data compound")
    player = data.get("Player")
    if not isinstance(player, Compound):
        raise TypeError("level.dat Data.Player is missing or is not a compound")
    return player


def set_level_player(level_file: File, player: Compound) -> File:
    result = clone_compound(level_file)
    data = result.get("Data")
    if not isinstance(data, Compound):
        raise TypeError("level.dat does not contain a Data compound")
    data["Player"] = clone_compound(player)
    return File({"": result})


def get_inventory(player: dict) -> list[Compound]:
    player = nbt_root(player)
    inventory = player.get("Inventory")
    if inventory is None:
        return []
    return list(inventory)


def item_id(item: dict) -> str:
    return str(item.get("id", ""))


def item_slot(item: dict) -> int | None:
    value = item.get("Slot")
    return None if value is None else int(value)


def item_count(item: dict) -> int:
    value = item.get("Count", Int(1))
    return int(value)


def has_curios(player: dict) -> bool:
    player = nbt_root(player)
    forge_caps = player.get("ForgeCaps")
    if not isinstance(forge_caps, dict):
        return False
    if "curios:inventory" in forge_caps:
        return True
    return any("curios" in str(key).lower() for key in forge_caps)


def get_player_uuid(player: dict) -> str | None:
    return uuid_tag_to_string(nbt_root(player))


def replace_player_uuid(player: Compound, target_uuid: str) -> Compound:
    result = clone_compound(player)
    set_player_uuid(result, target_uuid)
    return result


def compound_digest(value: Compound | File) -> str:
    stream = io.BytesIO()
    File({"": clone_compound(nbt_root(value))}).write(stream)
    return hashlib.sha256(stream.getvalue()).hexdigest()


def get_position(player: dict) -> tuple[float, float, float] | None:
    player = nbt_root(player)
    position = player.get("Pos")
    if position is None or len(position) < 3:
        return None
    return float(position[0]), float(position[1]), float(position[2])


def get_dimension(player: dict) -> str | None:
    player = nbt_root(player)
    value = player.get("Dimension")
    return None if value is None else str(value)


def get_ender_count(player: dict) -> int:
    player = nbt_root(player)
    return len(player.get("EnderItems", []))

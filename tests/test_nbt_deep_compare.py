from nbtlib import Compound, File
from nbtlib.tag import (
    Byte,
    ByteArray,
    Double,
    Float,
    Int,
    IntArray,
    List,
    Long,
    LongArray,
    Short,
    String,
)

from core.nbt_codec import deep_compare_nbt, load_nbt_bytes, nbt_root, serialize_nbt


def make_all_tag_types() -> Compound:
    return Compound(
        {
            "byte": Byte(1),
            "short": Short(2),
            "int": Int(3),
            "long": Long(4),
            "float": Float(5.5),
            "double": Double(6.5),
            "byte_array": ByteArray([1, -2]),
            "int_array": IntArray([7, -8]),
            "long_array": LongArray([9, -10]),
            "string": String("value"),
            "list": List[Int]([Int(11), Int(12)]),
            "empty_list": List[Compound]([]),
            "compound": Compound({"nested": String("nested-value")}),
        }
    )


def test_deep_compare_reports_first_nested_path() -> None:
    left = Compound({"ForgeCaps": Compound({"curios": Compound({"slot": Int(1)})})})
    right = Compound({"ForgeCaps": Compound({"curios": Compound({"slot": Int(2)})})})
    assert deep_compare_nbt(left, right) == "root/ForgeCaps/curios/slot: value Int(1) != Int(2)"


def test_all_nbt_tag_types_round_trip_structurally() -> None:
    source = make_all_tag_types()
    parsed = nbt_root(load_nbt_bytes(serialize_nbt(File(source))))
    assert deep_compare_nbt(source, parsed) is None


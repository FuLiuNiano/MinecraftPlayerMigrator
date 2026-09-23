from core.uuid_utils import (
    int_array_to_uuid,
    most_least_to_uuid,
    normalize_uuid,
    uuid_to_int_array,
    uuid_to_most_least,
)

UUID = "22222222-2222-4222-8222-222222222222"


def test_int_array_round_trip() -> None:
    values = list(uuid_to_int_array(UUID))
    assert len(values) == 4
    assert int_array_to_uuid(values) == UUID


def test_most_least_round_trip() -> None:
    most, least = uuid_to_most_least(UUID)
    assert most_least_to_uuid(most, least) == UUID


def test_compact_uuid_normalizes() -> None:
    assert normalize_uuid(UUID.replace("-", "").upper()) == UUID

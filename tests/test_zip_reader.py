from zipfile import ZipFile

import pytest

from core.player_scanner import load_source_names
from core.zip_reader import resolve_world_source
from tests.helpers import SOURCE_UUID, TEST_USERNAME


def test_zip_world_root_is_detected(tmp_path) -> None:
    archive_path = tmp_path / "world.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("world/level.dat", b"level")
        archive.writestr(
            f"world/playerdata/{SOURCE_UUID}.dat",
            b"player",
        )
    source = resolve_world_source(archive_path)
    assert source.root_prefix == "world/"
    assert source.list_player_files() == [f"{SOURCE_UUID}.dat"]
    assert source.read_bytes("level.dat") == b"level"


def test_zip_slip_is_rejected(tmp_path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("world/level.dat", b"level")
        archive.writestr("world/playerdata/source.dat", b"player")
        archive.writestr("../outside.txt", b"blocked")
    with pytest.raises(ValueError, match="Unsafe ZIP entry"):
        resolve_world_source(archive_path)


def test_ftbteams_name_is_associated_with_uuid(tmp_path) -> None:
    archive_path = tmp_path / "named-world.zip"
    uuid = SOURCE_UUID
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("world/level.dat", b"level")
        archive.writestr(f"world/playerdata/{uuid}.dat", b"player")
        archive.writestr(
            f"world/ftbteams/player/{uuid}.snbt",
            f'{{id: "{uuid}", type: "player", player_name: "{TEST_USERNAME}"}}',
        )
    source = resolve_world_source(archive_path)
    assert load_source_names(source)[uuid] == TEST_USERNAME

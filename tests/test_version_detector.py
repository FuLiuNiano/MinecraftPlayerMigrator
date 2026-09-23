from __future__ import annotations

from pathlib import Path

from nbtlib import Compound, File
from nbtlib.tag import Int, String

from core.nbt_codec import serialize_nbt
from core.version_detector import (
    MinecraftVersionInfo,
    VersionStatus,
    compare_minecraft_versions,
    detect_world_version,
)


def _write_level(path: Path, *, name: str | None, data_version: int | None) -> None:
    data = {}
    if name is not None:
        data["Version"] = Compound({"Name": String(name), "Id": Int(data_version or 0)})
    if data_version is not None:
        data["DataVersion"] = Int(data_version)
    path.mkdir(parents=True, exist_ok=True)
    (path / "level.dat").write_bytes(serialize_nbt(File({"Data": Compound(data)})))


def test_version_name_same_is_match(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_level(source, name="1.20.1", data_version=3465)
    _write_level(target, name="1.20.1", data_version=3465)

    result = compare_minecraft_versions(detect_world_version(source), detect_world_version(target))

    assert result.status is VersionStatus.MATCH
    assert result.source.display_version == "1.20.1"
    assert result.target.data_version == 3465


def test_version_name_different_is_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_level(source, name="1.20.1", data_version=3465)
    _write_level(target, name="1.21.1", data_version=3955)

    assert compare_minecraft_versions(source, target).status is VersionStatus.MISMATCH


def test_dataversion_fallback_same_and_different(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_level(source, name=None, data_version=3465)
    _write_level(target, name=None, data_version=3465)
    assert compare_minecraft_versions(source, target).status is VersionStatus.MATCH

    _write_level(target, name=None, data_version=3955)
    assert compare_minecraft_versions(source, target).status is VersionStatus.MISMATCH


def test_same_name_with_conflicting_dataversion_is_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_level(source, name="1.20.1", data_version=3465)
    _write_level(target, name="1.20.1", data_version=3955)

    assert compare_minecraft_versions(source, target).status is VersionStatus.CONFLICT


def test_missing_version_is_unknown(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_level(source, name=None, data_version=None)
    _write_level(target, name="1.20.1", data_version=3465)

    result = compare_minecraft_versions(source, target)

    assert result.status is VersionStatus.UNKNOWN
    assert result.source.confidence == "low"


def test_compare_version_info_does_not_guess() -> None:
    result = compare_minecraft_versions(
        MinecraftVersionInfo(display_version="1.20.1"),
        MinecraftVersionInfo(data_version=3465),
    )

    assert result.status is VersionStatus.UNKNOWN


def test_missing_level_dat_is_unknown_without_guessing(tmp_path: Path) -> None:
    result = detect_world_version(tmp_path / "missing-world")

    assert result.display_version is None
    assert result.data_version is None
    assert result.confidence == "low"
    assert result.warnings

from __future__ import annotations

from pathlib import Path

from core.compatibility_scanner import scan_compatibility
from core.full_models import ModuleStatus
from core.models import IdentityEvidence
from core.nbt_codec import load_nbt_file, nbt_root, serialize_nbt
from core.version_detector import VersionStatus
from tests.helpers import SOURCE_UUID, TARGET_UUID, TEST_USERNAME, write_world
from tests.test_progress_migration import _write_progress_source

OTHER_UUID = "11111111-1111-1111-1111-111111111111"


def _evidence(uuid: str = TARGET_UUID) -> tuple[IdentityEvidence, ...]:
    return (
        IdentityEvidence(
            uuid=uuid,
            level_dat_match=True,
            recent_playerdata_match=True,
            launcher_arg_match=True,
            usercache_match=True,
            username=TEST_USERNAME,
        ),
    )


def _worlds(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    target = tmp_path / "target"
    write_world(source, SOURCE_UUID)
    write_world(target, TARGET_UUID, target=True)
    _write_progress_source(source)
    return source, target


def test_scanner_reports_all_verified_modules(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.can_migrate is True
    assert report.source_username == TEST_USERNAME
    assert report.target_username == TEST_USERNAME
    assert report.version_status is VersionStatus.MATCH
    assert report.source_version is not None
    assert report.source_version.display_version == "1.20.1"
    assert report.target_version is not None
    assert report.target_version.data_version == 3465
    assert report.modules["player"].status is ModuleStatus.READY
    assert report.modules["player"].counts["inventory"] == 1
    assert report.modules["advancements"].counts == {"total": 3, "completed": 2, "unfinished": 1}
    assert report.modules["stats"].counts["categories"] == 2
    assert report.modules["stats"].counts["records"] == 2
    assert report.modules["ftb_quests"].counts == {
        "task_progress": 2,
        "started": 1,
        "completed": 1,
        "claimed_rewards": 1,
    }
    assert report.modules["ftb_quests"].target_paths == (
        str(target / "ftbquests" / f"{TARGET_UUID}.snbt"),
    )
    assert report.modules["ftb_team"].counts["members"] == 1
    assert report.modules["ftb_team"].counts["rank"] == "owner"
    assert report.modules["ftb_team"].target_paths == (
        str(target / "ftbteams" / "player" / f"{TARGET_UUID}.snbt"),
    )
    assert report.modules["waystones"].counts["owned"] == 5
    assert report.modules["waystones"].counts["total"] == 6
    assert report.modules["endinglib"].counts["total"] == 2
    assert report.modules["cosarmor"].counts["parseable"] is True


def test_scanner_marks_missing_modules_without_blocking_player(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    write_world(source, SOURCE_UUID)
    write_world(target, TARGET_UUID, target=True)

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.can_migrate is True
    for name in ("advancements", "stats", "ftb_quests", "ftb_team", "waystones", "endinglib", "cosarmor"):
        assert report.modules[name].status is ModuleStatus.SKIPPED_NOT_FOUND


def test_scanner_detects_existing_target_advancement(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    target_path = target / "advancements" / f"{TARGET_UUID}.json"
    target_path.parent.mkdir(parents=True)
    target_path.write_text("{}", encoding="utf-8")

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.modules["advancements"].will_overwrite is True


def test_scanner_rejects_unverified_quest_structure(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    quest_path = source / "ftbquests" / f"{SOURCE_UUID}.snbt"
    source_compact = SOURCE_UUID.replace("-", "")
    quest_path.write_text(
        f'{{\n\tuuid: "{source_compact}"\n'
        f'\tname: "{TEST_USERNAME}#{source_compact[:8]}"\n\tteam_progress: {{ TASK_A: 1 }}\n}}\n',
        encoding="utf-8",
    )

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.modules["ftb_quests"].status is ModuleStatus.SKIPPED_UNSUPPORTED
    assert "ftb_quests" in report.unsupported
    assert report.can_migrate is True


def test_scanner_rejects_party_team(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    team_path = source / "ftbteams" / "player" / f"{SOURCE_UUID}.snbt"
    team_path.write_text(
        f'''{{
\tid: "{SOURCE_UUID}"
\ttype: "party"
\tplayer_name: "{TEST_USERNAME}"
\tranks: {{
\t\t{SOURCE_UUID}: "owner"
\t\t{OTHER_UUID}: "member"
\t}}
}}
''',
        encoding="utf-8",
    )

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.modules["ftb_team"].status is ModuleStatus.SKIPPED_UNSUPPORTED
    assert "多人 FTB Team" in " ".join(report.modules["ftb_team"].warnings)


def test_scanner_blocks_conflicting_identity_evidence(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    evidence = _evidence() + (
        IdentityEvidence(
            uuid=OTHER_UUID,
            level_dat_match=True,
            recent_playerdata_match=True,
            launcher_arg_match=True,
            usercache_match=True,
        ),
    )

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, evidence)

    assert report.can_migrate is False
    assert any("冲突" in item for item in report.blocking_issues)


def test_scanner_allows_same_source_and_target_uuid(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)

    report = scan_compatibility(source, SOURCE_UUID, target, SOURCE_UUID, _evidence(SOURCE_UUID))

    assert not any("UUID" in item and "冲突" in item for item in report.blocking_issues)


def test_scanner_allows_unowned_waystones_without_owner_uid(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    waystones = source / "data" / "waystones.dat"
    from core.nbt_codec import load_nbt_file, nbt_root, serialize_nbt

    root = nbt_root(load_nbt_file(waystones))
    root["data"]["Waystones"][0].pop("OwnerUid")
    waystones.write_bytes(serialize_nbt(root))

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.modules["waystones"].status is ModuleStatus.READY
    assert report.modules["waystones"].counts["owned"] == 4


def _set_version(world: Path, *, name: str | None, data_version: int | None) -> None:
    document = load_nbt_file(world / "level.dat")
    data = nbt_root(document)["Data"]
    data.pop("Version", None)
    data.pop("DataVersion", None)
    if name is not None:
        from nbtlib import Compound
        from nbtlib.tag import Int, String

        data["Version"] = Compound({"Name": String(name), "Id": Int(data_version or 0)})
    if data_version is not None:
        from nbtlib.tag import Int

        data["DataVersion"] = Int(data_version)
    world.joinpath("level.dat").write_bytes(serialize_nbt(document))


def test_scanner_blocks_minecraft_version_mismatch(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    _set_version(target, name="1.21.1", data_version=3955)

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.version_status is VersionStatus.MISMATCH
    assert report.can_migrate is False
    assert any("MISMATCH" in item for item in report.blocking_issues)


def test_scanner_blocks_unknown_minecraft_version(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    _set_version(source, name=None, data_version=None)

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.version_status is VersionStatus.UNKNOWN
    assert report.can_migrate is False
    assert any("UNKNOWN" in item for item in report.blocking_issues)


def test_scanner_blocks_conflicting_minecraft_version_metadata(tmp_path: Path) -> None:
    source, target = _worlds(tmp_path)
    _set_version(target, name="1.20.1", data_version=3955)

    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())

    assert report.version_status is VersionStatus.CONFLICT
    assert report.can_migrate is False
    assert any("CONFLICT" in item for item in report.blocking_issues)

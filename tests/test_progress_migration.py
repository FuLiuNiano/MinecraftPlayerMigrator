from __future__ import annotations

import json
from pathlib import Path

import pytest
from nbtlib import Compound, File
from nbtlib.tag import Int, IntArray, List, String

import core.progress_migration as progress_module
from core.nbt_codec import load_nbt_file, nbt_root, serialize_nbt
from core.progress_migration import execute_progress_migration
from core.snbt_codec import direct_compound_entries, parse_snbt
from core.uuid_utils import uuid_to_int_array
from core.zip_reader import resolve_world_source
from tests.helpers import SOURCE_UUID, TARGET_UUID, TEST_USERNAME, write_world

OTHER_UUID = "33333333-3333-4333-8333-333333333333"


def _write_progress_source(world: Path) -> None:
    source_compact = SOURCE_UUID.replace("-", "")
    (world / "advancements").mkdir(parents=True, exist_ok=True)
    (world / "stats").mkdir(parents=True, exist_ok=True)
    (world / "ftbquests").mkdir(parents=True, exist_ok=True)
    (world / "ftbteams" / "player").mkdir(parents=True, exist_ok=True)
    (world / "data").mkdir(parents=True, exist_ok=True)

    (world / "advancements" / f"{SOURCE_UUID}.json").write_text(
        json.dumps(
            {
                "minecraft:root": {"done": True},
                "mod:second": {"done": True},
                "mod:third": {"done": False},
                "DataVersion": 3465,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (world / "stats" / f"{SOURCE_UUID}.json").write_text(
        json.dumps(
            {
                "DataVersion": 3465,
                "stats": {
                    "minecraft:custom": {"minecraft:play_time": 858456},
                    "minecraft:used": {"slashblade:slashblade": 17061},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (world / "ftbquests" / f"{SOURCE_UUID}.snbt").write_text(
        """{
\tversion: 1
\tuuid: \"SOURCE_COMPACT\"
\tname: \"TEST_USERNAME#SOURCE_SHORT\"
\tlock: false
\trewards_blocked: false
\ttask_progress: {
\t\tTASK_A: 3
\t\tTASK_B: 1
\t}
\tstarted: {
\t\tTASK_A: 100L
\t}
\tcompleted: {
\t\tTASK_A: 101L
\t}
\tclaimed_rewards: {
\t\t\"SOURCE_COMPACT:REWARD_A\": 102L
\t}
\tplayer_data: { }
}
        """.replace("SOURCE_COMPACT", source_compact)
        .replace("SOURCE_SHORT", source_compact[:8])
        .replace("TEST_USERNAME", TEST_USERNAME),
        encoding="utf-8",
    )
    (world / "ftbteams" / "player" / f"{SOURCE_UUID}.snbt").write_text(
        f'''{{
\tid: "{SOURCE_UUID}"
\ttype: "player"
\tplayer_name: "{TEST_USERNAME}"
\tranks: {{
\t\t{SOURCE_UUID}: "owner"
\t}}
\tproperties: {{ "ftbteams:color": "#59F9FF" }}
\tmessage_history: [ ]
\textra: {{ }}
}}
''',
        encoding="utf-8",
    )
    (world / "ftbteams" / "ftbteams.snbt").write_text(
        '{\n\tid: "99999999-9999-4999-8999-999999999999"\n\textra: { }\n}\n',
        encoding="utf-8",
    )

    waystones = []
    source_owner = uuid_to_int_array(SOURCE_UUID)
    other_owner = IntArray(uuid_to_int_array(OTHER_UUID))
    for index in range(5):
        waystones.append(
            Compound(
                {
                    "Name": String(f"Source{index}"),
                    "World": String("minecraft:overworld"),
                    "OwnerUid": IntArray(source_owner),
                    "WaystoneUid": IntArray([index, 2, 3, 4]),
                    "BlockPos": Compound({"X": Int(index), "Y": Int(70), "Z": Int(index + 10)}),
                }
            )
        )
    waystones.append(
        Compound(
            {
                "Name": String("Other"),
                "World": String("minecraft:overworld"),
                "OwnerUid": other_owner,
                "WaystoneUid": IntArray([9, 8, 7, 6]),
                "BlockPos": Compound({"X": Int(9), "Y": Int(70), "Z": Int(9)}),
            }
        )
    )
    (world / "data" / "waystones.dat").write_bytes(
        serialize_nbt(
            File(
                {
                    "data": Compound(
                        {"Waystones": List[Compound](waystones), "Unknown": String("keep")}
                    )
                }
            )
        )
    )
    ending_entries = List[Compound](
        [
            Compound({"id": IntArray(source_owner), "overlays": List[String]([])}),
            Compound({"id": other_owner, "overlays": List[String]([])}),
        ]
    )
    (world / "data" / "endinglib_saved_data.dat").write_bytes(
        serialize_nbt(
            File(
                {
                    "data": Compound(
                        {
                            "PlayersDisabledOverlays": ending_entries,
                            "PlayersDisabledInputPermissions": List[Compound](
                                [
                                    Compound({"id": IntArray(source_owner)}),
                                    Compound({"id": other_owner}),
                                ]
                            ),
                        }
                    )
                }
            )
        )
    )
    (world / "playerdata" / f"{SOURCE_UUID}.cosarmor").write_bytes(
        serialize_nbt(File({"Size": Int(11), "Hidden": String(""), "Items": List[Compound]([])}))
    )


def _make_worlds(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source_world"
    target = tmp_path / "Migrator_FullProgressTest"
    write_world(source, SOURCE_UUID)
    write_world(target, TARGET_UUID, target=True)
    _write_progress_source(source)
    (target / "ftbteams").mkdir(exist_ok=True)
    (target / "ftbteams" / "ftbteams.snbt").write_text(
        '{\n\tid: "99999999-9999-4999-8999-999999999999"\n}\n', encoding="utf-8"
    )
    (target / "advancements").mkdir(exist_ok=True)
    (target / "advancements" / f"{TARGET_UUID}.json").write_text("{\"old\": true}", encoding="utf-8")
    return source, target


def test_snbt_targeted_identity_rewrite_preserves_unknown_fields() -> None:
    source_compact = SOURCE_UUID.replace("-", "")
    target_compact = TARGET_UUID.replace("-", "")
    document = parse_snbt(
        """{
\tuuid: \"SOURCE_COMPACT\"
\tname: \"TEST_USERNAME#SOURCE_SHORT\"
\tclaimed_rewards: {
\t\t\"SOURCE_COMPACT:R1\": 1L
\t\t\"QUEST_SOURCE_SHORT\": 2L
\t}
}
""".replace("SOURCE_COMPACT", source_compact)
        .replace("SOURCE_SHORT", source_compact[:8])
        .replace("TEST_USERNAME", TEST_USERNAME)
    )
    document.replace_string(
        document.root_field("uuid"),
        source_compact,
        target_compact,
    )
    document.replace_string(
        document.root_field("name"),
        f"{TEST_USERNAME}#{source_compact[:8]}",
        f"{TEST_USERNAME}#{target_compact[:8]}"
    )
    reward = direct_compound_entries(document, "claimed_rewards")[0]
    document.replace_key(
        reward,
        reward.key,
        f"{target_compact}:R1",
    )
    rendered = document.render()
    assert f'uuid: "{target_compact}"' in rendered
    assert f'"{target_compact}:R1"' in rendered
    assert f"QUEST_{source_compact[:8]}" in rendered
    parse_snbt(rendered)


def test_full_progress_migration_updates_selected_data_only(tmp_path: Path) -> None:
    source, target = _make_worlds(tmp_path)
    old_advancements = (target / "advancements" / f"{TARGET_UUID}.json").read_bytes()
    global_team_before = (target / "ftbteams" / "ftbteams.snbt").read_bytes()

    result = execute_progress_migration(
        resolve_world_source(source), target, SOURCE_UUID, TARGET_UUID
    )

    assert result.backup_path.is_dir()
    assert (result.backup_path / "advancements" / f"{TARGET_UUID}.json").read_bytes() == old_advancements
    assert result.advancements_total == 3
    assert result.advancements_completed == 2
    assert result.advancements_incomplete == 1
    assert result.stats_categories == 2
    assert result.stats_records == 2
    assert (result.ftb_quest_task_progress, result.ftb_quest_started) == (2, 1)
    assert (result.ftb_quest_completed, result.ftb_quest_claimed_rewards) == (1, 1)
    assert result.ftb_team_owner == TEST_USERNAME
    assert result.ftb_team_rank == "owner"
    assert len(result.waystones) == 5
    assert result.endinglib_records == 2
    assert result.source_identity_residuals == ()

    advancements = json.loads(
        (target / "advancements" / f"{TARGET_UUID}.json").read_text(encoding="utf-8")
    )
    advancement_entries = [item for item in advancements.values() if isinstance(item, dict)]
    assert len(advancement_entries) == 3
    assert sum(item["done"] is True for item in advancement_entries) == 2
    stats = json.loads((target / "stats" / f"{TARGET_UUID}.json").read_text(encoding="utf-8"))
    assert stats["DataVersion"] == 3465
    assert stats["stats"]["minecraft:custom"]["minecraft:play_time"] == 858456

    quest = parse_snbt((target / "ftbquests" / f"{TARGET_UUID}.snbt").read_text())
    assert quest.string_value(quest.root_field("uuid")) == TARGET_UUID.replace("-", "")
    assert quest.string_value(quest.root_field("name")) == f"{TEST_USERNAME}#{TARGET_UUID.replace('-', '')[:8]}"
    assert direct_compound_entries(quest, "claimed_rewards")[0].key.startswith(
        TARGET_UUID.replace("-", "") + ":"
    )

    team = parse_snbt(
        (target / "ftbteams" / "player" / f"{TARGET_UUID}.snbt").read_text()
    )
    assert team.string_value(team.root_field("id")) == TARGET_UUID
    assert direct_compound_entries(team, "ranks")[0].key == TARGET_UUID
    assert (target / "ftbteams" / "ftbteams.snbt").read_bytes() == global_team_before

    waystones = nbt_root(load_nbt_file(target / "data" / "waystones.dat"))
    owners = [tuple(int(item) for item in entry["OwnerUid"]) for entry in waystones["data"]["Waystones"]]
    assert owners.count(tuple(uuid_to_int_array(TARGET_UUID))) == 5
    assert owners.count(tuple(uuid_to_int_array(OTHER_UUID))) == 1

    ending = nbt_root(load_nbt_file(target / "data" / "endinglib_saved_data.dat"))
    for section in ("PlayersDisabledOverlays", "PlayersDisabledInputPermissions"):
        ids = [tuple(int(item) for item in entry["id"]) for entry in ending["data"][section]]
        assert ids.count(tuple(uuid_to_int_array(TARGET_UUID))) == 1
        assert ids.count(tuple(uuid_to_int_array(SOURCE_UUID))) == 0

    assert (target / "playerdata" / f"{TARGET_UUID}.cosarmor").is_file()
    assert (source / "ftbquests" / f"{SOURCE_UUID}.snbt").is_file()


def test_progress_transaction_rolls_back_on_late_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = _make_worlds(tmp_path)
    old_advancements = (target / "advancements" / f"{TARGET_UUID}.json").read_bytes()
    real_atomic_write = progress_module.atomic_write
    failed = False

    def fail_on_quest(path: Path, payload: bytes) -> None:
        nonlocal failed
        if path.name == f"{TARGET_UUID}.snbt" and path.parent.name == "ftbquests" and not failed:
            failed = True
            raise OSError("simulated Quest write failure")
        real_atomic_write(path, payload)

    monkeypatch.setattr(progress_module, "atomic_write", fail_on_quest)
    with pytest.raises(OSError, match="simulated Quest write failure"):
        execute_progress_migration(resolve_world_source(source), target, SOURCE_UUID, TARGET_UUID)

    assert failed is True
    assert (target / "advancements" / f"{TARGET_UUID}.json").read_bytes() == old_advancements
    assert not (target / "ftbquests" / f"{TARGET_UUID}.snbt").exists()
    assert not (target / "ftbteams" / "player" / f"{TARGET_UUID}.snbt").exists()
    assert list(target.rglob("*.tmp")) == []
    assert list(tmp_path.glob("world_backup_before_full_progress_migration_*"))

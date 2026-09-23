from pathlib import Path

from core.identity_detector import detect_local_identities
from core.world_detector import discover_worlds
from tests.helpers import TARGET_UUID, write_world


def test_pcl2_comma_uuid_launch_argument_is_detected(tmp_path: Path) -> None:
    world = tmp_path / "中文 路径" / "测试 world"
    write_world(world, TARGET_UUID, target=True)
    instance = tmp_path / "实例 with spaces"
    logs = instance / "logs"
    logs.mkdir(parents=True)
    (logs / "latest.log").write_text(
        f"[Client thread/INFO]: --uuid, {TARGET_UUID}\n",
        encoding="utf-8",
    )

    evidences = detect_local_identities(world, instance)

    assert evidences[0].uuid == TARGET_UUID
    assert evidences[0].launcher_arg_match is True
    assert "启动参数" in evidences[0].evidence_labels


def test_discover_worlds_supports_saves_and_chinese_space_paths(tmp_path: Path) -> None:
    saves = tmp_path / "Minecraft 实例" / "saves"
    world = saves / "Migrator 测试 world"
    write_world(world, TARGET_UUID, target=True)

    assert discover_worlds(saves) == [world.resolve()]
    assert discover_worlds(saves.parent) == [world.resolve()]

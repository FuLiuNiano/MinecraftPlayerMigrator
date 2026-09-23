from __future__ import annotations

import argparse
import os
from pathlib import Path

APP_VERSION = "1.1.0"


def _run_self_test() -> None:
    """Run a small local-only release health check without touching a world."""
    from tempfile import TemporaryDirectory
    from zipfile import ZIP_DEFLATED, ZipFile

    from nbtlib import Compound, File
    from nbtlib.tag import Byte, Int, List, String

    from core.nbt_codec import (
        deep_compare_nbt,
        get_player_uuid,
        load_nbt_bytes,
        load_nbt_file,
        nbt_root,
        player_from_level,
        serialize_nbt,
    )
    from core.uuid_utils import (
        int_array_to_uuid,
        most_least_to_uuid,
        uuid_to_int_array,
        uuid_to_most_least,
    )
    from core.zip_reader import resolve_world_source

    target_uuid = "22222222-2222-4222-8222-222222222222"
    expected_int_array = list(uuid_to_int_array(target_uuid))
    if list(uuid_to_int_array(target_uuid)) != expected_int_array:
        raise RuntimeError("UUID IntArray 自检失败")
    if int_array_to_uuid(expected_int_array) != target_uuid:
        raise RuntimeError("UUID IntArray 反向自检失败")
    most, least = uuid_to_most_least(target_uuid)
    if most_least_to_uuid(most, least) != target_uuid:
        raise RuntimeError("UUID Most/Least 自检失败")

    player = Compound(
        {
            "UUID": uuid_to_int_array(target_uuid),
            "Inventory": List[Compound](
                [Compound({"id": String("minecraft:bread"), "Count": Byte(1), "Slot": Byte(0)})]
            ),
            "XpLevel": Int(7),
            "ForgeCaps": Compound({"example:capability": Compound({"value": Int(1)})}),
        }
    )
    level_root = Compound({"Data": Compound({"Player": player})})

    with TemporaryDirectory(prefix="minecraft_player_migrator_self_test_") as temp_dir:
        temp_root = Path(temp_dir)
        world = temp_root / "world"
        playerdata = world / "playerdata"
        playerdata.mkdir(parents=True)
        level_path = world / "level.dat"
        player_path = playerdata / f"{target_uuid}.dat"
        level_path.write_bytes(serialize_nbt(File({"": level_root})))
        player_path.write_bytes(serialize_nbt(File({"": player})))

        parsed_player = load_nbt_file(player_path)
        parsed_level = load_nbt_file(level_path)
        parsed_player_root = nbt_root(parsed_player)
        level_player = player_from_level(parsed_level)
        if get_player_uuid(parsed_player) != target_uuid:
            raise RuntimeError("NBT UUID 自检失败")
        if not parsed_player_root.get("Inventory"):
            raise RuntimeError("NBT Inventory 自检失败")
        if deep_compare_nbt(parsed_player_root, level_player):
            raise RuntimeError("playerdata 与 level.dat Player 自检失败")
        round_trip = load_nbt_bytes(serialize_nbt(parsed_player))
        if deep_compare_nbt(parsed_player_root, nbt_root(round_trip)):
            raise RuntimeError("NBT gzip round-trip 自检失败")
        if nbt_root(round_trip)["ForgeCaps"]["example:capability"]["value"] != 1:
            raise RuntimeError("ForgeCaps 自检失败")

        zip_path = temp_root / "服务器世界备份.zip"
        with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
            archive.write(level_path, "world/level.dat")
            archive.write(player_path, f"world/playerdata/{target_uuid}.dat")
        source = resolve_world_source(zip_path)
        if source.root_prefix != "world/" or f"{target_uuid}.dat" not in source.list_player_files():
            raise RuntimeError("ZIP world 根目录自检失败")

        permission_probe = temp_root / "write-probe.tmp"
        permission_probe.write_text("ok", encoding="utf-8")
        if permission_probe.read_text(encoding="utf-8") != "ok":
            raise RuntimeError("临时目录/文件权限自检失败")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minecraft Player Migrator")
    parser.add_argument("--version", action="version", version=f"Minecraft Player Migrator {APP_VERSION}")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="快速检查发布版 GUI 是否可以启动并构造向导",
    )
    parser.add_argument("source", type=Path, nargs="?", help="server world directory or ZIP")
    parser.add_argument(
        "--instance", type=Path, help="Minecraft instance directory for username cache"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        # The packaged executable uses this path for a non-interactive smoke test.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        try:
            _run_self_test()
            window = MainWindow()
            app.processEvents()
            window.close()
            app.quit()
            app.processEvents()
            return 0
        except Exception as exc:  # noqa: BLE001 - self-test must surface every failure clearly.
            from PySide6.QtWidgets import QMessageBox

            from ui.errors import LOGGER, friendly_error

            LOGGER.exception("发布版自检失败")
            QMessageBox.critical(None, "Minecraft Player Migrator 自检失败", friendly_error(exc))
            return 1
    if args.source is None:
        from ui.main_window import run_gui

        return run_gui()
    from core.player_scanner import load_name_cache, load_source_names, scan_players
    from core.zip_reader import resolve_world_source

    source = resolve_world_source(args.source)
    names = load_source_names(source)
    names.update(load_name_cache(args.instance))
    players = scan_players(source, names)
    for player in players:
        print(
            f"{player.username or '-'}\t{player.uuid}\t"
            f"Lv{player.xp_level if player.xp_level is not None else '-'}\t"
            f"Inventory={player.inventory_count}\tPos={player.position}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

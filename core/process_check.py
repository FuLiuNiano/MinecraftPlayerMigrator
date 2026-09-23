from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass(slots=True)
class ProcessInfo:
    name: str
    pid: int
    command_line: str


def running_game_processes() -> list[ProcessInfo]:
    result: list[ProcessInfo] = []
    launcher_names = {"java", "javaw", "java.exe", "javaw.exe", "minecraft", "minecraft.exe"}
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            name = str(process.info.get("name") or "")
            command_line = " ".join(process.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        lowered = command_line.lower()
        if name.lower() in launcher_names and (
            "net.minecraft" in lowered
            or "--gamedir" in lowered
            or "minecraft" in lowered
            or name.lower() in {"minecraft", "minecraft.exe"}
        ):
            result.append(ProcessInfo(name, process.pid, command_line))
    return result


def assert_game_closed() -> None:
    processes = running_game_processes()
    if processes:
        details = ", ".join(f"{item.name} [{item.pid}]" for item in processes)
        raise RuntimeError(f"Minecraft game process is still running: {details}")

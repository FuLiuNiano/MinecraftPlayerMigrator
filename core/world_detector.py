from __future__ import annotations

from pathlib import Path

from .zip_reader import WorldSource


def discover_worlds(path: str | Path) -> list[Path]:
    """Find selectable world folders from a world, saves, or instance path."""
    candidate = Path(path).expanduser().resolve()
    if (candidate / "level.dat").is_file() and (candidate / "playerdata").is_dir():
        return [candidate]

    search_roots = [candidate]
    if candidate.name.lower() == "saves":
        search_roots = [candidate]
    elif (candidate / "saves").is_dir():
        search_roots = [candidate / "saves"]
    elif (candidate / "versions").is_dir():
        search_roots = [
            version / "saves"
            for version in candidate.joinpath("versions").iterdir()
            if version.is_dir() and (version / "saves").is_dir()
        ]

    worlds: list[Path] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and (child / "level.dat").is_file() and (child / "playerdata").is_dir():
                worlds.append(child.resolve())
    return sorted(set(worlds), key=lambda item: str(item).lower())


def validate_world(source: WorldSource) -> None:
    if not source.exists("level.dat"):
        raise ValueError(f"level.dat is missing: {source.label}")
    if not source.list_player_files():
        raise ValueError(f"No playerdata/*.dat files found: {source.label}")


def find_instance_roots(start: Path) -> list[Path]:
    """Return shallow, user-visible Minecraft instance candidates."""
    candidates: list[Path] = []
    for root in (start, start / ".minecraft", start / "PCL2", start / "HMCL"):
        if (root / "versions").is_dir() or (root / "saves").is_dir():
            candidates.append(root)
    return list(dict.fromkeys(candidates))

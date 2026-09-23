from __future__ import annotations

import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PLAYER_FILE_RE = re.compile(
    r"^(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.dat$"
)


def _normalise_entry(name: str) -> str:
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or name.startswith("/") or ":" in path.parts[0] or ".." in path.parts:
        raise ValueError(f"Unsafe ZIP entry: {name}")
    return "/".join(part for part in path.parts if part != ".")


def _assert_safe_zip_info(info: zipfile.ZipInfo) -> None:
    _normalise_entry(info.filename)
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        raise ValueError(f"Symbolic links are not allowed in ZIP sources: {info.filename}")


def _find_zip_root(names: list[str]) -> str:
    normalised = {_normalise_entry(name).rstrip("/") for name in names}
    candidates: list[str] = []
    for name in normalised:
        if not name.endswith("/level.dat"):
            continue
        prefix = name[: -len("level.dat")]
        has_playerdata = any(
            entry == f"{prefix}playerdata" or entry.startswith(f"{prefix}playerdata/")
            for entry in normalised
        )
        if has_playerdata:
            candidates.append(prefix)
    if "level.dat" in normalised and any(
        entry == "playerdata" or entry.startswith("playerdata/") for entry in normalised
    ):
        candidates.append("")
    if not candidates:
        raise ValueError("Could not find a world root containing level.dat and playerdata")
    if "world/" in candidates:
        return "world/"
    return min(set(candidates), key=lambda item: (item != "", len(item)))


@dataclass(slots=True)
class WorldSource:
    location: Path
    root_prefix: str = ""

    @property
    def is_zip(self) -> bool:
        return self.location.is_file()

    @property
    def label(self) -> str:
        if self.is_zip:
            return f"{self.location}!/{self.root_prefix}"
        return str(self.location)

    def _entry_name(self, relative: str) -> str:
        return f"{self.root_prefix}{relative.replace(chr(92), '/')}"

    def exists(self, relative: str) -> bool:
        if not self.is_zip:
            return (self.location / relative).is_file()
        with zipfile.ZipFile(self.location) as archive:
            try:
                archive.getinfo(self._entry_name(relative))
            except KeyError:
                return False
            return True

    def read_bytes(self, relative: str) -> bytes:
        if not self.is_zip:
            return (self.location / relative).read_bytes()
        with zipfile.ZipFile(self.location) as archive:
            try:
                return archive.read(self._entry_name(relative))
            except KeyError as exc:
                raise FileNotFoundError(
                    f"ZIP entry not found: {self._entry_name(relative)}"
                ) from exc

    def read_text(self, relative: str) -> str:
        return self.read_bytes(relative).decode("utf-8", errors="replace")

    def list_files(self, relative_dir: str, suffix: str | None = None) -> list[str]:
        relative_dir = relative_dir.replace("\\", "/").rstrip("/") + "/"
        if not self.is_zip:
            directory = self.location / relative_dir
            if not directory.is_dir():
                return []
            return sorted(
                path.name
                for path in directory.iterdir()
                if path.is_file() and (suffix is None or path.name.endswith(suffix))
            )

        prefix = self.root_prefix + relative_dir
        result: list[str] = []
        with zipfile.ZipFile(self.location) as archive:
            for raw_name in archive.namelist():
                name = _normalise_entry(raw_name)
                if not name.startswith(prefix):
                    continue
                filename = name[len(prefix) :]
                if "/" not in filename and (suffix is None or filename.endswith(suffix)):
                    result.append(filename)
        return sorted(set(result))

    def list_player_files(self) -> list[str]:
        if not self.is_zip:
            playerdata = self.location / "playerdata"
            if not playerdata.is_dir():
                return []
            return sorted(
                path.name
                for path in playerdata.iterdir()
                if path.is_file() and PLAYER_FILE_RE.fullmatch(path.name)
            )

        prefix = self.root_prefix + "playerdata/"
        result: list[str] = []
        with zipfile.ZipFile(self.location) as archive:
            for raw_name in archive.namelist():
                name = _normalise_entry(raw_name)
                if not name.startswith(prefix):
                    continue
                filename = name[len(prefix) :]
                if "/" not in filename and PLAYER_FILE_RE.fullmatch(filename):
                    result.append(filename)
        return sorted(set(result))


def resolve_world_source(path: str | Path) -> WorldSource:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file() and candidate.suffix.lower() == ".zip":
        with zipfile.ZipFile(candidate) as archive:
            infos = archive.infolist()
            for info in infos:
                _assert_safe_zip_info(info)
            root_prefix = _find_zip_root([info.filename for info in infos])
        return WorldSource(candidate, root_prefix)

    if (
        candidate.is_dir()
        and (candidate / "level.dat").is_file()
        and (candidate / "playerdata").is_dir()
    ):
        return WorldSource(candidate)

    if candidate.is_dir():
        worlds = [
            child
            for child in candidate.iterdir()
            if child.is_dir()
            and (child / "level.dat").is_file()
            and (child / "playerdata").is_dir()
        ]
        if len(worlds) == 1:
            return WorldSource(worlds[0])
        if worlds:
            raise ValueError(
                "The selected directory contains multiple worlds; choose one explicitly"
            )

    raise FileNotFoundError(f"Not a Minecraft world folder or ZIP: {candidate}")

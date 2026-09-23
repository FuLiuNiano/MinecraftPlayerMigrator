from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .models import IdentityEvidence
from .nbt_codec import get_player_uuid, load_nbt_file, player_from_level

LAUNCH_UUID_RE = re.compile(
    r"--uuid(?:=|[\s,]+)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})",
    re.IGNORECASE,
)
LOGGER = logging.getLogger(__name__)


def _normalise_match(value: str) -> str:
    value = value.replace("-", "")
    return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}".lower()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def read_identity_cache(instance_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    data = _read_json(instance_dir / "usercache.json")
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("uuid") and row.get("name"):
                result[_normalise_match(str(row["uuid"]))] = str(row["name"])
    data = _read_json(instance_dir / "usernamecache.json")
    if isinstance(data, dict):
        for uuid, name in data.items():
            result.setdefault(_normalise_match(str(uuid)), str(name))
    return result


def uuid_from_launch_logs(instance_dir: Path) -> set[str]:
    candidates = [instance_dir / "logs" / "latest.log"]
    candidates.extend(
        sorted((instance_dir / "logs").glob("*.log")) if (instance_dir / "logs").is_dir() else []
    )
    found: set[str] = set()
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = LAUNCH_UUID_RE.findall(text)
        for match in matches:
            found.add(_normalise_match(match))
    return found


def detect_local_identities(
    world: Path, instance_dir: Path | None = None
) -> list[IdentityEvidence]:
    candidates: dict[str, IdentityEvidence] = {}
    level_path = world / "level.dat"
    if level_path.is_file():
        try:
            level_player = player_from_level(load_nbt_file(level_path))
            uuid = get_player_uuid(level_player)
            if uuid:
                candidates.setdefault(uuid, IdentityEvidence(uuid)).level_dat_match = True
        except Exception as exc:  # noqa: BLE001 - a corrupt level.dat must not stop scanning
            LOGGER.debug("Could not inspect level.dat while detecting identity: %s", exc)

    playerdata = world / "playerdata"
    if playerdata.is_dir():
        dat_files = sorted(
            (path for path in playerdata.glob("*.dat") if not path.name.endswith(".dat_old")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for index, path in enumerate(dat_files):
            try:
                uuid = get_player_uuid(load_nbt_file(path))
            except Exception as exc:  # noqa: BLE001 - one corrupt player file must not stop scanning
                LOGGER.debug("Could not inspect playerdata %s: %s", path, exc)
                continue
            if uuid and index == 0:
                candidates.setdefault(uuid, IdentityEvidence(uuid)).recent_playerdata_match = True

    if instance_dir:
        cache = read_identity_cache(instance_dir)
        launch_uuids = uuid_from_launch_logs(instance_dir)
        for uuid, name in cache.items():
            evidence = candidates.setdefault(uuid, IdentityEvidence(uuid))
            evidence.username = name
            evidence.usercache_match = True
        for uuid in launch_uuids:
            candidates.setdefault(uuid, IdentityEvidence(uuid)).launcher_arg_match = True

    return sorted(candidates.values(), key=lambda item: item.score, reverse=True)

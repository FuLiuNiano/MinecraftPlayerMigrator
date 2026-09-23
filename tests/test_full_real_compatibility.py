from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.compatibility_scanner import scan_compatibility
from core.models import IdentityEvidence

REAL_ZIP = os.environ.get("MIGRATOR_REAL_ZIP")
REAL_WORLD = os.environ.get("MIGRATOR_FULL_PROGRESS_WORLD") or os.environ.get("MIGRATOR_REAL_WORLD")
REAL_SOURCE_UUID = os.environ.get("MIGRATOR_REAL_SOURCE_UUID")
REAL_TARGET_UUID = os.environ.get("MIGRATOR_REAL_TARGET_UUID")

pytestmark = pytest.mark.real_data


@pytest.mark.skipif(
    not all((REAL_ZIP, REAL_WORLD, REAL_SOURCE_UUID, REAL_TARGET_UUID)),
    reason="MIGRATOR_REAL_ZIP, MIGRATOR_FULL_PROGRESS_WORLD, MIGRATOR_REAL_SOURCE_UUID and MIGRATOR_REAL_TARGET_UUID are required",
)
def test_real_full_compatibility_is_read_only_and_matches_verified_counts() -> None:
    source_uuid = str(REAL_SOURCE_UUID)
    target_uuid = str(REAL_TARGET_UUID)
    evidence = (
        IdentityEvidence(
            uuid=target_uuid,
            level_dat_match=True,
            recent_playerdata_match=True,
            launcher_arg_match=True,
            usercache_match=True,
        ),
    )

    report = scan_compatibility(
        Path(str(REAL_ZIP)),
        source_uuid,
        Path(str(REAL_WORLD)),
        target_uuid,
        evidence,
    )

    assert report.can_migrate is True
    assert report.modules["player"].counts["inventory"] == 38
    assert report.modules["player"].counts["xp_level"] == 64
    assert report.modules["advancements"].counts["total"] == 1113
    assert report.modules["advancements"].counts["completed"] == 1100
    assert report.modules["stats"].counts["categories"] == 9
    assert report.modules["stats"].counts["records"] == 833
    assert report.modules["ftb_quests"].counts == {
        "task_progress": 189,
        "started": 394,
        "completed": 328,
        "claimed_rewards": 97,
    }
    assert report.modules["ftb_team"].counts["members"] == 1
    assert report.modules["waystones"].counts["owned"] == 5
    assert report.modules["endinglib"].counts["total"] == 2
    assert report.modules["cosarmor"].status.value == "READY"

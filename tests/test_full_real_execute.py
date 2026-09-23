from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.compatibility_scanner import scan_compatibility
from core.full_migration import build_full_plan, execute_full_migration
from core.full_verifier import verify_full_migration
from core.models import IdentityEvidence

REAL_ZIP = os.environ.get("MIGRATOR_REAL_ZIP")
REAL_WORLD = os.environ.get("MIGRATOR_FULL_PROGRESS_EXECUTE_WORLD")
REAL_SOURCE_UUID = os.environ.get("MIGRATOR_REAL_SOURCE_UUID")
REAL_TARGET_UUID = os.environ.get("MIGRATOR_REAL_TARGET_UUID")

pytestmark = pytest.mark.real_data


@pytest.mark.skipif(
    not all((REAL_ZIP, REAL_WORLD, REAL_SOURCE_UUID, REAL_TARGET_UUID)),
    reason="explicit disposable execute-world environment is required",
)
def test_real_isolated_full_execute_and_verify() -> None:
    target_uuid = str(REAL_TARGET_UUID)
    report = scan_compatibility(
        Path(str(REAL_ZIP)),
        str(REAL_SOURCE_UUID),
        Path(str(REAL_WORLD)),
        target_uuid,
        (
            IdentityEvidence(
                uuid=target_uuid,
                level_dat_match=True,
                recent_playerdata_match=True,
                launcher_arg_match=True,
                usercache_match=True,
            ),
        ),
    )
    plan = build_full_plan(report)
    result = execute_full_migration(plan)

    assert result.success is True
    assert verify_full_migration(plan).success is True

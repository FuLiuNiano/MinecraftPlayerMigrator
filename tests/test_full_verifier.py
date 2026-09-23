from __future__ import annotations

from pathlib import Path

from core.compatibility_scanner import scan_compatibility
from core.full_migration import build_full_plan
from core.full_verifier import verify_full_migration
from core.models import IdentityEvidence
from core.progress_migration import execute_progress_migration
from tests.helpers import SOURCE_UUID, TARGET_UUID, TEST_USERNAME, write_world
from tests.test_progress_migration import _write_progress_source


def _evidence() -> tuple[IdentityEvidence, ...]:
    return (
        IdentityEvidence(
            uuid=TARGET_UUID,
            level_dat_match=True,
            recent_playerdata_match=True,
            launcher_arg_match=True,
            usercache_match=True,
            username=TEST_USERNAME,
        ),
    )


def _prepared_plan(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    write_world(source, SOURCE_UUID)
    write_world(target, TARGET_UUID, target=True)
    _write_progress_source(source)
    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())
    plan = build_full_plan(report)
    execute_progress_migration(source, target, SOURCE_UUID, TARGET_UUID)
    return plan, target


def test_full_verifier_accepts_verified_progress_outputs(tmp_path: Path) -> None:
    plan, _target = _prepared_plan(tmp_path)

    result = verify_full_migration(plan)

    assert result.success is True
    assert result.player_ok is True
    assert result.player_leveldat_equal is True
    assert result.advancements_ok is True
    assert result.stats_ok is True
    assert result.ftb_quests_ok is True
    assert result.ftb_team_ok is True
    assert result.waystones_ok is True
    assert result.endinglib_ok is True
    assert result.cosarmor_ok is True
    assert result.source_identity_residuals == ()


def test_full_verifier_rejects_wrong_quest_identity(tmp_path: Path) -> None:
    plan, target = _prepared_plan(tmp_path)
    quest_path = target / "ftbquests" / f"{TARGET_UUID}.snbt"
    text = quest_path.read_text(encoding="utf-8")
    text = text.replace(
        TARGET_UUID.replace("-", ""),
        SOURCE_UUID.replace("-", ""),
        1,
    )
    quest_path.write_text(text, encoding="utf-8")

    result = verify_full_migration(plan)

    assert result.success is False
    assert result.ftb_quests_ok is False
    assert "ftbquests.uuid" in result.source_identity_residuals


def test_full_verifier_ignores_unrelated_source_uuid_history(tmp_path: Path) -> None:
    plan, target = _prepared_plan(tmp_path)
    history = target / "data" / "unrelated_world_history.snbt"
    history.write_text(
        f'{{ owner: "{SOURCE_UUID}", note: "historical entity relationship" }}',
        encoding="utf-8",
    )

    result = verify_full_migration(plan)

    assert result.success is True
    assert result.source_identity_residuals == ()


def test_full_verifier_accepts_not_found_optional_modules(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    write_world(source, SOURCE_UUID)
    write_world(target, TARGET_UUID, target=True)
    report = scan_compatibility(source, SOURCE_UUID, target, TARGET_UUID, _evidence())
    plan = build_full_plan(report)

    result = verify_full_migration(plan)

    assert result.success is True
    assert result.player_ok is True
    assert result.advancements_ok is True
    assert result.ftb_quests_ok is True

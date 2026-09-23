from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .backup import atomic_write, create_full_backup, directory_size, ensure_free_space
from .full_models import (
    CompatibilityReport,
    FullMigrationOptions,
    FullMigrationPlan,
    FullMigrationResult,
    FullVerificationResult,
    ModuleReport,
    ModuleStatus,
)
from .full_verifier import verify_full_migration
from .nbt_codec import (
    clone_compound,
    get_player_uuid,
    load_nbt_bytes,
    player_from_level,
    replace_player_uuid,
    serialize_nbt,
    set_level_player,
)
from .process_check import assert_game_closed
from .progress_migration import ProgressMigrationOptions, _prepare
from .snbt_codec import parse_snbt
from .uuid_utils import normalize_uuid
from .version_detector import VersionStatus


class FullMigrationPlanError(ValueError):
    """Raised when a compatibility report cannot produce a safe plan."""


_OPTION_NAMES = (
    "player",
    "advancements",
    "stats",
    "ftb_quests",
    "ftb_team",
    "waystones",
    "endinglib",
    "cosarmor",
)

_PROGRESS_OPTION_NAMES = (
    "advancements",
    "stats",
    "ftb_quests",
    "ftb_team",
    "waystones",
    "endinglib",
    "cosarmor",
)


@dataclass(slots=True)
class PendingWrite:
    """A formal destination staged in a same-directory temporary file."""

    destination: Path
    temp_path: Path
    original_existed: bool
    original_bytes: bytes | None
    module_name: str


class FullMigrationTransaction:
    """One transaction for player data and every selected progress module."""

    def __init__(self, target_world: Path, backup_path: Path | None = None) -> None:
        self.target_world = Path(target_world).resolve()
        self.backup_path = backup_path
        self.pending_writes: list[PendingWrite] = []
        self.committed_writes: list[PendingWrite] = []
        self.created_files: list[Path] = []
        self.overwritten_files: list[Path] = []
        self.temp_files: list[Path] = []
        self.rollback_performed = False
        self.rollback_success = False
        self.rollback_errors: list[str] = []
        self.cleanup_errors: list[str] = []
        self._validators: dict[Path, Callable[[bytes], None] | None] = {}

    @property
    def modified_files(self) -> tuple[Path, ...]:
        return tuple(item.destination for item in self.committed_writes)

    def _destination(self, destination: Path) -> Path:
        path = Path(destination).expanduser()
        if not path.is_absolute():
            path = self.target_world / path
        path = path.resolve()
        try:
            path.relative_to(self.target_world)
        except ValueError as exc:
            raise ValueError(f"迁移目标越过 world 根目录：{path}") from exc
        return path

    def _write_temp(self, path: Path, payload: bytes) -> None:
        with path.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def stage(
        self,
        destination: Path,
        payload: bytes,
        module_name: str,
        *,
        validator: Callable[[bytes], None] | None = None,
    ) -> PendingWrite:
        path = self._destination(destination)
        if any(item.destination == path for item in self.pending_writes):
            raise ValueError(f"重复的事务目标：{path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        original_existed = path.is_file()
        original_bytes = path.read_bytes() if original_existed else None
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.migrator.", suffix=".tmp", dir=path.parent
        )
        os.close(fd)
        temp_path = Path(temp_name)
        self.temp_files.append(temp_path)
        try:
            self._write_temp(temp_path, payload)
        except Exception:
            self._safe_unlink(temp_path)
            raise
        pending = PendingWrite(
            destination=path,
            temp_path=temp_path,
            original_existed=original_existed,
            original_bytes=original_bytes,
            module_name=module_name,
        )
        self.pending_writes.append(pending)
        self._validators[path] = validator
        return pending

    def validate_pending(self) -> None:
        for pending in self.pending_writes:
            if not pending.temp_path.is_file():
                raise FileNotFoundError(f"临时文件不存在：{pending.temp_path}")
            payload = pending.temp_path.read_bytes()
            validator = self._validators.get(pending.destination)
            if validator is not None:
                validator(payload)
            else:
                _validate_payload_by_path(pending.destination, payload)

    def commit(self) -> None:
        self.validate_pending()
        for pending in self.pending_writes:
            os.replace(pending.temp_path, pending.destination)
            self.committed_writes.append(pending)
            if pending.original_existed:
                self.overwritten_files.append(pending.destination)
            else:
                self.created_files.append(pending.destination)

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self.cleanup_errors.append(f"{path}: {exc}")

    def cleanup_temp(self) -> tuple[str, ...]:
        for path in tuple(self.temp_files):
            if path.exists():
                self._safe_unlink(path)
        return tuple(self.cleanup_errors)

    def rollback(self) -> tuple[str, ...]:
        self.rollback_performed = True
        self.rollback_errors = []
        for pending in reversed(self.committed_writes):
            try:
                if pending.original_existed:
                    if pending.original_bytes is None:
                        raise ValueError(f"缺少原始字节：{pending.destination}")
                    atomic_write(pending.destination, pending.original_bytes)
                else:
                    pending.destination.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001 - rollback must report every failure.
                self.rollback_errors.append(f"{pending.destination}: {exc}")
        self.cleanup_temp()
        self.rollback_errors.extend(self.cleanup_errors)
        self.rollback_success = not self.rollback_errors
        return tuple(self.rollback_errors)


def build_full_plan(
    report: CompatibilityReport,
    options: FullMigrationOptions | None = None,
    *,
    backup_prefix: str = "world_backup_before_full_migration",
) -> FullMigrationPlan:
    """Build a read-only full migration plan from a compatibility report."""

    options = options or FullMigrationOptions()
    if report.version_status is not VersionStatus.MATCH:
        status = report.version_status.value if report.version_status else "UNKNOWN"
        raise FullMigrationPlanError(f"Minecraft 版本未通过 MATCH 检查：{status}")
    if report.blocking_issues:
        details = "; ".join(report.blocking_issues)
        raise FullMigrationPlanError(f"无法建立迁移计划：{details}")

    player = report.modules.get("player")
    if player is None or player.status is not ModuleStatus.READY:
        raise FullMigrationPlanError("人物数据不可用，无法建立迁移计划")

    planned: list[Path] = []
    warnings = list(report.warnings)
    for module_name in _OPTION_NAMES:
        module = report.modules.get(module_name)
        if module is None:
            if module_name == "player":
                raise FullMigrationPlanError("兼容性报告缺少人物模块")
            warnings.append(f"{module_name} 未出现在兼容性报告，已跳过")
            continue

        if not options.enabled(module_name):
            if module_name == "player":
                raise FullMigrationPlanError("人物数据不允许关闭")
            warnings.append(f"{module.display_name} 已由用户关闭")
            continue

        if module.status in {
            ModuleStatus.SKIPPED_NOT_FOUND,
            ModuleStatus.SKIPPED_UNSUPPORTED,
        }:
            warnings.extend(module.warnings)
            warnings.append(f"{module.display_name}：{module.status.value}")
            continue
        if module.status in {ModuleStatus.BLOCKED, ModuleStatus.FAILED}:
            reason = module.error or module.display_name
            raise FullMigrationPlanError(f"{module.display_name} 无法纳入迁移计划：{reason}")
        if module.status not in {ModuleStatus.READY, ModuleStatus.SUCCESS}:
            raise FullMigrationPlanError(
                f"{module.display_name} 处于未知状态：{module.status.value}"
            )
        planned.extend(Path(path) for path in module.target_paths)
        warnings.extend(module.warnings)

    unique_planned = tuple(dict.fromkeys(planned))
    overwrite = tuple(path for path in unique_planned if path.is_file())
    new_files = tuple(path for path in unique_planned if not path.exists())
    return FullMigrationPlan(
        source=report.source,
        target_world=report.target_world,
        source_uuid=report.source_uuid,
        target_uuid=report.target_uuid,
        source_username=report.source_username,
        target_username=report.target_username,
        options=options,
        compatibility_report=report,
        planned_files=unique_planned,
        overwrite_files=overwrite,
        new_files=new_files,
        warnings=tuple(dict.fromkeys(warnings)),
        backup_prefix=backup_prefix,
    )


plan_full_migration = build_full_plan


def _validate_payload_by_path(path: Path, payload: bytes) -> None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        json.loads(payload.decode("utf-8"))
    elif suffix == ".snbt":
        parse_snbt(payload.decode("utf-8"))
    else:
        load_nbt_bytes(payload)


def _player_payloads(plan: FullMigrationPlan) -> tuple[bytes, bytes]:
    source = plan.source
    if source is None:
        raise ValueError("迁移计划缺少只读 source")
    source_uuid = normalize_uuid(plan.source_uuid)
    target_uuid = normalize_uuid(plan.target_uuid)
    source_bytes = source.read_bytes(f"playerdata/{source_uuid}.dat")
    source_file = load_nbt_bytes(source_bytes)
    source_player = clone_compound(source_file)
    source_identity = get_player_uuid(source_player)
    if source_identity != source_uuid:
        raise ValueError(
            f"源人物文件名与 NBT UUID 不一致：file={source_uuid}, nbt={source_identity}"
        )
    migrated_player = replace_player_uuid(source_player, target_uuid)

    level_path = plan.target_world / "level.dat"
    level_before = load_nbt_bytes(level_path.read_bytes())
    migrated_level = set_level_player(level_before, migrated_player)
    player_payload = serialize_nbt(
        migrated_player, gzipped=bool(getattr(source_file, "gzipped", True))
    )
    level_payload = serialize_nbt(
        migrated_level, gzipped=bool(getattr(level_before, "gzipped", True))
    )
    return player_payload, level_payload


def _validate_player_payload(payload: bytes, target_uuid: str) -> None:
    parsed = load_nbt_bytes(payload)
    if get_player_uuid(parsed) != target_uuid:
        raise ValueError("临时 playerdata UUID 校验失败")


def _validate_level_payload(payload: bytes, target_uuid: str) -> None:
    parsed = load_nbt_bytes(payload)
    if get_player_uuid(player_from_level(parsed)) != target_uuid:
        raise ValueError("临时 level.dat Data.Player UUID 校验失败")


def _progress_options(plan: FullMigrationPlan) -> ProgressMigrationOptions:
    values: dict[str, bool] = {}
    for name in _PROGRESS_OPTION_NAMES:
        module = plan.compatibility_report.modules.get(name)
        values[name] = bool(
            plan.options.enabled(name)
            and module is not None
            and module.status in {ModuleStatus.READY, ModuleStatus.SUCCESS}
        )
    return ProgressMigrationOptions(
        advancements=values["advancements"],
        stats=values["stats"],
        ftb_quests=values["ftb_quests"],
        ftb_team=values["ftb_team"],
        waystones=values["waystones"],
        endinglib=values["endinglib"],
        cosmetic_armor=values["cosarmor"],
    )


def _module_for_relative(relative: Path) -> str:
    parts = relative.as_posix().split("/")
    if parts[0] == "advancements":
        return "advancements"
    if parts[0] == "stats":
        return "stats"
    if parts[0] == "ftbquests":
        return "ftb_quests"
    if parts[:2] == ["ftbteams", "player"]:
        return "ftb_team"
    if relative.name == "waystones.dat":
        return "waystones"
    if relative.name == "endinglib_saved_data.dat":
        return "endinglib"
    if relative.suffix.lower() == ".cosarmor":
        return "cosarmor"
    raise ValueError(f"无法识别进度文件模块：{relative}")


class _RunLog:
    def __init__(self, plan: FullMigrationPlan) -> None:
        self.lines = [
            f"source={getattr(plan.source, 'location', plan.source)}",
            f"target={plan.target_world}",
            f"source_uuid={plan.source_uuid}",
            f"target_uuid={plan.target_uuid}",
            "enabled_modules="
            + ",".join(name for name in _OPTION_NAMES if plan.options.enabled(name)),
        ]
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
        candidate = log_dir / f"full-migration-{stamp}.log"
        index = 1
        while candidate.exists():
            candidate = log_dir / f"full-migration-{stamp}_{index:02d}.log"
            index += 1
        self.path = candidate

    def add(self, message: str) -> None:
        self.lines.append(message)

    def save(self) -> Path | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        except OSError:
            return None
        return self.path


def _module_results(plan: FullMigrationPlan, *, success: bool) -> tuple[ModuleReport, ...]:
    result: list[ModuleReport] = []
    for name, module in plan.compatibility_report.modules.items():
        if (
            success
            and name in _OPTION_NAMES
            and plan.options.enabled(name)
            and module.status in {ModuleStatus.READY, ModuleStatus.SUCCESS}
        ):
            result.append(replace(module, status=ModuleStatus.SUCCESS))
        else:
            result.append(module)
    return tuple(result)


def execute_full_migration(
    plan: FullMigrationPlan,
    progress_callback: Callable[[str, int], None] | None = None,
) -> FullMigrationResult:
    """Execute all selected modules through one backup and one transaction."""

    log = _RunLog(plan)
    transaction: FullMigrationTransaction | None = None
    backup_path: Path | None = None
    verification = FullVerificationResult()

    def progress(stage: str, percent: int) -> None:
        if progress_callback is not None:
            progress_callback(stage, percent)

    try:
        progress("CHECKING_GAME", 0)
        assert_game_closed()
        if not plan.compatibility_report.can_migrate:
            raise FullMigrationPlanError("兼容性报告不允许执行迁移")
        if plan.source is None:
            raise ValueError("迁移计划缺少 source")
        target = plan.target_world.resolve()
        if not target.is_dir() or not (target / "level.dat").is_file():
            raise FileNotFoundError("目标 world 缺少 level.dat")
        source_location = getattr(plan.source, "location", None)
        if (
            source_location is not None
            and not getattr(plan.source, "is_zip", False)
            and Path(source_location).resolve() == target
        ):
            raise ValueError("source 与 target world 不能相同")

        progress("BACKING_UP", 5)
        ensure_free_space(target, directory_size(target))
        backup_path = create_full_backup(target, prefix=plan.backup_prefix)
        log.add(f"backup_path={backup_path}")
        transaction = FullMigrationTransaction(target, backup_path)

        progress("PREPARING_PLAYER", 10)
        player_payload, level_payload = _player_payloads(plan)
        progress_options = _progress_options(plan)
        prepared = _prepare(
            plan.source,
            target,
            normalize_uuid(plan.source_uuid),
            normalize_uuid(plan.target_uuid),
            progress_options,
        )
        stage_for_module = {
            "advancements": ("PREPARING_ADVANCEMENTS", 20),
            "stats": ("PREPARING_STATS", 28),
            "ftb_quests": ("PREPARING_QUESTS", 36),
            "ftb_team": ("PREPARING_TEAM", 44),
            "waystones": ("PREPARING_WAYSTONES", 52),
            "endinglib": ("PREPARING_ENDINGLIB", 60),
            "cosarmor": ("PREPARING_COSARMOR", 68),
        }

        prepared_modules: list[tuple[Path, bytes, str]] = []
        for relative, payload in prepared.payloads:
            module_name = _module_for_relative(relative)
            stage_name, percent = stage_for_module[module_name]
            progress(stage_name, percent)
            prepared_modules.append((relative, payload, module_name))

        # Preparation is complete before any target-world temporary file is
        # created.  This keeps a preparation failure entirely before the temp
        # write phase, while still allowing one transaction to own every file.
        progress("WRITING_TEMP", 75)
        transaction.stage(
            target / "playerdata" / f"{plan.target_uuid}.dat",
            player_payload,
            "player",
            validator=lambda payload: _validate_player_payload(payload, plan.target_uuid),
        )
        transaction.stage(
            target / "level.dat",
            level_payload,
            "player",
            validator=lambda payload: _validate_level_payload(payload, plan.target_uuid),
        )
        for relative, payload, module_name in prepared_modules:
            transaction.stage(target / relative, payload, module_name)

        progress("VALIDATING_TEMP", 80)
        transaction.validate_pending()
        log.add(
            "prepared_files="
            + ",".join(str(item.destination) for item in transaction.pending_writes)
        )

        progress("COMMITTING", 85)
        transaction.commit()
        log.add(
            "committed_files="
            + ",".join(str(item.destination) for item in transaction.committed_writes)
        )

        progress("VERIFYING", 92)
        verification = verify_full_migration(plan)
        if not verification.success:
            raise ValueError("最终完整性校验失败：" + "; ".join(verification.errors))
        cleanup_errors = transaction.cleanup_temp()
        if cleanup_errors:
            verification = replace(
                verification,
                errors=("临时文件清理失败：" + "; ".join(cleanup_errors),),
            )
            raise OSError(verification.errors[0])

        progress("DONE", 100)
        log.add("verification=SUCCESS")
        log.add("rollback_performed=False")
        return FullMigrationResult(
            success=True,
            backup_path=backup_path,
            modified_files=transaction.modified_files,
            created_files=tuple(transaction.created_files),
            module_results=_module_results(plan, success=True),
            verification=verification,
            rollback_performed=False,
            rollback_success=False,
            log_path=log.save(),
        )
    except Exception as exc:  # noqa: BLE001 - every failure must become a result and rollback.
        primary_error = f"{type(exc).__name__}: {exc}"
        log.add("exception=" + primary_error)
        if transaction is not None:
            progress("ROLLING_BACK", 96)
            transaction.rollback()
            if transaction.rollback_errors:
                primary_error += "；回滚错误：" + "; ".join(transaction.rollback_errors)
        verification = replace(
            verification,
            errors=tuple(dict.fromkeys((*verification.errors, primary_error))),
        )
        log.add(f"rollback_performed={transaction.rollback_performed if transaction else False}")
        log.add(f"rollback_success={transaction.rollback_success if transaction else False}")
        log.add("verification=FAILED")
        return FullMigrationResult(
            success=False,
            backup_path=backup_path,
            modified_files=transaction.modified_files if transaction else (),
            created_files=tuple(transaction.created_files) if transaction else (),
            module_results=_module_results(plan, success=False),
            verification=verification,
            rollback_performed=transaction.rollback_performed if transaction else False,
            rollback_success=transaction.rollback_success if transaction else False,
            log_path=log.save(),
        )

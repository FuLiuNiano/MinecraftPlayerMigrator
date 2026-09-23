from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from core.compatibility_scanner import MODULE_DISPLAY_NAMES
from core.full_migration import build_full_plan
from core.full_models import (
    CompatibilityReport,
    FullMigrationOptions,
    FullMigrationPlan,
    FullMigrationResult,
    ModuleStatus,
)
from core.models import PlayerInfo
from core.world_detector import discover_worlds
from core.zip_reader import WorldSource
from ui.errors import LOGGER, configure_logging, error_code, friendly_error
from ui.workers import CompatibilityWorker, FullMigrationWorker, ScanWorker

APP_VERSION = "1.1.0"
APP_TITLE = "Minecraft Player Migrator"
NBTEXPLORER_URL = "https://github.com/jaquadro/NBTExplorer"


class PathLineEdit(QLineEdit):
    """A line edit that accepts a dropped local file or folder."""

    path_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setPlaceholderText("可拖入文件或文件夹")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.setText(path)
                self.path_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


class MainWindow(QMainWindow):
    """Five-page v1.1 wizard; all migration writes stay in the core API."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self.resize(1040, 760)
        self.setAcceptDrops(True)

        self.source: WorldSource | None = None
        self.players: list[PlayerInfo] = []
        self.selected_player: PlayerInfo | None = None
        self.compatibility_report: CompatibilityReport | None = None
        self.plan: FullMigrationPlan | None = None
        self.last_result: FullMigrationResult | None = None
        self.scan_worker: ScanWorker | None = None
        self.compatibility_worker: CompatibilityWorker | None = None
        self.migration_worker: FullMigrationWorker | None = None
        self.migrating = False
        self.log_path = configure_logging()
        self.navigation_controls: list[QPushButton] = []

        self.pages = QStackedWidget()
        for page in (
            self._build_source_page(),
            self._build_player_page(),
            self._build_target_page(),
            self._build_preview_page(),
            self._build_progress_page(),
        ):
            self.pages.addWidget(page)
        self.setCentralWidget(self.pages)
        self._set_page(0)

    def _page(self, title: str, subtitle: str) -> tuple[QWizardPage, QVBoxLayout]:
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(subtitle)
        layout = QVBoxLayout(page)
        return page, layout

    def _path_row(self, edit: QLineEdit, callback) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        button = QPushButton("浏览")
        button.clicked.connect(callback)
        row.addWidget(button)
        return widget

    def _set_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)

    def _show_error(self, title: str, exc: Exception) -> None:
        LOGGER.exception("%s: %s", title, exc)
        QMessageBox.critical(
            self,
            title,
            f"[{error_code(exc)}] {friendly_error(exc)}\n\n详细信息已记录到日志：{self.log_path}",
        )

    def _open_path(self, path: str | Path) -> None:
        candidate = Path(path)
        if candidate.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))

    def _build_source_page(self) -> QWidget:
        page, layout = self._page(
            "1. 选择服务器来源",
            "选择服务器 world 文件夹或 ZIP。扫描只读，不会修改来源数据。",
        )
        form = QFormLayout()
        self.source_edit = PathLineEdit()
        self.source_edit.setToolTip("支持 world 文件夹、服务器 ZIP、saves 文件夹或实例目录")
        form.addRow("服务器 world / ZIP", self._path_row(self.source_edit, self.choose_source))
        layout.addLayout(form)

        info = QGroupBox("v1.1 安全边界")
        info_layout = QVBoxLayout(info)
        info_layout.addWidget(QLabel("• 先扫描版本和人物，再生成只读兼容性报告。"))
        info_layout.addWidget(QLabel("• 只有同版本 MATCH 才允许建立迁移计划；UNKNOWN/MISMATCH/CONFLICT 会阻止。"))
        info_layout.addWidget(QLabel("• 完整事务由核心层创建备份、原子写入并在失败时回滚。"))
        layout.addWidget(info)

        self.source_status = QLabel("等待选择路径。")
        layout.addWidget(self.source_status)
        row = QHBoxLayout()
        nbt_button = QPushButton("NBTExplorer（可选）")
        nbt_button.clicked.connect(self.open_nbt_explorer)
        row.addWidget(nbt_button)
        row.addStretch(1)
        self.scan_button = QPushButton("扫描服务器人物 →")
        self.scan_button.clicked.connect(self.scan)
        row.addWidget(self.scan_button)
        layout.addLayout(row)
        self.navigation_controls.append(self.scan_button)
        return page

    def choose_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择服务器 ZIP", filter="ZIP (*.zip);;所有文件 (*)"
        )
        if not path:
            path = QFileDialog.getExistingDirectory(self, "选择服务器 world、saves 或实例文件夹")
        if path:
            self.source_edit.setText(path)

    def choose_target(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地目标 world")
        if path:
            self.target_edit.setText(path)

    def choose_instance(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Minecraft 实例目录")
        if path:
            self.instance_edit.setText(path)

    def open_nbt_explorer(self) -> None:
        answer = QMessageBox.information(
            self,
            "NBTExplorer（可选）",
            "NBTExplorer 仅用于人工查看 NBT，不参与迁移。\n\n将打开项目主页。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Ok:
            QDesktopServices.openUrl(QUrl(NBTEXPLORER_URL))

    def _choose_world_if_needed(self, path: Path) -> Path:
        if path.is_file():
            return path
        if (path / "level.dat").is_file() and (path / "playerdata").is_dir():
            return path
        worlds = discover_worlds(path)
        if not worlds:
            raise FileNotFoundError("没有找到包含 level.dat 和 playerdata 的 Minecraft world")
        if len(worlds) == 1:
            return worlds[0]
        raise ValueError("检测到多个 world，请先直接选择要读取的 world 文件夹")

    def _source_path_for_scan(self) -> Path:
        raw = self.source_edit.text().strip()
        if not raw:
            raise ValueError("请先选择服务器 world 文件夹或 ZIP")
        return self._choose_world_if_needed(Path(raw).expanduser().resolve())

    def _target_path(self) -> Path:
        raw = self.target_edit.text().strip()
        if not raw:
            raise ValueError("请先选择本地目标 world")
        target = Path(raw).expanduser().resolve()
        if not target.is_dir() or not (target / "level.dat").is_file():
            raise ValueError("目标路径不是有效的 Minecraft world：缺少 level.dat")
        return target

    def _instance_path(self) -> Path | None:
        raw = self.instance_edit.text().strip()
        return Path(raw).expanduser().resolve() if raw else None

    def _build_player_page(self) -> QWidget:
        page, layout = self._page(
            "2. 选择服务器人物",
            "检查用户名、UUID、背包、等级、坐标和 capability 摘要。",
        )
        self.player_list = QListWidget()
        self.player_list.currentItemChanged.connect(self.show_player)
        layout.addWidget(self.player_list, 2)
        self.player_preview = QPlainTextEdit()
        self.player_preview.setReadOnly(True)
        layout.addWidget(self.player_preview, 2)
        self.player_status = QLabel("等待扫描。")
        layout.addWidget(self.player_status)
        row = QHBoxLayout()
        back = QPushButton("← 返回")
        back.clicked.connect(lambda: self._set_page(0))
        row.addWidget(back)
        row.addStretch(1)
        self.target_button = QPushButton("选择本地 world 与版本 →")
        self.target_button.clicked.connect(self.show_target_page)
        self.target_button.setEnabled(False)
        row.addWidget(self.target_button)
        layout.addLayout(row)
        self.navigation_controls.extend([back, self.target_button])
        return page

    def show_player(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self.selected_player = None
        if current is not None:
            uuid = current.data(Qt.ItemDataRole.UserRole)
            self.selected_player = next((item for item in self.players if item.uuid == uuid), None)
        player = self.selected_player
        if player is None:
            self.player_preview.clear()
            self.target_button.setEnabled(False)
            return
        items = ", ".join(item.item_id for item in player.inventory_preview) or "（空）"
        self.player_preview.setPlainText(
            "服务器人物\n"
            f"用户名：{player.username or '未知'}\n"
            f"UUID：{player.uuid}\n"
            f"Inventory：{player.inventory_count} 项\n"
            f"主要物品：{items}\n"
            f"XpLevel / XpTotal：{player.xp_level} / {player.xp_total}\n"
            f"Pos：{player.position}\n"
            f"Health：{player.health}\n"
            f"ForgeCaps：{'存在' if player.has_forge_caps else '不存在'}；"
            f"Curios：{'存在' if player.has_curios else '不存在'}"
        )
        self.target_button.setEnabled(True)

    def scan(self) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            return
        try:
            source_path = self._source_path_for_scan()
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self._show_error("扫描失败", exc)
            return
        self.source_status.setText("正在读取来源人物 NBT，请稍候...")
        self.scan_button.setEnabled(False)
        self._set_page(1)
        self.scan_worker = ScanWorker(source_path, None, None)
        self.scan_worker.completed.connect(self._scan_completed)
        self.scan_worker.failed.connect(self._scan_failed)
        self.scan_worker.finished.connect(self._scan_finished)
        self.scan_worker.start()

    def _scan_completed(self, payload: object) -> None:
        self.source, self.players, _evidence = payload
        self.player_list.clear()
        for player in self.players:
            username = player.username or "未知玩家"
            item = QListWidgetItem(
                f"{username} | {player.uuid} | Lv{player.xp_level if player.xp_level is not None else '-'} "
                f"| Inventory {player.inventory_count} 项"
            )
            item.setData(Qt.ItemDataRole.UserRole, player.uuid)
            self.player_list.addItem(item)
        self.player_status.setText(f"扫描完成：找到 {len(self.players)} 个服务器人物。")
        self.source_status.setText("来源扫描完成。")
        if self.player_list.count():
            self.player_list.setCurrentRow(0)

    def _scan_failed(self, exc: object) -> None:
        error = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
        self._show_error("扫描失败", error)
        self._set_page(0)

    def _scan_finished(self) -> None:
        self.scan_worker = None
        self.scan_button.setEnabled(True)

    def show_target_page(self) -> None:
        if self.selected_player is None:
            self._show_error("无法继续", ValueError("请先选择服务器人物"))
            return
        self._set_page(2)

    def _build_target_page(self) -> QWidget:
        page, layout = self._page(
            "3. 选择本地 world 与版本",
            "必须是与来源相同 Minecraft 版本的隔离目标 world；本页只读检查。",
        )
        form = QFormLayout()
        self.target_edit = PathLineEdit()
        self.instance_edit = PathLineEdit()
        form.addRow("本地目标 world", self._path_row(self.target_edit, self.choose_target))
        form.addRow("Minecraft 实例（可选）", self._path_row(self.instance_edit, self.choose_instance))
        layout.addLayout(form)
        self.version_summary = QLabel("尚未检查版本。")
        self.version_summary.setWordWrap(True)
        layout.addWidget(self.version_summary)
        self.identity_summary = QLabel("目标本地 UUID 仍未检测。")
        self.identity_summary.setWordWrap(True)
        layout.addWidget(self.identity_summary)
        self.compatibility_status = QLabel("等待检查。")
        layout.addWidget(self.compatibility_status)
        row = QHBoxLayout()
        back = QPushButton("← 返回")
        back.clicked.connect(lambda: self._set_page(1))
        row.addWidget(back)
        row.addStretch(1)
        self.compatibility_button = QPushButton("检查版本与兼容性 →")
        self.compatibility_button.clicked.connect(self.check_compatibility)
        row.addWidget(self.compatibility_button)
        layout.addLayout(row)
        self.navigation_controls.extend([back, self.compatibility_button])
        return page

    def check_compatibility(self) -> None:
        if self.compatibility_worker and self.compatibility_worker.isRunning():
            return
        if self.selected_player is None or self.source is None:
            self._show_error("检查失败", ValueError("请先扫描并选择服务器人物"))
            return
        try:
            target = self._target_path()
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self._show_error("检查失败", exc)
            return
        self.compatibility_button.setEnabled(False)
        self.compatibility_status.setText("正在检查版本、身份和模块结构...")
        self.compatibility_worker = CompatibilityWorker(
            self.source,
            target,
            self.selected_player.uuid,
            self._instance_path(),
            source_username=self.selected_player.username,
        )
        self.compatibility_worker.completed.connect(self._compatibility_completed)
        self.compatibility_worker.failed.connect(self._compatibility_failed)
        self.compatibility_worker.finished.connect(self._compatibility_finished)
        self.compatibility_worker.start()

    def _compatibility_completed(self, report: CompatibilityReport) -> None:
        self.compatibility_report = report
        self._render_report(report)
        try:
            self.plan = build_full_plan(report, self._options_from_checks())
        except Exception as exc:  # noqa: BLE001 - report is shown even if planning is blocked
            self.plan = None
            self.compatibility_status.setText(f"无法建立迁移计划：{exc}")
        self._render_preview()
        self._set_page(3)

    def _compatibility_failed(self, exc: object) -> None:
        error = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
        self._show_error("兼容性检查失败", error)

    def _compatibility_finished(self) -> None:
        self.compatibility_worker = None
        self.compatibility_button.setEnabled(True)

    @staticmethod
    def _version_text(info) -> str:
        if info is None:
            return "未知"
        name = info.display_version or "未知名称"
        data = f", DataVersion={info.data_version}" if info.data_version is not None else ""
        return f"{name}{data}（来源：{info.source}）"

    def _render_report(self, report: CompatibilityReport) -> None:
        self.version_summary.setText(
            f"来源版本：{self._version_text(report.source_version)}\n"
            f"目标版本：{self._version_text(report.target_version)}\n"
            f"状态：{report.version_status.value if report.version_status else 'UNKNOWN'}"
        )
        self.identity_summary.setText(
            f"检测到目标 UUID：{report.target_uuid}\n身份：{report.target_username or '未知'}"
        )
        self.compatibility_status.setText(
            "可以建立迁移计划。" if report.can_migrate else "存在阻断项，迁移按钮将保持禁用。"
        )
        for name, checkbox in self.experimental_checks.items():
            module = report.modules.get(name)
            ready = module is not None and module.status in {ModuleStatus.READY, ModuleStatus.SUCCESS}
            checkbox.setChecked(ready)
            checkbox.setEnabled(ready)
            checkbox.setText(f"{MODULE_DISPLAY_NAMES[name]}：{module.status.value if module else 'UNKNOWN'}")
        for name, checkbox in self.core_checks.items():
            module = report.modules.get(name)
            ready = module is not None and module.status in {ModuleStatus.READY, ModuleStatus.SUCCESS}
            checkbox.setChecked(ready)
            checkbox.setEnabled(name != "player" and ready)

    def _build_preview_page(self) -> QWidget:
        page, layout = self._page(
            "4. 兼容性报告与迁移预览",
            "请确认版本为 MATCH、模块状态和覆盖文件，再开始完整事务。",
        )
        self.core_checks: dict[str, QCheckBox] = {}
        core_box = QGroupBox("核心模块")
        core_layout = QVBoxLayout(core_box)
        for name in ("player", "advancements", "stats"):
            checkbox = QCheckBox(MODULE_DISPLAY_NAMES[name])
            checkbox.setChecked(name == "player")
            checkbox.setEnabled(name != "player")
            self.core_checks[name] = checkbox
            core_layout.addWidget(checkbox)
        layout.addWidget(core_box)

        experimental_box = QGroupBox("实验模块（默认按识别结果启用；未找到或不支持时跳过）")
        experimental_layout = QVBoxLayout(experimental_box)
        self.experimental_checks: dict[str, QCheckBox] = {}
        for name in ("ftb_quests", "ftb_team", "waystones", "endinglib", "cosarmor"):
            checkbox = QCheckBox(MODULE_DISPLAY_NAMES[name])
            checkbox.setEnabled(False)
            checkbox.toggled.connect(self._checks_changed)
            self.experimental_checks[name] = checkbox
            experimental_layout.addWidget(checkbox)
        layout.addWidget(experimental_box)

        self.migration_preview = QPlainTextEdit()
        self.migration_preview.setReadOnly(True)
        layout.addWidget(self.migration_preview, 1)
        self.preview_warning = QLabel("尚未生成迁移计划。")
        self.preview_warning.setWordWrap(True)
        layout.addWidget(self.preview_warning)
        row = QHBoxLayout()
        back = QPushButton("← 返回")
        back.clicked.connect(lambda: self._set_page(2))
        row.addWidget(back)
        row.addStretch(1)
        self.start_button = QPushButton("开始完整事务迁移")
        self.start_button.clicked.connect(self.migrate)
        self.start_button.setEnabled(False)
        row.addWidget(self.start_button)
        layout.addLayout(row)
        self.navigation_controls.extend([back, self.start_button])
        return page

    def _options_from_checks(self) -> FullMigrationOptions:
        return FullMigrationOptions(
            player=True,
            advancements=self.core_checks["advancements"].isChecked(),
            stats=self.core_checks["stats"].isChecked(),
            ftb_quests=self.experimental_checks["ftb_quests"].isChecked(),
            ftb_team=self.experimental_checks["ftb_team"].isChecked(),
            waystones=self.experimental_checks["waystones"].isChecked(),
            endinglib=self.experimental_checks["endinglib"].isChecked(),
            cosarmor=self.experimental_checks["cosarmor"].isChecked(),
        )

    def _checks_changed(self) -> None:
        if self.compatibility_report is None:
            return
        try:
            self.plan = build_full_plan(self.compatibility_report, self._options_from_checks())
        except Exception as exc:  # noqa: BLE001 - show disabled state, not traceback
            self.plan = None
            self.preview_warning.setText(f"无法建立迁移计划：{exc}")
        self._render_preview()

    def _render_preview(self) -> None:
        report = self.compatibility_report
        if report is None:
            self.migration_preview.setPlainText("尚未完成兼容性检查。")
            self.start_button.setEnabled(False)
            return
        lines = [
            f"来源：{report.source_label}",
            f"目标：{report.target_world}",
            f"版本状态：{report.version_status.value if report.version_status else 'UNKNOWN'}",
            f"目标 UUID：{report.target_uuid}",
            "",
            "模块状态：",
        ]
        for module in report.modules.values():
            counts = ", ".join(f"{key}={value}" for key, value in module.counts.items())
            suffix = f"（{counts}）" if counts else ""
            lines.append(f"  {module.display_name}：{module.status.value}{suffix}")
        if report.blocking_issues:
            lines.extend(["", "阻断项：", *[f"  • {item}" for item in report.blocking_issues]])
        if report.warnings:
            lines.extend(["", "警告：", *[f"  • {item}" for item in report.warnings]])
        lines.extend(["", "事务策略：先完整备份，再临时写入、重新解析、原子替换，失败时回滚。"])
        self.migration_preview.setPlainText("\n".join(lines))
        if report.blocking_issues:
            self.preview_warning.setStyleSheet("color: #b00020; font-weight: bold;")
            if report.version_status is not None and report.version_status.value == "MISMATCH":
                message = "Minecraft 版本不一致，不能开始迁移。请更换同版本目标。"
            elif report.version_status is not None:
                message = (
                    f"Minecraft 版本无法安全确认（{report.version_status.value}），不能开始迁移。"
                )
            else:
                message = "存在阻断项，不能开始迁移。请修正存档后重新检查。"
            self.preview_warning.setText(message)
            self.start_button.setEnabled(False)
        elif self.plan is not None:
            self.preview_warning.setStyleSheet("color: #166534;")
            self.preview_warning.setText("兼容性检查通过。开始后将创建完整备份并由核心事务统一提交。")
            self.start_button.setEnabled(True)
        else:
            self.start_button.setEnabled(False)

    def _build_progress_page(self) -> QWidget:
        page, layout = self._page("5. 迁移进度与结果", "迁移期间请不要启动 Minecraft 或关闭本程序。")
        self.progress_label = QLabel("准备中...")
        layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        self.progress_detail = QPlainTextEdit()
        self.progress_detail.setReadOnly(True)
        layout.addWidget(self.progress_detail, 1)
        self.result_summary = QPlainTextEdit()
        self.result_summary.setReadOnly(True)
        layout.addWidget(self.result_summary, 2)
        row = QHBoxLayout()
        self.open_backup_button = QPushButton("打开备份目录")
        self.open_backup_button.clicked.connect(self._open_backup)
        self.open_backup_button.setEnabled(False)
        row.addWidget(self.open_backup_button)
        self.open_target_button = QPushButton("打开目标 world")
        self.open_target_button.clicked.connect(self._open_target)
        self.open_target_button.setEnabled(False)
        row.addWidget(self.open_target_button)
        row.addStretch(1)
        self.finish_button = QPushButton("完成")
        self.finish_button.clicked.connect(self.close)
        row.addWidget(self.finish_button)
        layout.addLayout(row)
        return page

    def migrate(self) -> None:
        if self.plan is None:
            self._show_error("无法迁移", ValueError("尚未生成有效迁移计划"))
            return
        answer = QMessageBox.question(
            self,
            "确认完整事务迁移",
            "将先创建完整 world 备份，再统一迁移已勾选模块。\n\n确认 Minecraft、启动器和 Java 游戏进程均已退出吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._set_migrating(True)
        self._set_page(4)
        self.progress_label.setText("准备中...")
        self.progress_bar.setValue(0)
        self.progress_detail.clear()
        self.result_summary.clear()
        self.last_result = None
        self.migration_worker = FullMigrationWorker(self.plan)
        self.migration_worker.stage_changed.connect(self._migration_stage)
        self.migration_worker.progress_changed.connect(self._migration_progress)
        self.migration_worker.completed.connect(self._migration_completed)
        self.migration_worker.failed.connect(self._migration_failed)
        self.migration_worker.finished.connect(self._migration_finished)
        self.migration_worker.start()

    def _set_migrating(self, active: bool) -> None:
        self.migrating = active
        for control in self.navigation_controls:
            control.setEnabled(not active)
        if active:
            self.open_backup_button.setEnabled(False)
            self.open_target_button.setEnabled(False)

    def _migration_stage(self, stage: str) -> None:
        labels = {
            "CHECKING_GAME": "正在检查游戏进程",
            "BACKING_UP": "正在创建完整备份",
            "PREPARING_PLAYER": "正在准备 Player 数据",
            "PREPARING_ADVANCEMENTS": "正在准备 Advancements",
            "PREPARING_STATS": "正在准备 Stats",
            "PREPARING_QUESTS": "正在准备 FTB Quests",
            "PREPARING_TEAM": "正在准备 FTB Teams",
            "PREPARING_WAYSTONES": "正在准备 Waystones",
            "PREPARING_ENDINGLIB": "正在准备 Ending Library",
            "PREPARING_COSARMOR": "正在准备 Cosmetic Armor",
            "WRITING_TEMP": "正在写入临时文件",
            "VALIDATING_TEMP": "正在重新解析并校验临时文件",
            "COMMITTING": "正在原子提交迁移",
            "VERIFYING": "正在执行完整校验",
            "ROLLING_BACK": "正在回滚事务",
            "DONE": "迁移完成",
        }
        text = labels.get(stage, stage)
        self.progress_label.setText(text)
        self.progress_detail.appendPlainText(text)

    def _migration_progress(self, percent: int) -> None:
        self.progress_bar.setValue(max(0, min(100, int(percent))))

    def _migration_completed(self, result: FullMigrationResult) -> None:
        self.last_result = result
        self._render_result(result, success=True)

    def _migration_failed(self, payload: object) -> None:
        if isinstance(payload, FullMigrationResult):
            self.last_result = payload
            self._render_result(payload, success=False)
            return
        error = payload if isinstance(payload, Exception) else RuntimeError(str(payload))
        self._show_error("迁移失败", error)
        self.progress_label.setText("迁移失败")
        self.result_summary.setPlainText(f"[{error_code(error)}] {friendly_error(error)}")

    def _render_result(self, result: FullMigrationResult, *, success: bool) -> None:
        verification = result.verification
        lines = [
            "完整事务迁移成功。" if success else "完整事务迁移失败。",
            f"完整备份：{result.backup_path or '未创建'}",
            f"回滚：{'已成功' if result.rollback_success else ('已执行但需检查' if result.rollback_performed else '未执行')}",
            f"Player 校验：{'通过' if verification.player_ok else '失败'}",
            f"Player 双写一致：{'是' if verification.player_leveldat_equal else '否'}",
            f"Advancements：{'通过' if verification.advancements_ok else '未通过/未启用'}",
            f"Stats：{'通过' if verification.stats_ok else '未通过/未启用'}",
            (
                f"FTB Quests / Teams：{'通过' if verification.ftb_quests_ok else '未通过/未启用'} / "
                f"{'通过' if verification.ftb_team_ok else '未通过/未启用'}"
            ),
            f"日志：{result.log_path or '无'}",
        ]
        if not success and not result.rollback_success:
            lines.extend(
                [
                    "",
                    "高风险警告：请不要启动此世界。",
                    "请从完整备份恢复。",
                ]
            )
        if verification.errors:
            lines.extend(["", "错误：", *[f"  • {item}" for item in verification.errors]])
        self.result_summary.setPlainText("\n".join(lines))
        self.progress_label.setText("迁移完成" if success else "迁移失败")
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        self.open_backup_button.setEnabled(bool(result.backup_path))
        self.open_target_button.setEnabled(True)

    def _migration_finished(self) -> None:
        self.migration_worker = None
        self._set_migrating(False)

    def _open_backup(self) -> None:
        if self.last_result and self.last_result.backup_path:
            self._open_path(self.last_result.backup_path)

    def _open_target(self) -> None:
        if self.plan:
            self._open_path(self.plan.target_world)

    def closeEvent(self, event) -> None:
        workers = [self.scan_worker, self.compatibility_worker, self.migration_worker]
        if self.migrating or any(worker and worker.isRunning() for worker in workers):
            QMessageBox.warning(self, "任务仍在运行", "请等待当前任务完成后再关闭窗口。")
            event.ignore()
            return
        event.accept()


def run_gui() -> int:
    configure_logging()
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()

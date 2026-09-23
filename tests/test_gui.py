import os

import pytest


def test_gui_constructs_without_starting_minecraft(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    from ui.main_window import MainWindow

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    window = MainWindow()
    assert "Minecraft" in window.windowTitle()
    assert window.pages.count() == 5
    assert window.source_edit.acceptDrops() is True
    window.close()
    app.processEvents()

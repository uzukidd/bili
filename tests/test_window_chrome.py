"""Offscreen Qt coverage for custom window chrome.

无界面 Qt 标题栏与窗口边缘交互测试。
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import Mock

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from bili_player.i18n import translate
from bili_player.window_chrome import FramelessController, TitleBar, install_frameless


_REAL_HANDLE = object()


class HostWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.handle_override = _REAL_HANDLE
        self.minimize_calls = self.maximize_calls = self.restore_calls = self.close_calls = 0
        self.keep_on_close = False

    def windowHandle(self):
        if self.handle_override is _REAL_HANDLE:
            return super().windowHandle()
        return self.handle_override

    def showMinimized(self):
        self.minimize_calls += 1
        super().showMinimized()

    def showMaximized(self):
        self.maximize_calls += 1
        super().showMaximized()

    def showNormal(self):
        self.restore_calls += 1
        super().showNormal()

    def closeEvent(self, event):
        self.close_calls += 1
        if self.keep_on_close:
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)


class WindowChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.windows = []
        self.window = self.make_window()
        self.bar = TitleBar(self.window)
        layout = QVBoxLayout(self.window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.bar)
        layout.addStretch()
        self.window.show()
        self.app.processEvents()

    def make_window(self):
        window = HostWindow()
        window.resize(480, 240)
        self.windows.append(window)
        return window

    def tearDown(self):
        for window in self.windows:
            if sip.isdeleted(window):
                continue
            controller = getattr(window, "_bili_frameless_controller", None)
            if controller is not None and not sip.isdeleted(controller):
                controller.dispose()
            window.keep_on_close = False
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def mouse(self, widget, kind, point, *, button=Qt.MouseButton.NoButton,
              buttons=Qt.MouseButton.NoButton, global_point=None):
        if global_point is None:
            global_point = widget.mapToGlobal(point)
        event = QMouseEvent(kind, QPointF(point), QPointF(global_point), button,
                            buttons, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(widget, event)
        return event

    def test_title_bar_does_not_change_host_flags(self):
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertEqual(self.bar.title_label.text(), "Bili Music")
        self.assertEqual(self.bar.minimize_button.size().width(), 36)
        self.assertEqual(self.bar.minimize_button.size().height(), 30)
        self.assertLess(self.bar.title_label.geometry().right(), self.bar.minimize_button.x())
        self.assertLess(self.bar.minimize_button.x(), self.bar.maximize_button.x())
        self.assertLess(self.bar.maximize_button.x(), self.bar.close_button.x())

    def test_window_title_and_language_follow_live_changes(self):
        self.window.setWindowTitle("Bili Music · Playing")
        self.assertEqual(self.bar.title_label.text(), "Bili Music · Playing")
        self.window.setWindowTitle("")
        self.assertEqual(self.bar.title_label.text(), "Bili Music")
        self.assertEqual(self.bar.minimize_button.toolTip(), "最小化")
        self.bar.set_language("en_US")
        for button, key in ((self.bar.minimize_button, "最小化"),
                            (self.bar.maximize_button, "最大化"),
                            (self.bar.close_button, "关闭窗口")):
            self.assertEqual(button.toolTip(), translate(key, "en_US"))
            self.assertEqual(button.accessibleName(), button.toolTip())
        self.assertEqual(self.bar.toolTip(), translate("拖动窗口", "en_US"))
        self.bar.set_language("zh_CN")
        self.assertEqual(self.bar.close_button.toolTip(), "关闭窗口")

    def test_minimize_and_close_use_existing_window_behavior(self):
        QTest.mouseClick(self.bar.minimize_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.minimize_calls, 1)
        self.assertTrue(self.window.isMinimized())
        self.window.showNormal()
        self.window.keep_on_close = True
        QTest.mouseClick(self.bar.close_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.close_calls, 1)
        self.assertFalse(self.window.isVisible())
        self.assertFalse(sip.isdeleted(self.window))

    def test_maximize_restore_button_and_external_state_refresh(self):
        QTest.mouseClick(self.bar.maximize_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.isMaximized())
        self.assertEqual(self.window.maximize_calls, 1)
        self.assertEqual(self.bar.maximize_button.symbol, "restore")
        self.assertEqual(self.bar.maximize_button.toolTip(), "还原窗口")
        QTest.mouseClick(self.bar.maximize_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.isMaximized())
        self.assertEqual(self.window.restore_calls, 1)
        self.assertEqual(self.bar.maximize_button.symbol, "maximize")
        self.window.showMaximized()
        self.app.processEvents()
        self.assertEqual(self.bar.maximize_button.symbol, "restore")
        self.window.showNormal()
        self.app.processEvents()
        self.assertEqual(self.bar.maximize_button.symbol, "maximize")

    def test_double_click_header_and_title_toggle_both_directions(self):
        for widget in (self.bar, self.bar.title_label):
            with self.subTest(widget=widget.objectName()):
                QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(25, 15))
                self.assertTrue(self.window.isMaximized())
                QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(25, 15))
                self.assertFalse(self.window.isMaximized())
        self.assertEqual(self.window.minimize_calls, 0)
        self.assertEqual(self.window.close_calls, 0)

    def test_header_uses_native_drag_and_buttons_do_not(self):
        handle = Mock()
        handle.startSystemMove.return_value = True
        self.window.handle_override = handle
        for widget in (self.bar, self.bar.title_label):
            QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(25, 15))
        self.assertEqual(handle.startSystemMove.call_count, 2)
        QTest.mouseClick(self.bar.maximize_button, Qt.MouseButton.LeftButton)
        self.assertEqual(handle.startSystemMove.call_count, 2)
        self.assertEqual(self.window.maximize_calls, 1)
        self.assertEqual(self.window.close_calls, 0)

    def test_fallback_drag_without_handle_and_after_native_rejection(self):
        for handle in (None, Mock(startSystemMove=Mock(return_value=False))):
            with self.subTest(handle=handle):
                self.window.handle_override = handle
                start = self.window.pos()
                point = QPoint(25, 15)
                original_global = self.bar.mapToGlobal(point)
                self.mouse(self.bar, QEvent.Type.MouseButtonPress, point,
                           button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
                self.mouse(self.bar, QEvent.Type.MouseMove, point + QPoint(37, 21),
                           buttons=Qt.MouseButton.LeftButton, global_point=original_global + QPoint(37, 21))
                self.assertEqual(self.window.pos(), start + QPoint(37, 21))
                self.mouse(self.bar, QEvent.Type.MouseButtonRelease, point,
                           button=Qt.MouseButton.LeftButton)
                released = self.window.pos()
                self.mouse(self.bar, QEvent.Type.MouseMove, point + QPoint(80, 60))
                self.assertEqual(self.window.pos(), released)
        self.assertEqual(self.window.close_calls, 0)
        self.assertEqual(self.window.maximize_calls, 0)

    def test_right_button_header_does_not_drag_or_maximize(self):
        handle = Mock()
        self.window.handle_override = handle
        QTest.mouseClick(self.bar, Qt.MouseButton.RightButton)
        QTest.mouseDClick(self.bar, Qt.MouseButton.RightButton)
        handle.startSystemMove.assert_not_called()
        self.assertFalse(self.window.isMaximized())

    def test_install_sets_real_flags_preserves_other_flags_and_is_idempotent(self):
        self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        controller = install_frameless(self.window)
        self.assertIsInstance(controller, FramelessController)
        self.assertIs(controller.parent(), self.window)
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertIs(install_frameless(self.window), controller)

    def test_all_eight_resize_edges_use_native_handle_and_cursor(self):
        controller = install_frameless(self.window)
        self.window.show()
        self.app.processEvents()
        handle = Mock(startSystemResize=Mock(return_value=True))
        self.window.handle_override = handle
        width, height = self.window.width(), self.window.height()
        for point, edges, cursor in (
            (QPoint(1, height // 2), Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            (QPoint(width - 2, height // 2), Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            (QPoint(width // 2, 1), Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            (QPoint(width // 2, height - 2), Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            (QPoint(1, 1), Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeFDiagCursor),
            (QPoint(width - 2, 1), Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeBDiagCursor),
            (QPoint(1, height - 2), Qt.Edge.LeftEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeBDiagCursor),
            (QPoint(width - 2, height - 2), Qt.Edge.RightEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeFDiagCursor),
        ):
            with self.subTest(edges=edges):
                self.mouse(self.window, QEvent.Type.MouseMove, point)
                self.assertEqual(self.window.cursor().shape(), cursor)
                self.mouse(self.window, QEvent.Type.MouseButtonPress, point,
                           button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
                handle.startSystemResize.assert_called_with(edges)
        self.mouse(self.window, QEvent.Type.MouseMove, QPoint(20, 50))
        self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(controller.BORDER_WIDTH, 8)

    def test_resize_reaches_new_children_and_excludes_buttons_other_windows_and_maximized(self):
        install_frameless(self.window)
        self.window.show()
        handle = Mock(startSystemResize=Mock(return_value=True), startSystemMove=Mock(return_value=True))
        self.window.handle_override = handle
        child = QWidget(self.window)
        child.setGeometry(0, 80, 80, 80)
        child.show()
        self.app.processEvents()
        self.assertTrue(child.hasMouseTracking())
        self.mouse(child, QEvent.Type.MouseButtonPress, QPoint(1, 15),
                   button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
        handle.startSystemResize.assert_called_once_with(Qt.Edge.LeftEdge)
        handle.startSystemResize.reset_mock()
        button = QPushButton("pin", self.window)
        button.setGeometry(0, 80, 36, 30)
        button.show()
        clicked = Mock()
        button.clicked.connect(clicked)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(1, 15))
        clicked.assert_called_once()
        handle.startSystemResize.assert_not_called()
        other = self.make_window()
        other.show()
        self.mouse(other, QEvent.Type.MouseButtonPress, QPoint(1, 50),
                   button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
        handle.startSystemResize.assert_not_called()
        self.window.showMaximized()
        self.mouse(child, QEvent.Type.MouseButtonPress, QPoint(1, 15),
                   button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
        handle.startSystemResize.assert_not_called()
        self.window.showNormal()
        QTest.mouseClick(self.bar, Qt.MouseButton.LeftButton, pos=QPoint(30, 18))
        handle.startSystemMove.assert_called_once()
        handle.startSystemResize.assert_not_called()

    def test_border_width_and_native_rejection_do_not_consume_interior_clicks(self):
        controller = install_frameless(self.window)
        self.window.show()
        handle = Mock(startSystemResize=Mock(return_value=False))
        self.window.handle_override = handle
        for point in (QPoint(7, 50), QPoint(8, 50)):
            self.mouse(self.window, QEvent.Type.MouseButtonPress, point,
                       button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
        handle.startSystemResize.assert_called_once_with(Qt.Edge.LeftEdge)
        self.assertFalse(controller._edges_at(self.window, QPoint(-1, 50)))
        self.window.handle_override = None
        self.mouse(self.window, QEvent.Type.MouseButtonPress, QPoint(1, 50),
                   button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)

    def test_dispose_restores_tracking_and_cursor_and_stops_filter(self):
        self.window.setMouseTracking(False)
        self.window.setCursor(Qt.CursorShape.CrossCursor)
        controller = install_frameless(self.window)
        self.window.show()
        self.mouse(self.window, QEvent.Type.MouseMove, QPoint(1, 50))
        self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.SizeHorCursor)
        controller.dispose()
        controller.dispose()
        self.assertFalse(self.window.hasMouseTracking())
        self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.CrossCursor)
        handle = Mock()
        self.window.handle_override = handle
        self.mouse(self.window, QEvent.Type.MouseButtonPress, QPoint(1, 50),
                   button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton)
        handle.startSystemResize.assert_not_called()

    def test_window_destruction_removes_application_filter(self):
        window = self.make_window()
        controller = install_frameless(window)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(window))
        self.assertTrue(sip.isdeleted(controller))
        self.assertTrue(controller._disposed)
        self.mouse(self.window, QEvent.Type.MouseMove, QPoint(1, 50))


if __name__ == "__main__":
    unittest.main()

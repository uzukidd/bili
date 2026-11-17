"""Real Qt interaction coverage for user-only seek requests."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QSlider, QStyle, QStyleFactory, QStyleOptionSlider

from bili_player.widgets import SeekSlider


class SeekSliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.slider = SeekSlider()
        self.qt_style = QStyleFactory.create("Fusion")
        self.slider.setStyle(self.qt_style)
        self.slider.setRange(100, 1100)
        self.slider.resize(412, 32)
        self.slider.show()
        self.app.processEvents()
        self.seeks = QSignalSpy(self.slider.seekRequested)

    def tearDown(self):
        self.slider.close()
        self.slider.deleteLater()
        self.app.processEvents()

    def point(self, fraction):
        option = QStyleOptionSlider()
        self.slider.initStyleOption(option)
        groove = self.slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderGroove, self.slider,
        )
        handle = self.slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderHandle, self.slider,
        )
        return QPoint(
            groove.x() + handle.width() // 2
            + round((groove.width() - handle.width()) * fraction),
            groove.center().y(),
        )

    def click(self, fraction):
        QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(fraction))

    def test_horizontal_parent_constructor(self):
        parent = self.slider
        child = SeekSlider(parent)
        self.assertIs(child.parent(), parent)
        self.assertEqual(child.orientation(), Qt.Orientation.Horizontal)

    def test_click_quarter_middle_and_endpoints(self):
        for fraction, expected in ((0.25, 350), (0.5, 600), (0, 100), (1, 1100)):
            with self.subTest(fraction=fraction):
                before = len(self.seeks)
                self.click(fraction)
                self.assertAlmostEqual(self.slider.value(), expected, delta=2)
                self.assertEqual(len(self.seeks), before + 1)
                self.assertEqual(self.seeks[-1][0], self.slider.value())
                self.assertFalse(self.slider.isSliderDown())

    def test_click_on_handle_center_keeps_absolute_value(self):
        self.slider.setValue(600)
        self.click(0.5)
        self.assertAlmostEqual(self.slider.value(), 600, delta=2)
        self.assertEqual(len(self.seeks), 1)

    def test_drag_updates_display_and_seeks_once_on_release(self):
        moved = QSignalSpy(self.slider.sliderMoved)
        pressed = QSignalSpy(self.slider.sliderPressed)
        released = QSignalSpy(self.slider.sliderReleased)
        states = []
        self.slider.sliderMoved.connect(
            lambda value: states.append((value, self.slider.isSliderDown()))
        )
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=self.point(0.25))
        self.assertTrue(self.slider.isSliderDown())
        self.assertAlmostEqual(self.slider.value(), 350, delta=2)
        self.assertEqual(len(self.seeks), 0)
        QTest.mouseMove(self.slider, self.point(0.75))
        self.assertTrue(self.slider.isSliderDown())
        self.assertAlmostEqual(self.slider.value(), 850, delta=2)
        self.assertGreaterEqual(len(moved), 2)
        self.assertTrue(all(down for _, down in states))
        self.assertEqual(len(self.seeks), 0)
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=self.point(1))
        self.assertFalse(self.slider.isSliderDown())
        self.assertEqual(self.slider.value(), 1100)
        self.assertEqual(list(self.seeks), [[1100]])
        self.assertEqual(len(pressed), 1)
        self.assertEqual(len(released), 1)

    def test_click_signal_order_and_down_state(self):
        events = []
        self.slider.sliderPressed.connect(lambda: events.append(("pressed", self.slider.isSliderDown())))
        self.slider.sliderReleased.connect(lambda: events.append(("released", self.slider.isSliderDown())))
        self.slider.seekRequested.connect(lambda value: events.append((value, self.slider.isSliderDown())))
        self.click(1)
        self.assertEqual(events, [("pressed", True), ("released", False), (1100, False)])

    def test_disabled_and_zero_range_do_not_seek(self):
        self.slider.setEnabled(False)
        self.click(0.5)
        self.assertEqual(self.slider.value(), 100)
        self.assertFalse(self.slider.isSliderDown())
        self.slider.setEnabled(True)
        self.slider.setRange(0, 0)
        self.click(1)
        QTest.keyClick(self.slider, Qt.Key.Key_End)
        self.assertEqual(self.slider.value(), 0)
        self.assertFalse(self.slider.isSliderDown())
        self.assertEqual(len(self.seeks), 0)

    def test_programmatic_value_range_and_position_do_not_seek(self):
        self.slider.setValue(800)
        self.slider.setRange(0, 500)
        self.slider.setSliderPosition(250)
        self.slider.setMinimum(300)
        self.slider.setMaximum(350)
        self.assertEqual(len(self.seeks), 0)

    def test_keyboard_navigation_emits_once_per_changed_value(self):
        self.slider.setValue(600)
        self.slider.setSingleStep(10)
        self.slider.setPageStep(100)
        for key, expected in (
            (Qt.Key.Key_Right, 610),
            (Qt.Key.Key_Left, 600),
            (Qt.Key.Key_PageUp, 700),
            (Qt.Key.Key_PageDown, 600),
            (Qt.Key.Key_End, 1100),
            (Qt.Key.Key_Home, 100),
        ):
            with self.subTest(key=key):
                previous = len(self.seeks)
                QTest.keyClick(self.slider, key)
                self.assertEqual(self.slider.value(), expected)
                self.assertEqual(len(self.seeks), previous + 1)
                self.assertEqual(self.seeks[-1][0], expected)
        QTest.keyClick(self.slider, Qt.Key.Key_Home)
        QTest.keyClick(self.slider, Qt.Key.Key_Left)
        QTest.keyClick(self.slider, Qt.Key.Key_A)
        self.assertEqual(len(self.seeks), 6)

    def wheel(self, widget, delta):
        point = QPointF(widget.rect().center())
        event = QWheelEvent(
            point, QPointF(widget.mapToGlobal(point.toPoint())), QPoint(), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        self.app.sendEvent(widget, event)

    def test_wheel_matches_standard_slider_and_emits_once_per_change(self):
        standard = QSlider(Qt.Orientation.Horizontal)
        standard.setRange(100, 1100)
        standard.setValue(600)
        self.slider.setValue(600)
        self.wheel(standard, 120)
        self.wheel(self.slider, 120)
        self.assertEqual(self.slider.value(), standard.value())
        self.assertEqual(list(self.seeks), [[standard.value()]])
        self.slider.setValue(self.slider.maximum())
        self.wheel(self.slider, 120)
        self.assertEqual(len(self.seeks), 1)
        self.slider.setRange(0, 0)
        self.wheel(self.slider, -120)
        self.assertEqual(len(self.seeks), 1)
        standard.deleteLater()

    def test_right_click_does_not_seek(self):
        self.slider.setValue(600)
        QTest.mouseClick(self.slider, Qt.MouseButton.RightButton, pos=self.point(0.75))
        self.assertEqual(len(self.seeks), 0)
        self.assertFalse(self.slider.isSliderDown())

    def test_rtl_and_inverted_appearance_mapping(self):
        for rtl, inverted, left, right in (
            (False, False, 100, 1100),
            (True, False, 1100, 100),
            (False, True, 1100, 100),
            (True, True, 100, 1100),
        ):
            with self.subTest(rtl=rtl, inverted=inverted):
                self.slider.setLayoutDirection(
                    Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight
                )
                self.slider.setInvertedAppearance(inverted)
                self.click(0)
                self.assertEqual(self.slider.value(), left)
                self.click(1)
                self.assertEqual(self.slider.value(), right)

    def test_styled_groove_and_handle_endpoints(self):
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; margin: 0 19px; }"
            "QSlider::handle:horizontal { width: 12px; margin: -4px 0; }"
        )
        self.click(0)
        self.assertEqual(self.slider.value(), 100)
        self.click(1)
        self.assertEqual(self.slider.value(), 1100)
        self.click(0.5)
        self.assertAlmostEqual(self.slider.value(), 600, delta=2)

    def test_drag_without_tracking_commits_final_position_once(self):
        self.slider.setTracking(False)
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=self.point(0.25))
        QTest.mouseMove(self.slider, self.point(0.75))
        self.assertTrue(self.slider.isSliderDown())
        self.assertEqual(len(self.seeks), 0)
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=self.point(0.75))
        self.assertAlmostEqual(self.slider.value(), 850, delta=2)
        self.assertEqual(list(self.seeks), [[self.slider.value()]])


if __name__ == "__main__":
    unittest.main()

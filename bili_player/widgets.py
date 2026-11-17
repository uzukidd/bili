"""Reusable controls that distinguish user seeking from playback updates."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class SeekSlider(QSlider):
    """Horizontal absolute-position slider with one seek per user action."""

    seekRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._seeking = False

    def _value_at(self, point):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderGroove, self,
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderHandle, self,
        )
        start = groove.x()
        span = max(0, groove.width() - handle.width())
        position = point.x() - start - handle.width() // 2
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), position, span, option.upsideDown,
        )

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if not self.isEnabled() or self.minimum() == self.maximum():
            event.ignore()
            return
        self._seeking = True
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setSliderDown(True)
        self.setSliderPosition(self._value_at(event.position().toPoint()))
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._seeking:
            super().mouseMoveEvent(event)
            return
        if self.isEnabled() and self.minimum() != self.maximum():
            self.setSliderPosition(self._value_at(event.position().toPoint()))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._seeking:
            super().mouseReleaseEvent(event)
            return
        self._seeking = False
        can_seek = self.isEnabled() and self.minimum() != self.maximum()
        if can_seek:
            self.setSliderPosition(self._value_at(event.position().toPoint()))
            self.setValue(self.sliderPosition())
        self.setSliderDown(False)
        if can_seek:
            self.seekRequested.emit(self.value())
        event.accept()

    def keyPressEvent(self, event):
        previous = self.value()
        super().keyPressEvent(event)
        if self.isEnabled() and self.value() != previous:
            self.seekRequested.emit(self.value())

    def wheelEvent(self, event):
        previous = self.value()
        super().wheelEvent(event)
        if self.isEnabled() and self.value() != previous:
            self.seekRequested.emit(self.value())

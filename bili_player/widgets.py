"""Reusable controls that distinguish user seeking from playback updates."""
from __future__ import annotations

from html import escape

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QTextLayout, QTextOption
from PyQt6.QtWidgets import QLabel, QSizePolicy, QSlider, QStyle, QStyleOptionSlider


class PlaybackTitleLabel(QLabel):
    """Show two complete lines and keep the original title available on hover."""

    def __init__(self):
        super().__init__()
        self.setObjectName("section")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def setText(self, text: str):
        super().setText(text)
        self.setToolTip(f"<qt>{escape(text)}</qt>" if text else "")

    def sizeHint(self):
        return self.minimumSizeHint()

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height() + self.fontMetrics().lineSpacing())

    def _title_layout(self, width):
        # A song title reads as one paragraph, even when the source includes line breaks.
        title = " ".join(self.text().split())
        layout = QTextLayout(title, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        metrics = self.fontMetrics()
        layout.beginLayout()
        for row in range(2):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, row * metrics.lineSpacing()))
        layout.endLayout()
        last_line = None
        if layout.lineCount() == 2:
            # QTextLayout indexes UTF-16 code units, including surrogate pairs in emoji.
            second = layout.lineAt(1)
            encoded = title.encode("utf-16-le")
            if (second.textStart() + second.textLength()) * 2 < len(encoded):
                remainder = encoded[second.textStart() * 2:].decode("utf-16-le")
                last_line = metrics.elidedText(remainder, Qt.TextElideMode.ElideRight, width)
        return layout, last_line

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        layout, last_line = self._title_layout(self.width())
        if layout.lineCount() == 0:
            return
        line_count = min(layout.lineCount(), 2)
        height = self.fontMetrics().height() + (line_count - 1) * self.fontMetrics().lineSpacing()
        top = max(0, (self.height() - height) / 2)
        for row in range(line_count):
            if row == 1 and last_line is not None:
                painter.drawText(QRectF(0, top + self.fontMetrics().lineSpacing(), self.width(),
                                        self.fontMetrics().height()),
                                 Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter, last_line)
            else:
                layout.lineAt(row).draw(painter, QPointF(0, top))


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

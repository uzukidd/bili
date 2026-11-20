"""The mini player's clickable, time-synchronized lyric surface."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem


class MiniLyrics(QListWidget):
    coverRequested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("miniLyrics")
        self.setMinimumSize(240, 240)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setWordWrap(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._display = None
        self._line = -1

    def set_lyrics(self, display, placeholder):
        self._display = display
        self._line = -1
        self.clear()
        lines = [line.text for line in display.lines] if display and display.lines else [placeholder]
        for text in lines:
            item = QListWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.addItem(item)
        self.scrollToTop()

    def set_position(self, position):
        index = self._display.line_index(position) if self._display else -1
        if index == self._line:
            return
        self._line = index
        self.setCurrentRow(index)
        if index >= 0:
            self.scrollToItem(self.item(index), QAbstractItemView.ScrollHint.PositionAtCenter)
        else:
            self.scrollToTop()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.viewport().rect().contains(event.pos()):
            event.accept()
            self.coverRequested.emit()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            self.coverRequested.emit()
        else:
            event.ignore()

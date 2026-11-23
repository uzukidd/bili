"""Independent desktop lyrics window using the player's saved lyric cues."""
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget

from .i18n import translate


class FloatingLyrics(QWidget):
    closed = pyqtSignal()

    def __init__(self):
        # An independent tool window stays visible when the player is minimized or hidden.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(640, 126)
        self._display = None
        self._drag_offset = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QFrame()
        self.panel.setObjectName("floatingLyrics")
        self.panel.installEventFilter(self)
        outer.addWidget(self.panel)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(16, 9, 12, 13)
        layout.setSpacing(3)

        header = QHBoxLayout()
        self.caption = QLabel()
        self.caption.setObjectName("floatingLyricsCaption")
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(self.caption, 1)
        self.close_button = QToolButton()
        self.close_button.setObjectName("floatingLyricsClose")
        self.close_button.setText("×")
        self.close_button.setAutoRaise(True)
        self.close_button.setFixedSize(26, 26)
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.line_label = QLabel()
        self.line_label.setObjectName("floatingLyricsLine")
        self.line_label.setTextFormat(Qt.TextFormat.PlainText)
        self.line_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.line_label.setWordWrap(True)
        self.line_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.line_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.pages.addWidget(self.line_label)
        self.plain_lyrics = QTextEdit()
        self.plain_lyrics.setObjectName("floatingLyricsPlain")
        self.plain_lyrics.setReadOnly(True)
        self.plain_lyrics.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pages.addWidget(self.plain_lyrics)

    def set_language(self, language):
        title = translate("歌词悬浮窗", language)
        self.setWindowTitle(title)
        self.caption.setText(title)
        self.caption.setToolTip(translate("拖动窗口", language))
        close = translate("关闭歌词悬浮窗", language)
        self.close_button.setToolTip(close)
        self.close_button.setAccessibleName(close)

    def set_lyrics(self, display, placeholder):
        self._display = display
        self.line_label.setText(placeholder)
        if display and display.lines and not display.synced:
            self.plain_lyrics.setPlainText("\n".join(line.text for line in display.lines))
            self.plain_lyrics.verticalScrollBar().setValue(0)
            self.pages.setCurrentWidget(self.plain_lyrics)
        else:
            self.pages.setCurrentWidget(self.line_label)

    def set_position(self, position):
        if self._display is None or not self._display.synced:
            return
        index = self._display.line_index(position)
        text = self._display.lines[index].text if index >= 0 else ""
        if text != self.line_label.text():
            self.line_label.setText(text)

    def show_on_screen(self, screen):
        positioned = self.testAttribute(Qt.WidgetAttribute.WA_Moved)
        caption_center = self.frameGeometry().topLeft() + self.caption.geometry().center()
        on_screen = any(candidate.availableGeometry().contains(caption_center)
                        for candidate in QGuiApplication.screens())
        if screen is not None and (not positioned or not on_screen):
            area = screen.availableGeometry()
            self.resize(min(640, area.width()), self.height())
            self.move(area.x() + (area.width() - self.width()) // 2,
                      area.y() + max(0, area.height() - self.height() - 48))
        self.show()

    def closeEvent(self, event):
        self._drag_offset = None
        super().closeEvent(event)
        self.closed.emit()

    def eventFilter(self, watched, event):
        if watched is self.panel:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.pos()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                point = event.globalPosition().toPoint() - self._drag_offset
                self.move(point)
                return True
            if event.type() in (QEvent.Type.MouseButtonRelease, QEvent.Type.UngrabMouse):
                self._drag_offset = None
        return super().eventFilter(watched, event)

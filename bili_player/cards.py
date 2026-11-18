"""Cover-led music cards with hover playback and internal drag-to-swap."""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QDrag, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QFrame, QGridLayout, QLabel, QLayout,
    QPushButton, QSizePolicy, QWidget,
)

from .theme import PALETTES

ENTRY_MIME = "application/x-bili-music-library-entry"
CARD_EXTRA_HEIGHT = 72


class ElidedLabel(QLabel):
    """Keep a long title from changing the tile's width or height."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def resizeEvent(self, event):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, self.width()))
        super().resizeEvent(event)


class CardArtwork(QPushButton):
    """Square, cropped artwork with no caption over the image."""

    def __init__(self, theme: str = "light", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.pixmap = QPixmap()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("cardArtwork")
        self.setFlat(True)

    def set_pixmap(self, pixmap: QPixmap):
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 7, 7)
        painter.setClipPath(clip)
        p = PALETTES[self.theme]
        painter.fillRect(self.rect(), QColor(p["art"]))
        if not self.pixmap.isNull():
            image = self.pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((self.width() - image.width()) // 2,
                               (self.height() - image.height()) // 2, image)
        else:
            painter.setPen(QColor(p["accent"]))
            font = self.font()
            font.setPointSize(38)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "♫")
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(p["accent"]))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)


class HoverPlayButton(QPushButton):
    def __init__(self, parent=None, theme="light"):
        super().__init__(parent)
        self.theme = theme
        self.setFixedSize(62, 62)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("hoverPlay")
        self.setFlat(True)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 62, self.height() / 62)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 45))
        painter.drawEllipse(QRectF(3, 6, 56, 56))
        palette = PALETTES[self.theme]
        color = QColor(palette["accent"])
        if self.underMouse() or self.hasFocus():
            color = color.lighter(108)
        if self.isDown():
            color = QColor(palette["accent_hover"])
        painter.setBrush(color)
        painter.drawEllipse(QRectF(3, 2, 56, 56))
        painter.setBrush(QColor("#202635"))
        triangle = QPainterPath()
        triangle.moveTo(26, 19)
        triangle.lineTo(43, 30)
        triangle.lineTo(26, 41)
        triangle.closeSubpath()
        painter.drawPath(triangle)


class LibraryCard(QFrame):
    swapRequested = pyqtSignal(str, str)
    dragActiveChanged = pyqtSignal(bool)
    openRequested = pyqtSignal(str)
    playRequested = pyqtSignal(str)
    menuRequested = pyqtSignal(str, QPoint)
    selectionRequested = pyqtSignal(str, object)

    def __init__(self, entry_id: str, drag_group: str, drag_hint: str, parent=None):
        super().__init__(parent)
        self.entry_id = entry_id
        self.drag_group = drag_group
        self.setObjectName("card")
        self.setAcceptDrops(True)
        self.setAccessibleDescription(drag_hint)
        self.setProperty("dropTarget", False)
        self._press = None
        self._dragged = False
        self._drag_widgets = set()
        self._hovered = False
        self._keyboard_focus = False
        self._scale = 1.0
        self.artwork = None
        self.selection_mode = False
        self.selection_box = QCheckBox(self)
        self.selection_box.setFixedSize(26, 26)
        self.selection_box.hide()
        self.selection_box.clicked.connect(lambda: self.selectionRequested.emit(self.entry_id, Qt.KeyboardModifier.ControlModifier))
        self.title_label = None
        self.subtitle_label = None
        self.play_button = HoverPlayButton(self)
        self.play_button.hide()
        self.play_button.installEventFilter(self)
        self.play_button.clicked.connect(lambda: self.playRequested.emit(self.entry_id))
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(100)
        self._hover_timer.timeout.connect(self._refresh_hover)
        self.enable_drag(self)

    def set_content(self, artwork: CardArtwork, title: str, subtitle: str, play_hint: str):
        self.artwork = artwork
        self.play_button.theme = artwork.theme
        artwork.setParent(self)
        artwork.clicked.connect(lambda: self.selectionRequested.emit(self.entry_id, Qt.KeyboardModifier.ControlModifier)
                                if self.selection_mode else self.openRequested.emit(self.entry_id))
        self.title_label = ElidedLabel(title, self)
        self.title_label.setObjectName("cardTitle")
        self.subtitle_label = ElidedLabel(subtitle, self)
        self.subtitle_label.setObjectName("cardSubtitle")
        for widget in (artwork, self.title_label, self.subtitle_label):
            self.enable_drag(widget)
        self.play_button.setParent(artwork)
        self.play_button.setToolTip(play_hint)
        self.play_button.setAccessibleName(play_hint)
        self._layout_content()
        self._set_hovered(False)

    def enable_drag(self, widget):
        self._drag_widgets.add(widget)
        widget.installEventFilter(self)

    def set_scale(self, scale: float):
        self._scale = scale
        self.play_button.setFixedSize(round(62 * scale), round(62 * scale))
        if self.title_label is not None:
            self.title_label.setStyleSheet(f"font-size: {max(11, round(16 * scale))}px;")
            self.subtitle_label.setStyleSheet(f"font-size: {max(10, round(13 * scale))}px;")
        self._layout_content()

    def _layout_content(self):
        if self.artwork is None:
            return
        scale = self._scale
        padding = round(14 * scale)
        side = max(0, self.width() - 2 * padding)
        self.artwork.setGeometry(padding, padding, side, side)
        self.title_label.setGeometry(padding, padding + side + round(10 * scale), side, round(28 * scale))
        self.subtitle_label.setGeometry(padding, padding + side + round(42 * scale), side, round(22 * scale))
        inset = round(7 * scale)
        self.play_button.move(max(0, side - self.play_button.width() - inset),
                              max(0, side - self.play_button.height() - inset))
        self.play_button.raise_()
        self.selection_box.move(padding + 5, padding + 5)
        self.selection_box.raise_()

    def set_selection(self, enabled: bool, selected: bool):
        self.selection_mode = enabled
        self.selection_box.setVisible(enabled)
        self.selection_box.setChecked(selected)
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_content()

    def _set_hovered(self, hovered):
        self._hovered = hovered
        self.play_button.setVisible(hovered)
        if hovered and self.isVisible():
            if not self._hover_timer.isActive():
                self._hover_timer.start()
        else:
            self._hover_timer.stop()

    def hideEvent(self, event):
        self._keyboard_focus = False
        self._set_hovered(False)
        super().hideEvent(event)

    def enterEvent(self, event):
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._keyboard_focus:
            self._set_hovered(False)
        super().leaveEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ContextMenu:
            self.menuRequested.emit(self.entry_id, event.globalPos())
            event.accept()
            return True
        if event.type() == QEvent.Type.FocusIn:
            self._keyboard_focus = event.reason() in (
                Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                Qt.FocusReason.ShortcutFocusReason,
            )
            if self._keyboard_focus:
                self._set_hovered(True)
        if event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self._refresh_hover)
        if event.type() == QEvent.Type.MouseMove and not event.buttons() and not self._hovered:
            self._refresh_hover()
        if watched not in self._drag_widgets:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            modifiers = event.modifiers()
            if self.selection_mode or modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                self.selectionRequested.emit(self.entry_id, modifiers)
                self._press = None
                event.accept()
                return True
            self._press = event.globalPosition().toPoint()
            self._dragged = False
        elif event.type() == QEvent.Type.MouseMove and self._press is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                distance = (event.globalPosition().toPoint() - self._press).manhattanLength()
                if distance >= QApplication.startDragDistance():
                    self._press = None
                    self._dragged = True
                    if isinstance(watched, QAbstractButton):
                        watched.setDown(False)
                    self._start_drag()
                    return True
        elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self._press = None
            if self._dragged:
                self._dragged = False
                return True
        return super().eventFilter(watched, event)

    def _refresh_hover(self):
        if sip.isdeleted(self):
            return
        if not self.isVisible() or QApplication.activePopupWidget() is not None:
            self._set_hovered(False)
            return
        modal = QApplication.activeModalWidget()
        if modal is not None and modal is not self.window():
            self._set_hovered(False)
            return
        focus = QApplication.focusWidget()
        has_focus = focus is self or (focus is not None and self.isAncestorOf(focus))
        target = QApplication.widgetAt(QCursor.pos())
        has_pointer = target is self or (target is not None and self.isAncestorOf(target))
        self._set_hovered(has_pointer or (self._keyboard_focus and has_focus))

    def _start_drag(self):
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(ENTRY_MIME, self.entry_id.encode("utf-8"))
        drag.setMimeData(mime)
        preview = self.grab().scaled(160, 200, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        drag.setPixmap(preview)
        drag.setHotSpot(QPoint(preview.width() // 2, 18))
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.dragActiveChanged.emit(True)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            if not sip.isdeleted(drag):
                drag.deleteLater()
            if not sip.isdeleted(self):
                self.unsetCursor()
                self.dragActiveChanged.emit(False)
                self._refresh_hover()

    def _valid_source(self, event):
        source = event.source()
        if not isinstance(source, LibraryCard) or source.drag_group != self.drag_group:
            return None
        if not event.mimeData().hasFormat(ENTRY_MIME):
            return None
        try:
            identity = bytes(event.mimeData().data(ENTRY_MIME)).decode("utf-8")
        except UnicodeError:
            return None
        if identity != source.entry_id or identity == self.entry_id:
            return None
        return identity

    def _highlight(self, enabled):
        self.setProperty("dropTarget", enabled)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragEnterEvent(self, event):
        if self._valid_source(event):
            self._highlight(True)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._valid_source(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._highlight(False)
        event.accept()

    def dropEvent(self, event):
        source_id = self._valid_source(event)
        self._highlight(False)
        if source_id:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            def notify():
                if not sip.isdeleted(self):
                    self.swapRequested.emit(source_id, self.entry_id)
            QTimer.singleShot(0, notify)
        else:
            event.ignore()


class LibraryGrid(QWidget):
    scaleChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scale = 1.0
        self._wheel_remainder = 0
        self.setObjectName("cards")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 8, 12)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._cards = []
        self._empty = None
        self._columns = 0
        self._side = 0
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.timeout.connect(self.reflow)

    def set_scroll_area(self, area):
        area.viewport().installEventFilter(self)

    def set_scale(self, value: float):
        value = round(max(0.7, min(1.6, float(value))), 2)
        if value != self.scale:
            self.scale = value
            self._side = 0
            self.reflow()

    def _zoom_wheel(self, event):
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False
        delta = event.angleDelta().y() or event.pixelDelta().y() * 3
        self._wheel_remainder += delta
        steps = int(self._wheel_remainder / 120)
        self._wheel_remainder -= steps * 120
        if steps:
            previous = self.scale
            self.set_scale(self.scale + steps * 0.1)
            if self.scale != previous:
                self.scaleChanged.emit(self.scale)
        event.accept()
        return True

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and self._zoom_wheel(event):
            return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event):
        if not self._zoom_wheel(event):
            event.ignore()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(560, 350)

    def clear(self):
        self._cards = []
        self._empty = None
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._columns = 0
        self._side = 0
        self.setMinimumHeight(0)

    def show_cards(self, cards):
        self._cards = cards
        for card in cards:
            card.installEventFilter(self)
            for child in card.findChildren(QWidget):
                child.installEventFilter(self)
        self.reflow()

    def show_empty(self, widget):
        self._empty = widget
        self.grid.addWidget(widget, 0, 0)
        self.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_timer.start(0)

    def reflow(self):
        margins = self.grid.contentsMargins()
        width = max(0, self.width() - margins.left() - margins.right())
        if self._empty is not None:
            self._empty.setFixedWidth(width)
            self.setMinimumHeight(self._empty.sizeHint().height() + margins.bottom())
            return
        if not self._cards:
            return
        gap = self.grid.spacing()
        preferred = round(248 * self.scale)
        columns = max(1, (width + gap) // (preferred + gap))
        side = min(preferred, max(1, width))
        if (columns, side) == (self._columns, self._side):
            return
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(max(columns, self._columns)):
            self.grid.setColumnStretch(column, 0)
            self.grid.setColumnMinimumWidth(column, 0)
        self._columns, self._side = columns, side
        height = side + round(CARD_EXTRA_HEIGHT * self.scale)
        for index, card in enumerate(self._cards):
            card.set_scale(self.scale)
            card.setFixedSize(side, height)
            self.grid.addWidget(card, index // columns, index % columns)
        rows = (len(self._cards) + columns - 1) // columns
        self.setMinimumHeight(rows * height + max(0, rows - 1) * gap + margins.top() + margins.bottom())

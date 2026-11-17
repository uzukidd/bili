"""Palette-aware window controls and native frameless resizing.

自绘窗口按钮、标题拖动及原生无边框缩放。
"""
from __future__ import annotations

import weakref

from PyQt6.QtCore import QEvent, QObject, QPoint, QRectF, Qt
from PyQt6.QtGui import QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QAbstractButton, QApplication, QHBoxLayout, QLabel, QToolButton, QWidget,
)

from .i18n import translate

__all__ = ["TitleBar", "FramelessController", "install_frameless"]


class _WindowButton(QToolButton):
    def __init__(self, symbol: str, parent: QWidget):
        super().__init__(parent)
        self.symbol = symbol
        self.setFixedSize(36, 30)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        if self.underMouse() or self.isDown() or self.hasFocus():
            background = palette.color(QPalette.ColorRole.Highlight)
            background.setAlpha(65 if self.isDown() else 32)
            painter.fillRect(self.rect(), background)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(QPen(palette.color(group, QPalette.ColorRole.ButtonText), 1.2))
        center = self.rect().center()
        x, y = center.x() - 5, center.y() - 5
        if self.symbol == "minimize":
            painter.drawLine(x, y + 8, x + 10, y + 8)
        elif self.symbol == "maximize":
            painter.drawRect(QRectF(x, y, 10, 10))
        elif self.symbol == "restore":
            painter.drawPolyline(QPoint(x + 3, y + 2), QPoint(x + 3, y - 1),
                                 QPoint(x + 11, y - 1), QPoint(x + 11, y + 7),
                                 QPoint(x + 8, y + 7))
            painter.drawRect(QRectF(x, y + 2, 8, 8))
        else:
            painter.drawLine(x, y, x + 10, y + 10)
            painter.drawLine(x + 10, y, x, y + 10)


class TitleBar(QWidget):
    """Independent title bar; the host owns its layout and window flags.

    标题栏不设置无边框标志，由宿主负责根布局及窗口标志。
    """

    def __init__(self, window: QWidget, language: str = "zh_CN"):
        super().__init__(window)
        self._window = window
        self._language = language
        self._drag_offset: QPoint | None = None
        self._drag_start: QPoint | None = None
        self.setObjectName("windowTitleBar")
        self.setFixedHeight(38)
        self.title_label = QLabel(window.windowTitle() or "Bili Music", self)
        self.title_label.setObjectName("windowTitle")
        self.title_label.setMinimumWidth(0)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title_label.installEventFilter(self)
        self.minimize_button = _WindowButton("minimize", self)
        self.maximize_button = _WindowButton("maximize", self)
        self.close_button = _WindowButton("close", self)
        self.minimize_button.setObjectName("windowMinimize")
        self.maximize_button.setObjectName("windowMaximize")
        self.close_button.setObjectName("windowClose")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 8, 4)
        layout.setSpacing(0)
        layout.addWidget(self.title_label, 1)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)
        window.windowTitleChanged.connect(self._set_title)
        window.installEventFilter(self)
        self.set_language(language)

    def _set_title(self, title: str):
        self.title_label.setText(title or "Bili Music")

    def set_language(self, language: str):
        self._language = language
        self.setToolTip(translate("拖动窗口", language))
        self.title_label.setToolTip(self.toolTip())
        self.refresh_state()

    def refresh_state(self):
        maximized = self._window.isMaximized()
        self.maximize_button.symbol = "restore" if maximized else "maximize"
        for button, text in (
            (self.minimize_button, "最小化"),
            (self.maximize_button, "还原窗口" if maximized else "最大化"),
            (self.close_button, "关闭窗口"),
        ):
            label = translate(text, self._language)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.update()

    def toggle_maximized(self):
        self._drag_offset = self._drag_start = None
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.refresh_state()

    def eventFilter(self, watched, event):
        if watched is self._window and event.type() in (
            QEvent.Type.WindowStateChange, QEvent.Type.Show,
        ):
            self.refresh_state()
        elif watched is self.title_label and event.type() in (
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove,
        ):
            return self._handle_mouse(event)
        return super().eventFilter(watched, event)

    def _handle_mouse(self, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
        elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = self._drag_start = None
            handle = self._window.windowHandle()
            if handle is None or not handle.startSystemMove():
                self._drag_start = event.globalPosition().toPoint()
                self._drag_offset = self._drag_start - self._window.pos()
        elif kind == QEvent.Type.MouseMove and self._drag_offset is not None:
            if not event.buttons() & Qt.MouseButton.LeftButton:
                self._drag_offset = self._drag_start = None
                return False
            position = event.globalPosition().toPoint()
            if self._window.isMaximized():
                if (position - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                    return True
                fraction = self._drag_offset.x() / max(1, self._window.width())
                self._window.showNormal()
                self._drag_offset = QPoint(round(self._window.width() * fraction), self._drag_offset.y())
            self._window.move(position - self._drag_offset)
        elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = self._drag_start = None
        else:
            return False
        event.accept()
        return True

    def mousePressEvent(self, event):
        if not self._handle_mouse(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._handle_mouse(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if not self._handle_mouse(event):
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if not self._handle_mouse(event):
            super().mouseDoubleClickEvent(event)


class FramelessController(QObject):
    """Install native edge resizing for one window and its child widgets.

    为单个窗口及其子控件提供原生边缘缩放，窗口销毁时自动移除过滤器。
    """

    BORDER_WIDTH = 8

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window_ref = weakref.ref(window)
        self._app = QApplication.instance()
        self._disposed = False
        self._cursor_widget = None
        self._cursor_before = None
        self._tracking = weakref.WeakKeyDictionary()
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        window._bili_frameless_controller = self
        self._track(window)
        for child in window.findChildren(QWidget):
            self._track(child)
        if self._app is not None:
            self._app.installEventFilter(self)
        window.destroyed.connect(self.dispose)

    def _track(self, widget):
        if widget not in self._tracking:
            self._tracking[widget] = (widget.hasMouseTracking(), widget.testAttribute(Qt.WidgetAttribute.WA_Hover))
            widget.setMouseTracking(True)
            widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _clear_cursor(self):
        widget = self._cursor_widget() if self._cursor_widget is not None else None
        self._cursor_widget = None
        if widget is not None:
            try:
                if self._cursor_before is None:
                    widget.unsetCursor()
                else:
                    widget.setCursor(self._cursor_before)
            except RuntimeError:
                pass  # The hovered child can be deleted before the next move.
        self._cursor_before = None

    def dispose(self):
        """Remove the application filter, also called on window destruction.

        移除应用过滤器；窗口销毁时亦自动调用。
        """
        if self._disposed:
            return
        self._disposed = True
        if self._app is not None:
            self._app.removeEventFilter(self)
        self._clear_cursor()
        for widget, (tracking, hover) in list(self._tracking.items()):
            try:
                widget.setMouseTracking(tracking)
                widget.setAttribute(Qt.WidgetAttribute.WA_Hover, hover)
            except RuntimeError:
                pass
        self._tracking.clear()

    def _edges_at(self, window, position):
        edges = Qt.Edge(0)
        if not window.rect().contains(position):
            return edges
        border = self.BORDER_WIDTH
        if position.x() < border:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= window.width() - border:
            edges |= Qt.Edge.RightEdge
        if position.y() < border:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= window.height() - border:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _is_button(widget, window):
        while widget is not None and widget is not window:
            if isinstance(widget, QAbstractButton):
                return True
            widget = widget.parentWidget()
        return False

    def eventFilter(self, watched, event):
        if self._disposed or not isinstance(watched, QWidget):
            return False
        window = self._window_ref()
        if window is None or watched.window() is not window:
            return False
        kind = event.type()
        if kind in (QEvent.Type.Show, QEvent.Type.Polish, QEvent.Type.Enter):
            self._track(watched)
        if kind == QEvent.Type.WindowStateChange and watched is window:
            self._clear_cursor()
        if kind == QEvent.Type.Leave:
            if self._cursor_widget is not None and self._cursor_widget() is watched:
                self._clear_cursor()
            return False
        if kind not in (QEvent.Type.MouseMove, QEvent.Type.HoverMove, QEvent.Type.MouseButtonPress):
            return False
        if window.isMaximized() or window.isFullScreen() or self._is_button(watched, window):
            self._clear_cursor()
            return False
        position = window.mapFromGlobal(watched.mapToGlobal(event.position().toPoint()))
        edges = self._edges_at(window, position)
        self._clear_cursor()
        if not edges:
            return False
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            diagonal = bool(edges & Qt.Edge.LeftEdge) == bool(edges & Qt.Edge.TopEdge)
            cursor = Qt.CursorShape.SizeFDiagCursor if diagonal else Qt.CursorShape.SizeBDiagCursor
        else:
            cursor = Qt.CursorShape.SizeHorCursor if horizontal else Qt.CursorShape.SizeVerCursor
        self._cursor_widget = weakref.ref(watched)
        self._cursor_before = watched.cursor() if watched.testAttribute(Qt.WidgetAttribute.WA_SetCursor) else None
        watched.setCursor(cursor)
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            handle = window.windowHandle()
            if handle is not None and handle.startSystemResize(edges):
                event.accept()
                return True
        return False


def install_frameless(window: QWidget) -> FramelessController:
    """Return the existing controller, or install one.

    返回现有控制器；首次调用时创建。
    """
    controller = getattr(window, "_bili_frameless_controller", None)
    if not isinstance(controller, FramelessController) or controller._disposed:
        controller = FramelessController(window)
    return controller

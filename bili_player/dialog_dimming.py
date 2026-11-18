"""Dim the owning window for the lifetime of a visible Qt dialog."""
from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QDialog, QWidget


class _DimLayer(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.setObjectName("dialogDimLayer")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        host.installEventFilter(self)
        self.setGeometry(host.rect())

    def eventFilter(self, host, event):
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(host.rect())
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(0, 0, 0, 108))


class _DialogDimming(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._hosts = {}
        self._layers = {}
        app.installEventFilter(self)

    def eventFilter(self, dialog, event):
        kind = event.type()
        if kind not in (QEvent.Type.Show, QEvent.Type.Hide) or not isinstance(dialog, QDialog):
            return False
        if kind == QEvent.Type.Hide:
            self._remove(id(dialog))
            return False
        parent = dialog.parentWidget()
        if parent is None:
            return False
        host = parent.window()
        if host is dialog:
            return False
        key = id(dialog)
        self._hosts[key] = (dialog, host)
        if not getattr(dialog, "_dim_cleanup_connected", False):
            dialog.destroyed.connect(partial(self._remove, key))
            dialog._dim_cleanup_connected = True
        layer = self._layers.get(host)
        if layer is None or sip.isdeleted(layer):
            layer = self._layers[host] = _DimLayer(host)
        layer.show()
        layer.raise_()
        return False

    def _remove(self, key, *_):
        pair = self._hosts.pop(key, None)
        if pair is None:
            return
        _, host = pair
        if any(other_host is host for _, other_host in self._hosts.values()):
            return
        layer = self._layers.pop(host, None)
        if layer is not None and not sip.isdeleted(layer):
            layer.hide()
            layer.deleteLater()


def install_dialog_dimming():
    app = QApplication.instance()
    if not hasattr(app, "_bili_dialog_dimming"):
        app._bili_dialog_dimming = _DialogDimming(app)

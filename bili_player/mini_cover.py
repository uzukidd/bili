"""Cover rendering shared by the mini player and its settings previews."""
from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QPushButton

from .models import DEFAULT_MINI_COVER_STYLE
from .theme import PALETTES


def _draw_cover(painter, rect, pixmap, palette, circular=False, angle=0):
    painter.save()
    clip = QPainterPath()
    if circular:
        clip.addEllipse(rect)
    else:
        clip.addRoundedRect(rect, 9, 9)
    painter.setClipPath(clip)
    painter.fillRect(rect, QColor(palette["art"]))
    if pixmap.isNull():
        font = painter.font()
        font.setPixelSize(max(12, round(rect.height() / 5)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(palette["accent"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♫")
    else:
        scale = min(pixmap.width() / rect.width(), pixmap.height() / rect.height())
        width, height = rect.width() * scale, rect.height() * scale
        source = QRectF((pixmap.width() - width) / 2, (pixmap.height() - height) / 2, width, height)
        center = rect.center()
        painter.translate(center)
        painter.rotate(angle)
        painter.drawPixmap(rect.translated(-center), pixmap, source)
    painter.restore()


def _draw_record(painter, area, pixmap, palette, angle=0):
    diameter = min(area.width(), area.height())
    center = area.center()
    radius = diameter / 2
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#171923"))
    painter.drawEllipse(center, radius, radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#3a3d4a"), 1))
    for fraction in (0.89, 0.93, 0.97):
        groove_radius = radius * fraction
        painter.drawEllipse(center, groove_radius, groove_radius)
    inset = diameter * 0.065
    image = QRectF(center.x() - radius + inset, center.y() - radius + inset,
                   diameter - 2 * inset, diameter - 2 * inset)
    _draw_cover(painter, image, pixmap, palette, circular=True, angle=angle)
    painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
    painter.setBrush(QColor("#202330"))
    painter.drawEllipse(center, diameter * 0.026, diameter * 0.026)


class CoverButton(QPushButton):
    """Clickable artwork. A visible record spins only while playback is active."""

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.pixmap = QPixmap()
        self.cover_style = DEFAULT_MINI_COVER_STYLE
        self._playing = False
        self._angle = 0.0
        self._clock = QElapsedTimer()
        self._rotation_timer = QTimer(self)
        self._rotation_timer.setInterval(25)
        self._rotation_timer.timeout.connect(self.update)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def angle(self):
        if self._rotation_timer.isActive():
            return (self._angle + self._clock.elapsed() * 0.024) % 360
        return self._angle

    def set_pixmap(self, pixmap):
        self.pixmap = pixmap
        self.update()

    def set_cover_style(self, style):
        self.cover_style = style
        self._sync_rotation()
        self.update()

    def set_playing(self, playing):
        self._playing = playing
        self._sync_rotation()

    def _sync_rotation(self):
        if self._playing and self.cover_style == "record" and self.isVisible():
            if not self._rotation_timer.isActive():
                self._clock.start()
                self._rotation_timer.start()
        else:
            self._stop_rotation()

    def _stop_rotation(self):
        if self._rotation_timer.isActive():
            self._angle = self.angle
            self._rotation_timer.stop()
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_rotation()

    def hideEvent(self, event):
        self._stop_rotation()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        palette = PALETTES[self.theme]
        if self.cover_style == "record":
            _draw_record(painter, rect.adjusted(6, 6, -6, -6), self.pixmap, palette, self.angle)
        else:
            _draw_cover(painter, rect, self.pixmap, palette)
        if self.hasFocus():
            painter.setPen(QColor(palette["accent"]))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)


def mini_cover_preview(style, theme, pixel_ratio):
    """Draw a compact static miniature of each style with the same sample artwork."""
    sample = QPixmap(160, 160)
    painter = QPainter(sample)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 160, 160)
    gradient.setColorAt(0, QColor("#705bb6"))
    gradient.setColorAt(1, QColor("#e382a6"))
    painter.fillRect(sample.rect(), gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ffcfaa"))
    painter.drawEllipse(QPointF(112, 42), 28, 28)
    hills = QPainterPath(QPointF(-5, 132))
    hills.cubicTo(60, 28, 101, 159, 164, 77)
    hills.lineTo(164, 165)
    hills.lineTo(-5, 165)
    painter.fillPath(hills, QColor("#38335c"))
    painter.end()

    preview = QPixmap(round(128 * pixel_ratio), round(112 * pixel_ratio))
    preview.setDevicePixelRatio(pixel_ratio)
    preview.fill(Qt.GlobalColor.transparent)
    painter = QPainter(preview)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    palette = PALETTES[theme]
    painter.setPen(QColor(palette["line"]))
    painter.setBrush(QColor(palette["bg"]))
    painter.drawRoundedRect(QRectF(16, 1, 96, 110), 9, 9)
    art = QRectF(26, 9, 76, 57)
    if style == "record":
        _draw_record(painter, art, sample, palette)
    else:
        _draw_cover(painter, art, sample, palette)
    painter.setPen(QPen(QColor(palette["muted"]), 2))
    painter.drawLine(27, 74, 70, 74)
    painter.setPen(QPen(QColor(palette["line"]), 2))
    painter.drawLine(27, 84, 101, 84)
    painter.setPen(QPen(QColor(palette["accent"]), 2))
    painter.drawLine(27, 84, 58, 84)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(palette["accent"]))
    painter.drawEllipse(QPointF(64, 97), 7, 7)
    triangle = QPainterPath(QPointF(62, 93))
    triangle.lineTo(68, 97)
    triangle.lineTo(62, 101)
    triangle.closeSubpath()
    painter.fillPath(triangle, QColor("#202635"))
    painter.end()
    return preview

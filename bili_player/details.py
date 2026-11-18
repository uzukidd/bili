"""Scrollable entry details and explicit per-track actions."""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QMouseEvent, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget, QStyledItemDelegate,
)

from .i18n import translate
from .models import LibraryEntry, Track


def _duration(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


class DetailCover(QLabel):
    """Square artwork with rounded corners and center cropping."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.setFixedSize(212, 212)

    def set_pixmap(self, pixmap: QPixmap):
        self._pixmap = QPixmap(pixmap)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        target = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(target, 16, 16)
        painter.setClipPath(path)
        painter.fillRect(target, self.palette().alternateBase())
        if self._pixmap.isNull():
            painter.setPen(self.palette().placeholderText().color())
            font = painter.font()
            font.setPixelSize(56)
            painter.setFont(font)
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter, "♪")
            return
        width, height = self._pixmap.width(), self._pixmap.height()
        side = min(width, height)
        painter.drawPixmap(target, self._pixmap, QRectF((width - side) / 2, (height - side) / 2, side, side))


class TrackDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""


class TrackRow(QFrame):
    """Buttons act on a song; double-clicking its text opens its details."""

    def __init__(self, details, item, track, index, title, metadata):
        super().__init__()
        self.details, self.item, self.track = details, item, track
        self.setObjectName("detailTrack")
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        self.title_label = details._label(f"{index}.  {title}", "section")
        self.title_label.setWordWrap(False)
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.title_label.setToolTip(title)
        self.metadata_label = details._label(metadata, "muted")
        self.metadata_label.setMinimumWidth(0)
        layout.addWidget(self.title_label)
        layout.addWidget(self.metadata_label)
        actions = QHBoxLayout()
        self.play_button = details._button("播放", lambda: details._play_track(item))
        self.play_button.setObjectName("primary")
        self.insert_button = details._button("插入队列", lambda: details._insert_track(item))
        self.insert_button.setToolTip(details.tr("插入当前歌曲之后，不打断播放"))
        for widget in (self.play_button, self.insert_button):
            widget.setFixedHeight(30)
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        for widget in (self, self.title_label, self.metadata_label):
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            self.details.tracks_list.itemDoubleClicked.emit(self.item)
            event.accept()
            return True
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease) and event.button() == Qt.MouseButton.LeftButton:
            # Item widgets consume clicks before QListWidget can apply Ctrl/Shift selection.
            viewport = self.details.tracks_list.viewport()
            position = viewport.mapFromGlobal(event.globalPosition().toPoint())
            forwarded = QMouseEvent(
                event.type(), QPointF(position), event.globalPosition(), event.button(),
                event.buttons(), event.modifiers(),
            )
            QApplication.sendEvent(viewport, forwarded)
            event.accept()
            return True
        return super().eventFilter(watched, event)


class EntryDetailsDialog(QDialog):
    """One outer scroll keeps the album hero and track rows moving together."""

    def __init__(self, parent, entry: LibraryEntry, language=None, *, album_id=None, track_id=None):
        super().__init__(parent)
        self._owner = parent
        self.entry = entry
        self._album_id, self._track_id = album_id, track_id
        self.language = language or getattr(parent, "language", "zh_CN") or "zh_CN"
        self._cover_source = None
        self.track_rows = []
        self.setObjectName("root")
        self.setMinimumSize(640, 500)
        self.resize(780, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().setSingleStep(24)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.scroll_area.viewport().installEventFilter(self)
        self.content = QWidget()
        self.content.setObjectName("detailContent")
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 8, 12)
        content_layout.setSpacing(16)
        self.hero = QFrame()
        self.hero.setObjectName("queuePanel")
        self.hero.setFixedHeight(256)
        self.hero_layout = QHBoxLayout(self.hero)
        self.hero_layout.setContentsMargins(18, 18, 18, 18)
        self.hero_layout.setSpacing(24)
        self.cover_slot = QWidget()
        self.cover_slot.setFixedWidth(212)
        cover_layout = QVBoxLayout(self.cover_slot)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        self.cover_widget = DetailCover(self.hero)
        cover_layout.addWidget(self.cover_widget, 0, Qt.AlignmentFlag.AlignCenter)
        self.cover_opacity = QGraphicsOpacityEffect(self.cover_widget)
        self.cover_widget.setGraphicsEffect(self.cover_opacity)
        self.hero_layout.addWidget(self.cover_slot)
        self.information_widget = QWidget()
        information = QVBoxLayout(self.information_widget)
        information.setContentsMargins(0, 0, 0, 0)
        information.addStretch()
        self.title_label = self._label(name="heading")
        self.title_label.setWordWrap(True)
        information.addWidget(self.title_label)
        self.meta_label = self._label(name="muted")
        self.meta_label.setWordWrap(True)
        information.addWidget(self.meta_label)
        self.artist_label = self._label(name="muted")
        self.artist_label.setWordWrap(True)
        information.addWidget(self.artist_label)
        self.date_label = self._label(name="muted")
        self.date_label.setWordWrap(True)
        information.addWidget(self.date_label)
        self.source_label = self._label(name="muted")
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        information.addWidget(self.source_label)
        information.addStretch()
        self.information_opacity = QGraphicsOpacityEffect(self.information_widget)
        self.information_widget.setGraphicsEffect(self.information_opacity)
        self.hero_layout.addWidget(self.information_widget, 1)
        content_layout.addWidget(self.hero)
        self.track_heading = QWidget()
        heading = QHBoxLayout(self.track_heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(self._label(self.tr("歌曲"), "section"))
        heading.addStretch()
        heading.addWidget(self._label(self.tr("双击打开单曲详情"), "muted"))
        content_layout.addWidget(self.track_heading)
        self.selection_toolbar = QWidget()
        selection = QHBoxLayout(self.selection_toolbar)
        selection.setContentsMargins(0, 0, 0, 0)
        self.select_all_button = self._button("全选", lambda: self.tracks_list.selectAll())
        self.clear_selection_button = self._button("清除选择", lambda: self.tracks_list.clearSelection())
        self.selection_label = self._label(name="muted")
        for widget in (self.select_all_button, self.clear_selection_button, self.selection_label):
            selection.addWidget(widget)
        selection.addStretch()
        content_layout.addWidget(self.selection_toolbar)
        self.batch_toolbar = QWidget()
        batch = QHBoxLayout(self.batch_toolbar)
        batch.setContentsMargins(0, 0, 0, 0)
        self.batch_queue_button = self._button("添加所选到队列", lambda: self._batch_action("batch_queue_tracks"))
        self.batch_copy_button = self._button("复制所选", lambda: self._batch_action("batch_copy_tracks"))
        self.batch_remove_button = self._button("从专辑移除", lambda: self._batch_action("batch_remove_album_tracks"))
        for widget in (self.batch_queue_button, self.batch_copy_button, self.batch_remove_button):
            batch.addWidget(widget)
        batch.addStretch()
        content_layout.addWidget(self.batch_toolbar)
        self.tracks_list = QListWidget()
        self.tracks_list.setObjectName("detailTracks")
        self.tracks_list.setItemDelegate(TrackDelegate(self.tracks_list))
        self.tracks_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tracks_list.itemSelectionChanged.connect(self._update_selection_controls)
        self.tracks_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.tracks_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tracks_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tracks_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tracks_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tracks_list.itemDoubleClicked.connect(self._open_track)
        self.tracks_list.viewport().installEventFilter(self)
        content_layout.addWidget(self.tracks_list)
        self.empty_label = self._label(self.tr("这个专辑还没有歌曲，先添加几首吧。"), "muted")
        self.empty_label.setWordWrap(True)
        content_layout.addWidget(self.empty_label)
        content_layout.addStretch()
        self.scroll_area.setWidget(self.content)
        layout.addWidget(self.scroll_area, 1)
        self.sticky_header = QFrame(self.scroll_area.viewport())
        self.sticky_header.setObjectName("detailSticky")
        sticky_layout = QHBoxLayout(self.sticky_header)
        sticky_layout.setContentsMargins(18, 8, 18, 8)
        self.sticky_title = self._label(name="section")
        sticky_layout.addWidget(self.sticky_title)
        self.sticky_header.hide()
        actions = QHBoxLayout()
        self.play_button = self._button("播放", lambda: self._resolve(True))
        self.play_button.setObjectName("primary")
        self.queue_button = self._button("添加到队列", lambda: self._resolve(False))
        self.edit_button = self._button("编辑", self._edit)
        self.copy_button = self._button("复制", self._copy)
        self.close_button = self._button("关闭", self.accept)
        for widget in (self.play_button, self.queue_button, self.edit_button, self.copy_button):
            actions.addWidget(widget)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.refresh()
        changed = getattr(parent, "libraryChanged", None)
        if changed is not None:
            changed.connect(self.refresh)

    def tr(self, text: str, **values) -> str:
        return translate(text, language=self.language, **values)

    @staticmethod
    def _label(text: str = "", name: str = "") -> QLabel:
        widget = QLabel(text)
        widget.setObjectName(name)
        widget.setTextFormat(Qt.TextFormat.PlainText)
        return widget

    def _button(self, text, callback):
        widget = QPushButton(self.tr(text))
        widget.setAutoDefault(False)
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.clicked.connect(callback)
        return widget

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            bar = self.scroll_area.verticalScrollBar()
            pixels = event.pixelDelta().y()
            movement = pixels if pixels else event.angleDelta().y() / 120 * bar.singleStep() * 3
            bar.setValue(bar.value() - round(movement))
            event.accept()
            return True
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize and hasattr(self, "sticky_header"):
            self._position_sticky()
        return super().eventFilter(watched, event)

    def _position_sticky(self):
        self.sticky_header.setGeometry(0, 0, self.scroll_area.viewport().width(), 54)
        self.sticky_header.raise_()

    def _on_scroll(self, value):
        if not hasattr(self, "sticky_header"):
            return
        if self.entry.kind == "single":
            self.cover_widget.setFixedSize(280, 280)
            self.cover_opacity.setOpacity(1)
            self.information_opacity.setOpacity(1)
            self.sticky_header.hide()
            return
        # The hero's layout height remains fixed; scrolling cannot change its own range.
        progress = min(1.0, max(0.0, value / 202))
        self.cover_widget.setFixedSize(max(32, round(212 * (1 - progress))), max(32, round(212 * (1 - progress))))
        self.cover_opacity.setOpacity(1 - progress)
        self.information_opacity.setOpacity(1 - progress)
        self.sticky_title.setText(self.entry.title)
        self.sticky_header.setVisible(value >= 170)
        self._position_sticky()

    def _current_entry(self):
        find = getattr(self._owner, "_find", None)
        if self._album_id is not None:
            album = find(self._album_id) if callable(find) else None
            track = next((t for t in album.tracks if t.id == self._track_id), None) if album else None
            lookup = getattr(self._owner, "song_for_track", None)
            if callable(lookup):
                # The song survives removing its reference from the originating folder.
                track = track or next((t for t in self.entry.tracks if t.id == self._track_id), None)
                current = lookup(track, album) if track is not None else None
            else:
                current = LibraryEntry(track.title, track.bvid, page=track.page, id=track.id,
                                       tracks=[track], cover=album.cover, resolved=True,
                                       artist=track.artist, date=track.date) if track else None
        else:
            current = find(self.entry.id) if callable(find) else self.entry
        if current is None:
            for widget in (self.play_button, self.queue_button, self.edit_button, self.copy_button,
                           self.batch_queue_button, self.batch_copy_button, self.batch_remove_button,
                           self.select_all_button, self.clear_selection_button):
                widget.setEnabled(False)
            self.reject()
            return None
        self.entry = current
        return current

    def refresh(self):
        entry = self._current_entry()
        if entry is None:
            return
        position = self.scroll_area.verticalScrollBar().value()
        selected_ids = set(self.selected_song_ids())
        self.setWindowTitle(self.tr("专辑详情" if entry.kind == "album" else "单曲详情"))
        self.title_label.setText(entry.title or (entry.tracks[0].title if entry.tracks else entry.bvid))
        self.sticky_title.setText(self.title_label.text())
        is_single = entry.kind == "single"
        self.hero.setFixedHeight(340 if is_single else 256)
        self.cover_slot.setFixedWidth(280 if is_single else 212)
        self.meta_label.setText(self.tr(
            "单曲 · {duration}" if is_single else "专辑 · {count} 首 · {duration}",
            count=len(entry.tracks), duration=_duration(sum(track.duration for track in entry.tracks)),
        ))
        artists = list(dict.fromkeys(track.artist for track in entry.tracks if track.artist.strip()))
        if is_single:
            author = entry.artist or (entry.tracks[0].artist if entry.tracks else "")
            date = entry.date or (entry.tracks[0].date if entry.tracks else "")
            self.artist_label.setText(self.tr("作者：{author}", author=author or self.tr("未填写")))
            self.artist_label.show()
            self.date_label.setText(self.tr("日期：{date}", date=date or self.tr("未填写")))
            self.date_label.show()
        else:
            self.artist_label.setText(" · ".join(artists))
            self.artist_label.setVisible(bool(artists))
            self.date_label.hide()
        self.source_label.setText(self.tr("来源：{bvid}", bvid=entry.bvid) if entry.bvid else "")
        self.source_label.setVisible(bool(entry.bvid))
        self.tracks_list.clear()
        self.track_rows = []
        for index, track in enumerate(entry.tracks if not is_single else [], 1):
            title = track.title or (entry.title if entry.kind == "single" else track.bvid)
            metadata = " · ".join(part for part in (track.artist.strip(), _duration(track.duration)) if part)
            item = QListWidgetItem(f"{index}.  {title}\n{metadata}")
            item.setData(Qt.ItemDataRole.UserRole, track)
            item.setSizeHint(QSize(0, 114))
            self.tracks_list.addItem(item)
            item.setSelected((track.song_id or track.id) in selected_ids)
            row = TrackRow(self, item, track, index, title, metadata)
            self.tracks_list.setItemWidget(item, row)
            row.installEventFilter(self)
            for child in row.findChildren(QWidget):
                child.installEventFilter(self)
            self.track_rows.append(row)
        self.tracks_list.setFixedHeight(max(1, len(entry.tracks) * 114 + 2))
        self.tracks_list.setVisible(not is_single and bool(entry.tracks))
        self.track_heading.setVisible(not is_single)
        self.selection_toolbar.setVisible(not is_single)
        self.batch_toolbar.setVisible(not is_single)
        self._update_selection_controls()
        self.empty_label.setVisible(not is_single and not entry.tracks)
        self.copy_button.setEnabled(True)
        self.edit_button.setEnabled(True)
        self.play_button.setEnabled(bool(entry.tracks))
        self.queue_button.setEnabled(bool(entry.tracks))
        if entry.cover != self._cover_source:
            self._cover_source = entry.cover
            self.cover_widget._requested_cover = entry.cover
            self.cover_widget.set_pixmap(QPixmap())
            if entry.cover:
                self._owner._load_cover(entry.cover, self.cover_widget)
        self.content.layout().activate()
        self.scroll_area.verticalScrollBar().setValue(position)
        self._on_scroll(self.scroll_area.verticalScrollBar().value())

    def selected_song_ids(self):
        """Return selected song identities in album order, independent of click order."""
        identities = []
        for row in range(self.tracks_list.count()):
            item = self.tracks_list.item(row)
            if item.isSelected():
                track = item.data(Qt.ItemDataRole.UserRole)
                identity = track.song_id or track.id
                if identity not in identities:
                    identities.append(identity)
        return identities

    def _update_selection_controls(self):
        count = len(self.tracks_list.selectedItems())
        self.selection_label.setText(self.tr("已选择 {count} 首", count=count))
        self.select_all_button.setEnabled(self.tracks_list.count() > count)
        self.clear_selection_button.setEnabled(bool(count))
        for button in (self.batch_queue_button, self.batch_copy_button, self.batch_remove_button):
            button.setEnabled(bool(count))

    def _batch_action(self, method):
        selected = self.selected_song_ids()
        entry = self._current_entry()
        if entry is None or entry.kind != "album":
            return
        current_ids = {track.song_id or track.id for track in entry.tracks}
        identities = [identity for identity in selected if identity in current_ids]
        if not identities:
            self.refresh()
            return
        getattr(self._owner, method)(entry.id, identities)
        if not sip.isdeleted(self):
            self.refresh()

    def _copy(self):
        entry = self._current_entry()
        if entry is not None:
            self._owner.copy_library_entries([entry.id])
            if not sip.isdeleted(self):
                self.refresh()

    def _resolve(self, play):
        if self._current_entry() is not None:
            self._owner.resolve_entry(self.entry, play)

    def _edit(self):
        if self._current_entry() is None:
            return
        if self.entry.kind == "album":
            self._owner.open_album(self.entry)
        else:
            self._owner.edit_single(self.entry)
        if not sip.isdeleted(self):
            self.refresh()

    def _track(self, item):
        if item is None:
            return None
        saved = item.data(Qt.ItemDataRole.UserRole)
        identity = saved.song_id or saved.id
        current = self._current_entry()
        return next((track for track in current.tracks if (track.song_id or track.id) == identity), None) if current else None

    def _play_track(self, item):
        track = self._track(item)
        if track is None:
            return
        self._owner._append_tracks([replace(track, id=str(uuid4()))])
        queue = self._owner.queue
        self._owner.play_item(queue.item(queue.count() - 1))

    def _insert_track(self, item):
        track = self._track(item)
        if track is not None:
            self._owner.insert_track_next(track)

    def _open_track(self, item):
        track = self._track(item)
        if track is None or self.entry.kind == "single":
            return
        self._owner.open_track_details(track, self.entry)
        if not sip.isdeleted(self):
            self.refresh()

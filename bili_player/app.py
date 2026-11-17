from __future__ import annotations

import sys
import weakref
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import (
    QObject, QRunnable, QLibraryInfo, QLocale, QRectF, QStandardPaths, QThreadPool,
    QTimer, QTranslator, Qt, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QSplitter, QTabBar, QVBoxLayout, QWidget,
)

from .api import BilibiliClient
from .covers import CoverCache
from .dialogs import AlbumDialog, EntryDialog
from .i18n import translate, translate_error
from .models import LibraryEntry, Store, Track
from .stream import StreamProxy
from .theme import PALETTES, apply_theme, stylesheet

STYLE = stylesheet("light")


class Signals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()


class Task(QRunnable):
    def __init__(self, fn: Callable):
        super().__init__()
        self.fn = fn
        self.signals = Signals()

    def run(self):
        try:
            self.signals.result.emit(self.fn())
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


def label(text: str = "", name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    return widget


def button(text: str, fn: Callable, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(fn)
    return widget


def timestamp(ms: int) -> str:
    seconds = max(0, ms // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def copy_tracks(tracks: list[Track]) -> list[Track]:
    """An album, its source single, and each queue item own separate identities."""
    return [replace(track, id=str(uuid4())) for track in tracks]


def media_icon(kind: str, theme: str, primary: bool = False) -> QIcon:
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#202635" if primary else PALETTES[theme]["text"])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    if kind == "pause":
        painter.drawRoundedRect(5, 4, 4, 14, 1, 1)
        painter.drawRoundedRect(13, 4, 4, 14, 1, 1)
    else:
        path = QPainterPath()
        if kind == "play":
            path.moveTo(6, 3)
            path.lineTo(18, 11)
            path.lineTo(6, 19)
            path.closeSubpath()
        else:
            if kind == "next":
                painter.translate(22, 0)
                painter.scale(-1, 1)
            painter.drawRect(3, 4, 2, 14)
            for x in (11, 18):
                path.moveTo(x, 4)
                path.lineTo(x - 7, 11)
                path.lineTo(x, 18)
                path.closeSubpath()
        painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class CoverButton(QPushButton):
    def __init__(self, caption: str, theme: str, parent=None):
        super().__init__(parent)
        self.caption = caption
        self.theme = theme
        self.pixmap = QPixmap()
        self.setFixedHeight(130)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(caption)

    def set_pixmap(self, pixmap: QPixmap):
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, _event):
        p = PALETTES[self.theme]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, 9, 9)
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor(p["art"]))
        if not self.pixmap.isNull():
            image = self.pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((self.width() - image.width()) // 2,
                               (self.height() - image.height()) // 2, image)
            painter.fillRect(0, self.height() - 33, self.width(), 33, QColor(0, 0, 0, 125))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(self.rect().adjusted(12, 0, -12, -7),
                             Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, self.caption)
        else:
            painter.setPen(QColor(p["accent"]))
            font = QFont(self.font())
            font.setPointSize(18)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "♫  " + self.caption)
        if self.hasFocus():
            painter.setPen(QColor(p["accent"]))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)


class MainWindow(QMainWindow):
    def __init__(self, store_path: Path | None = None):
        super().__init__()
        self.resize(1220, 820)
        self.setMinimumSize(1020, 700)
        if store_path is None:
            store_path = Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation,
            )) / "library.json"
        self.store = Store(store_path)
        self.entries: list[LibraryEntry] = []
        tracks: list[Track] = []
        volume, load_error = 70, ""
        try:
            self.entries, tracks, volume = self.store.load()
        except (ValueError, OSError) as exc:
            load_error = str(exc)
        self.theme = self.store.settings["theme"]
        self.language = self.store.settings["language"]
        self._bindings = []
        self._status_value = ("准备就绪 · 添加 BV 号，开始收集你的音乐", {})
        self._qt_translator = QTranslator(self)
        self.current_id: str | None = None
        self._request = 0
        self._library_request = 0
        self._queue_epoch = 0
        self._closing = False
        self._tasks: set[Task] = set()
        self._save_error_shown = False
        self._cover_pixmaps: dict[str, QPixmap] = {}
        self._cover_waiters: dict[str, list] = {}
        self._editing_albums: set[str] = set()
        self._deferred_album_additions: dict[str, list] = {}
        self.covers = CoverCache(self.store.path.parent / "covers")
        self.pool = QThreadPool.globalInstance()
        self.proxy = StreamProxy()
        self.audio = QAudioOutput(self)
        self.audio.setVolume(volume / 100)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        apply_theme(QApplication.instance(), self.theme)
        self._build(volume)
        self._append_tracks(tracks, persist=False)
        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(self._duration)
        self.player.playbackStateChanged.connect(self._playback_state)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._media_error)
        self._shortcut = QShortcut(QKeySequence("Space"), self)
        self._shortcut.activated.connect(self.toggle_play)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._save)
        self.queue.model().rowsMoved.connect(self._queue_changed)
        self._retranslate()
        if load_error:
            QTimer.singleShot(0, lambda: self._error(self.tr(
                "无法读取音乐库，原文件已保留：\n{message}", message=load_error,
            )))

    def tr(self, text: str, **values) -> str:
        return translate(text, self.language, **values)

    def _bind(self, widget, text, method="setText"):
        self._bindings.append((widget, method, text))
        getattr(widget, method)(self.tr(text))
        return widget

    def _text(self, text, name=""):
        return self._bind(label(name=name), text)

    def _button(self, text, fn, primary=False):
        return self._bind(button("", fn, primary), text)

    def _build(self, volume):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 20, 26, 18)
        outer.setSpacing(16)
        header = QHBoxLayout()
        header.addWidget(label("bili music", "brand"))
        header.addSpacing(12)
        header.addWidget(self._text("让喜欢的声音，陪你久一点", "muted"))
        header.addStretch()
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("", "light")
        self.theme_combo.addItem("", "dark")
        self.theme_combo.setCurrentIndex(1 if self.theme == "dark" else 0)
        self._bind(self.theme_combo, "主题", "setToolTip")
        self.theme_combo.currentIndexChanged.connect(lambda: self.set_theme(self.theme_combo.currentData()))
        header.addWidget(self.theme_combo)
        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", "zh_CN")
        self.language_combo.addItem("English", "en_US")
        self.language_combo.setCurrentIndex(1 if self.language == "en_US" else 0)
        self._bind(self.language_combo, "语言", "setToolTip")
        self.language_combo.currentIndexChanged.connect(lambda: self.set_language(self.language_combo.currentData()))
        header.addWidget(self.language_combo)
        outer.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(14)
        library = QWidget()
        lib_layout = QVBoxLayout(library)
        lib_layout.setContentsMargins(0, 4, 0, 0)
        lib_layout.setSpacing(12)
        title_row = QHBoxLayout()
        title_row.addWidget(self._text("我的音乐库", "heading"))
        title_row.addStretch()
        title_row.addWidget(self._button("＋ 新建专辑", self.new_album))
        title_row.addWidget(self._button("＋ 导入 BV", self.add_entry, True))
        lib_layout.addLayout(title_row)
        filter_row = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        for _ in range(3):
            self.tabs.addTab("")
        self.tabs.currentChanged.connect(self._render_library)
        filter_row.addWidget(self.tabs)
        filter_row.addStretch()
        self.search = QLineEdit()
        self._bind(self.search, "搜索名称 / BV", "setPlaceholderText")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(self._render_library)
        filter_row.addWidget(self.search)
        lib_layout.addLayout(filter_row)
        self.library_count = label(name="muted")
        lib_layout.addWidget(self.library_count)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.cards = QWidget()
        self.cards.setObjectName("cards")
        self.grid = QGridLayout(self.cards)
        self.grid.setContentsMargins(0, 0, 8, 12)
        self.grid.setSpacing(16)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.cards)
        lib_layout.addWidget(scroll, 1)
        splitter.addWidget(library)
        panel = QFrame()
        panel.setObjectName("queuePanel")
        panel.setMinimumWidth(260)
        panel.setMaximumWidth(390)
        queue_layout = QVBoxLayout(panel)
        queue_layout.setContentsMargins(16, 18, 16, 16)
        queue_header = QHBoxLayout()
        self.queue_title = label(name="section")
        queue_header.addWidget(self.queue_title)
        queue_header.addStretch()
        queue_header.addWidget(self._button("清空", self.clear_queue))
        queue_layout.addLayout(queue_header)
        hint = self._text("双击播放 · 拖动排序 · 右键移除", "muted")
        hint.setWordWrap(True)
        queue_layout.addWidget(hint)
        self.queue = QListWidget()
        self.queue.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.queue.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.queue.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.queue.itemDoubleClicked.connect(self.play_item)
        self.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._queue_menu)
        queue_layout.addWidget(self.queue, 1)
        self.queue_empty = self._text("还没有歌曲，去音乐库选一首吧。", "muted")
        self.queue_empty.setWordWrap(True)
        queue_layout.addWidget(self.queue_empty)
        splitter.addWidget(panel)
        splitter.setSizes([790, 300])
        outer.addWidget(splitter, 1)
        self.status = label(name="muted")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        player_frame = QFrame()
        player_frame.setObjectName("player")
        controls = QVBoxLayout(player_frame)
        controls.setContentsMargins(18, 14, 18, 14)
        top = QHBoxLayout()
        self.now_title = label(name="section")
        self.now_title.setWordWrap(True)
        self.now_title.setMaximumWidth(350)
        self.now_meta = label(name="muted")
        meta = QVBoxLayout()
        meta.addWidget(self.now_title)
        meta.addWidget(self.now_meta)
        top.addLayout(meta, 1)
        self.previous_button = button("", self.previous)
        self.previous_button.setIcon(media_icon("previous", self.theme))
        self._bind(self.previous_button, "上一首", "setToolTip")
        top.addWidget(self.previous_button)
        self.play_button = button("", self.toggle_play, True)
        self.play_button.setFixedSize(48, 40)
        self.play_button.setIcon(media_icon("play", self.theme, primary=True))
        self._bind(self.play_button, "播放 / 暂停（空格）", "setToolTip")
        top.addWidget(self.play_button)
        self.next_button = button("", self.next)
        self.next_button.setIcon(media_icon("next", self.theme))
        self._bind(self.next_button, "下一首", "setToolTip")
        top.addWidget(self.next_button)
        self.mode = QComboBox()
        self.mode.addItems(["", "", ""])
        top.addWidget(self.mode)
        top.addSpacing(10)
        top.addWidget(self._text("音量", "muted"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(volume)
        self.volume.setFixedWidth(85)
        self.volume.valueChanged.connect(self._volume)
        top.addWidget(self.volume)
        controls.addLayout(top)
        progress = QHBoxLayout()
        self.elapsed, self.total = label("0:00", "muted"), label("0:00", "muted")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setEnabled(False)
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        self.seek.sliderMoved.connect(lambda value: self.elapsed.setText(timestamp(value)))
        self.player.seekableChanged.connect(self.seek.setEnabled)
        progress.addWidget(self.elapsed)
        progress.addWidget(self.seek, 1)
        progress.addWidget(self.total)
        controls.addLayout(progress)
        outer.addWidget(player_frame)

    def _retranslate(self):
        self.setWindowTitle(self.tr("Bili Music · B站音乐播放器"))
        app = QApplication.instance()
        app.removeTranslator(self._qt_translator)
        QLocale.setDefault(QLocale(self.language))
        if self._qt_translator.load("qtbase_" + self.language, QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(self._qt_translator)
        for widget, method, text in self._bindings:
            getattr(widget, method)(self.tr(text))
        for index, text in enumerate(("全部", "专辑", "单曲")):
            self.tabs.setTabText(index, self.tr(text))
        for index, text in enumerate(("顺序播放", "列表循环", "单曲循环")):
            self.mode.setItemText(index, self.tr(text))
        for index, text in enumerate(("白天模式", "夜晚模式")):
            self.theme_combo.setItemText(index, self.tr(text))
        if self.current_id is None:
            self.now_title.setText(self.tr("还没有正在播放的音乐"))
            self.now_meta.setText(self.tr("从音乐库点击播放"))
        self._status(*self._status_value[:1], **self._status_value[1])
        self._queue_changed(persist=False)
        self._render_library()

    def set_theme(self, theme: str):
        if theme == self.theme:
            return
        self.theme = theme
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(theme))
        self.theme_combo.blockSignals(False)
        apply_theme(QApplication.instance(), theme)
        self.previous_button.setIcon(media_icon("previous", self.theme))
        self.next_button.setIcon(media_icon("next", self.theme))
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setIcon(media_icon("pause" if playing else "play", self.theme, primary=True))
        self._render_library()
        self._save()

    def set_language(self, language: str):
        if language == self.language:
            return
        self.language = language
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(self.language_combo.findData(language))
        self.language_combo.blockSignals(False)
        self._retranslate()
        self._save()

    def _status(self, text: str, **values):
        self._status_value = (text, values)
        self.status.setText(self.tr(text, **values))

    def _render_library(self, *_):
        if not hasattr(self, "grid"):
            return
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        query = self.search.text().strip().lower()
        kind = {1: "album", 2: "single"}.get(self.tabs.currentIndex())
        entries = [e for e in self.entries if (not kind or e.kind == kind)
                   and (query in e.title.lower() or query in e.bvid.lower())]
        self.library_count.setText(self.tr("{total} 项收藏 · 当前显示 {shown} 项", total=len(self.entries), shown=len(entries)))
        if not entries:
            empty = QFrame()
            layout = QVBoxLayout(empty)
            layout.setContentsMargins(20, 65, 20, 40)
            title = label(self.tr("给喜欢的音乐，留一个位置"), "section")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(title)
            hint = label(self.tr("导入 BV，或新建一个空专辑开始整理。" if not self.entries else "没有匹配的音乐，换个关键词试试。"), "muted")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            layout.addWidget(hint)
            self.grid.addWidget(empty, 0, 0, 1, 2)
            return
        for index, entry in enumerate(entries):
            card = QFrame()
            card.setObjectName("card")
            card.setMinimumWidth(235)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(9)
            art = CoverButton(self.tr("专辑" if entry.kind == "album" else "单曲"), self.theme)
            art.setAccessibleName(entry.title)
            art.setToolTip(self.tr("打开专辑" if entry.kind == "album" else "点击播放"))
            art.clicked.connect(lambda _checked=False, e=entry: self.open_album(e) if e.kind == "album" else self.resolve_entry(e, True))
            layout.addWidget(art)
            if entry.cover:
                self._load_cover(entry.cover, art)
            title = label(entry.title, "section")
            title.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(label(entry.bvid or self.tr("自建专辑"), "muted"))
            if entry.kind == "album":
                subtitle = self.tr("专辑 · {count} 首", count=len(entry.tracks)) if entry.resolved else self.tr("专辑 · 等待导入")
            else:
                subtitle = self.tr("单曲 · P{page}", page=entry.page)
            layout.addWidget(label(subtitle, "muted"))
            actions = QHBoxLayout()
            actions.addWidget(button(self.tr("播放"), lambda _checked=False, e=entry: self.resolve_entry(e, True), True))
            actions.addWidget(button(self.tr("＋ 队列"), lambda _checked=False, e=entry: self.resolve_entry(e, False)))
            more = button("···", lambda _checked=False, e=entry: self._entry_menu(e))
            more.setToolTip(self.tr("编辑 / 删除 / 在 B站打开"))
            actions.addWidget(more)
            layout.addLayout(actions)
            self.grid.addWidget(card, index // 2, index % 2)
        for column in range(2):
            self.grid.setColumnStretch(column, 1)

    def _load_cover(self, source: str, artwork: CoverButton):
        if source in self._cover_pixmaps:
            artwork.set_pixmap(self._cover_pixmaps[source])
            return
        reference = weakref.ref(artwork)
        if source in self._cover_waiters:
            self._cover_waiters[source].append(reference)
            return
        self._cover_waiters[source] = [reference]

        def done(path):
            pixmap = QPixmap(str(path))
            if len(self._cover_pixmaps) >= 64:
                self._cover_pixmaps.pop(next(iter(self._cover_pixmaps)))
            self._cover_pixmaps[source] = pixmap
            for ref in self._cover_waiters.pop(source, []):
                target = ref()
                if target is not None and not sip.isdeleted(target):
                    target.set_pixmap(pixmap)

        def failed(_message):
            for ref in self._cover_waiters.pop(source, []):
                target = ref()
                if target is not None and not sip.isdeleted(target):
                    target.setToolTip(self.tr("封面暂时无法加载"))

        self._submit(lambda: self.covers.fetch(source), done, failed)

    def add_entry(self):
        dialog = EntryDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.import_entry(dialog.value())

    def import_entry(self, entry: LibraryEntry):
        self._status("正在导入 {title}…", title=entry.title or entry.bvid)

        def done(info):
            custom_title = entry.title and entry.title != entry.bvid
            title = entry.title if custom_title else (info.title if entry.kind == "album" else info.tracks[0].title)
            tracks = copy_tracks(info.tracks)
            if entry.kind == "single" and custom_title:
                tracks[0].title = entry.title
            imported = replace(entry, title=title, cover=info.cover, tracks=tracks, resolved=True)
            self.entries.append(imported)
            self._render_library()
            self._save()
            self._status("已导入 {title}", title=imported.title)

        self._submit(lambda: BilibiliClient().get_video(entry), done)

    def new_album(self, _checked=False, title: str | None = None):
        if title is None:
            title, accepted = QInputDialog.getText(self, self.tr("新建空专辑"), self.tr("专辑名称"))
            if not accepted or not title.strip():
                return None
        title = title.strip()
        if not title:
            return None
        album = LibraryEntry(title, "", "album", resolved=True)
        self.entries.append(album)
        self._render_library()
        self._save()
        self._status("已创建空专辑")
        return album

    def _find(self, identity: str):
        return next((entry for entry in self.entries if entry.id == identity), None)

    def _ensure_entry(self, entry: LibraryEntry, done, failed=None):
        if entry.resolved:
            done(entry)
            return
        source = (entry.bvid, entry.page, entry.kind)
        was_present = self._find(entry.id) is not None
        self._status("正在读取 {title}…", title=entry.title)

        def loaded(info):
            current = self._find(entry.id)
            if current is None:
                if was_present:
                    return
                current = entry
            if source != (current.bvid, current.page, current.kind):
                return
            if not current.resolved:
                tracks = copy_tracks(info.tracks)
                if current.kind == "single" and current.title and current.title != current.bvid:
                    tracks[0].title = current.title
                current.tracks = tracks
                current.cover = current.cover or info.cover
                current.resolved = True
                if not current.title or current.title == current.bvid:
                    current.title = info.title if current.kind == "album" else tracks[0].title
                if was_present:
                    self._save()
                    self._render_library()
            done(current)

        self._submit(lambda: BilibiliClient().get_video(entry), loaded, failed)

    def open_album(self, entry: LibraryEntry):
        def opened(album):
            if album.id in self._editing_albums:
                return
            self._editing_albums.add(album.id)
            try:
                dialog = AlbumDialog(self, album, [e for e in self.entries if e.kind == "single"])
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    self.save_album(dialog.value())
            finally:
                self._editing_albums.discard(album.id)
                self._flush_album_additions(album.id)
        self._ensure_entry(entry, opened)

    def _flush_album_additions(self, identity: str):
        pending = self._deferred_album_additions.pop(identity, [])
        target = self._find(identity)
        if target is None:
            return
        for tracks, cover, title in pending:
            target.tracks.extend(tracks)
            target.cover = target.cover or cover
            self._status("已将 {title} 加入 {album}", title=title, album=target.title)
        if pending:
            self._save()
            self._render_library()

    def save_album(self, album: LibraryEntry):
        if self._find(album.id) is None:
            return
        if album.cover and not album.cover.startswith(("http://", "https://")):
            try:
                album.cover = str(self.covers.fetch(album.cover))
            except (OSError, ValueError) as exc:
                self._error(str(exc))
                return
        self.entries = [album if e.id == album.id else e for e in self.entries]
        self._render_library()
        self._save()
        self._status("专辑已保存")

    def add_single_to_album(self, single: LibraryEntry, album: LibraryEntry):
        def source_ready(source):
            target = self._find(album.id)
            if target is None or target.kind != "album":
                return

            def target_ready(target):
                if self._find(target.id) is not target:
                    return
                tracks = copy_tracks(source.tracks)
                if target.id in self._editing_albums:
                    self._deferred_album_additions.setdefault(target.id, []).append((tracks, source.cover, source.title))
                    return
                target.tracks.extend(tracks)
                if not target.cover:
                    target.cover = source.cover
                self._save()
                self._render_library()
                self._status("已将 {title} 加入 {album}", title=source.title, album=target.title)

            self._ensure_entry(target, target_ready)
        self._ensure_entry(single, source_ready)

    def _choose_album(self, entry):
        albums = [e for e in self.entries if e.kind == "album"]
        if not albums:
            self._error(self.tr("还没有专辑，请先新建一个。"))
            return
        choices = [f"{i + 1}. {e.title}" for i, e in enumerate(albums)]
        chosen, accepted = QInputDialog.getItem(self, self.tr("加入专辑"), self.tr("请选择专辑"), choices, 0, False)
        if accepted:
            self.add_single_to_album(entry, albums[choices.index(chosen)])

    def _entry_menu(self, entry):
        menu = QMenu(self)
        edit = menu.addAction(self.tr("编辑专辑" if entry.kind == "album" else "编辑"))
        add_to_album = menu.addAction(self.tr("加入专辑…")) if entry.kind == "single" else None
        open_web = menu.addAction(self.tr("在 B站打开"))
        open_web.setEnabled(bool(entry.bvid))
        menu.addSeparator()
        remove = menu.addAction(self.tr("从音乐库删除"))
        chosen = menu.exec(self.cursor().pos())
        if chosen is None:
            return
        if chosen == edit:
            if entry.kind == "album":
                self.open_album(entry)
            else:
                dialog = EntryDialog(self, entry)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    updated = dialog.value()
                    if updated.kind == "album":
                        updated.resolved = False
                        updated.tracks = []
                    if (updated.bvid, updated.page) == (entry.bvid, entry.page) and updated.kind == entry.kind:
                        updated.tracks = copy_tracks(updated.tracks)
                        if updated.tracks:
                            updated.tracks[0].title = updated.title
                    self.entries = [updated if e.id == entry.id else e for e in self.entries]
                    self._render_library()
                    self._save()
        elif add_to_album is not None and chosen == add_to_album:
            self._choose_album(entry)
        elif chosen == open_web:
            QDesktopServices.openUrl(QUrl(f"https://www.bilibili.com/video/{entry.bvid}/"))
        elif chosen == remove:
            self.entries = [e for e in self.entries if e.id != entry.id]
            self._render_library()
            self._save()

    def _submit(self, fn, done, error=None):
        task = Task(fn)
        self._tasks.add(task)
        task.signals.result.connect(lambda result: done(result) if not self._closing else None)
        task.signals.error.connect(lambda message: (error or self._error)(message) if not self._closing else None)
        task.signals.finished.connect(lambda: self._tasks.discard(task))
        self.pool.start(task)

    def resolve_entry(self, entry: LibraryEntry, play: bool):
        self._library_request += 1
        request = self._library_request
        if play:
            self._request += 1
        generation, epoch = self._request, self._queue_epoch

        def done(snapshot):
            if epoch != self._queue_epoch or (play and generation != self._request):
                return
            if not snapshot.tracks:
                self._status("这个专辑还没有歌曲，先添加几首吧。")
                return
            tracks = copy_tracks(snapshot.tracks)
            self._append_tracks(tracks)
            self._status("已加入队列 · {count} 首", count=len(tracks))
            if play:
                self.play_item(self.queue.item(self.queue.count() - len(tracks)))

        def failed(message):
            if epoch == self._queue_epoch and ((play and generation == self._request) or (not play and request == self._library_request)):
                self._error(message)

        self._ensure_entry(entry, done, failed)

    def _append_tracks(self, tracks: list[Track], persist=True):
        for track in tracks:
            item = QListWidgetItem(f"{track.title}\nP{track.page} · {track.artist or track.bvid}")
            item.setData(Qt.ItemDataRole.UserRole, track)
            item.setToolTip(f"{track.title}\n{track.bvid} · P{track.page}")
            self.queue.addItem(item)
        self._queue_changed(persist=persist)

    def _tracks(self) -> list[Track]:
        return [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue.count())]

    def _current_row(self) -> int:
        return next((i for i, t in enumerate(self._tracks()) if t.id == self.current_id), -1)

    def _queue_changed(self, *_, persist=True):
        self.queue_title.setText(self.tr("播放队列 · {count}", count=self.queue.count()))
        self.queue_empty.setVisible(self.queue.count() == 0)
        if persist:
            self._save()

    def play_item(self, item: QListWidgetItem | None):
        if item is None:
            return
        track = item.data(Qt.ItemDataRole.UserRole)
        self._request += 1
        request = self._request
        self.player.stop()
        self.player.setSource(QUrl())
        self.current_id = track.id
        self.queue.setCurrentItem(item)
        self.now_title.setText(track.title)
        self.now_meta.setText(f"{track.artist or track.bvid} · P{track.page}")
        self._status("正在获取音频地址…")

        def done(url):
            if request != self._request or track.id != self.current_id:
                return
            self.player.setSource(QUrl(self.proxy.set_stream(url)))
            self.player.play()
            self._status("正在缓冲音频…")

        self._submit(lambda: BilibiliClient().get_stream(track), done,
                     lambda message: self._error(message) if request == self._request else None)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.current_id and self.player.source().isValid() and self.player.mediaStatus() != QMediaPlayer.MediaStatus.InvalidMedia:
            self.player.play()
        else:
            self.play_item(self.queue.currentItem() or self.queue.item(0))

    def previous(self):
        self.play_item(self.queue.item(max(0, self._current_row() - 1)))

    def next(self, _checked=False, automatic=False):
        count = self.queue.count()
        if not count:
            return
        row = self._current_row()
        if automatic and self.mode.currentIndex() == 2 and row >= 0:
            self.play_item(self.queue.item(row))
            return
        next_row = row + 1
        if next_row >= count:
            if self.mode.currentIndex() == 1:
                next_row = 0
            else:
                self._status("已播放到队列末尾")
                return
        self.play_item(self.queue.item(next_row))

    def clear_queue(self):
        self._request += 1
        self._library_request += 1
        self._queue_epoch += 1
        self.player.stop()
        self.player.setSource(QUrl())
        self.current_id = None
        self.queue.clear()
        self.now_title.setText(self.tr("还没有正在播放的音乐"))
        self.now_meta.setText(self.tr("从音乐库点击播放"))
        self._status("队列已清空")
        self._queue_changed()

    def _queue_menu(self, pos):
        item = self.queue.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        play = menu.addAction(self.tr("播放"))
        remove = menu.addAction(self.tr("移出队列"))
        chosen = menu.exec(self.queue.viewport().mapToGlobal(pos))
        if chosen == play:
            self.play_item(item)
        elif chosen == remove:
            row = self.queue.row(item)
            is_current = item.data(Qt.ItemDataRole.UserRole).id == self.current_id
            self.queue.takeItem(row)
            if is_current:
                self._request += 1
                self.player.stop()
                self.player.setSource(QUrl())
                self.current_id = None
                if self.queue.count():
                    self.play_item(self.queue.item(min(row, self.queue.count() - 1)))
                else:
                    self.now_title.setText(self.tr("还没有正在播放的音乐"))
                    self.now_meta.setText(self.tr("从音乐库点击播放"))
            self._queue_changed()

    def _position(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
            self.elapsed.setText(timestamp(position))

    def _duration(self, duration):
        self.seek.setRange(0, duration)
        self.total.setText(timestamp(duration))

    def _playback_state(self, state):
        self.play_button.setIcon(media_icon(
            "pause" if state == QMediaPlayer.PlaybackState.PlayingState else "play", self.theme, primary=True,
        ))
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._status("正在播放")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._status("已暂停")

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next(automatic=True)

    def _media_error(self, error, message):
        if error != QMediaPlayer.Error.NoError and self.current_id:
            self._status("播放失败：{message}。可重新双击歌曲获取地址。", message=message)

    def _volume(self, value):
        self.audio.setVolume(value / 100)
        self.volume.setToolTip(f"{value}%")
        if hasattr(self, "save_timer"):
            self.save_timer.start(350)

    def _save(self):
        try:
            self.store.save(self.entries, self._tracks(), self.volume.value(),
                            {"theme": self.theme, "language": self.language})
        except (ValueError, OSError) as exc:
            self._status("保存失败：{message}", message=translate_error(str(exc), self.language))
            if not self._save_error_shown:
                self._save_error_shown = True
                self._error(self.tr("无法保存音乐库：{message}", message=str(exc)))

    def _error(self, message: str):
        message = translate_error(message, self.language)
        self.status.setText(message.replace("\n", " "))
        QMessageBox.warning(self, "Bili Music", message)

    def closeEvent(self, event):
        self._closing = True
        self._request += 1
        self.save_timer.stop()
        self._save()
        self.player.stop()
        self.player.setSource(QUrl())
        self.proxy.close()
        QApplication.instance().removeTranslator(self._qt_translator)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BiliMusic")
    app.setOrganizationName("BiliMusic")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

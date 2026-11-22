from __future__ import annotations

import sys
import time
import weakref
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import (
    QObject, QRunnable, QLibraryInfo, QLocale, QRectF, QStandardPaths, QThreadPool, QEvent, QPoint,
    QTimer, QTranslator, Qt, QUrl, QSize, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QSplitter, QStackedLayout, QStackedWidget, QSystemTrayIcon, QTabBar, QVBoxLayout, QWidget,
)

from .api import BilibiliClient
from .audio_files import AudioFiles
from .catalog import normalize_library, merge_libraries, find_song, delete_song, copy_entries
from .cards import CardArtwork, LibraryCard, LibraryGrid
from .covers import CoverCache
from .details import EntryDetailsDialog
from .dialog_dimming import install_dialog_dimming
from .dialogs import AlbumDialog, EntryDialog
from .exchange import read_library_package, write_library_package
from .i18n import translate, translate_error
from .list_dialog import ListImportDialog
from .lyrics import LyricsStore
from .mini_cover import CoverButton
from .mini_lyrics import MiniLyrics
from .models import AUDIO_EXTENSIONS, DEFAULT_MINI_COVER_STYLE, MINI_COVER_STYLES, LibraryEntry, Store, Track
from .network import set_proxy, validate_proxy_url
from .youtube import YouTubeClient, YouTubeError, read_youtube_cookie_file
from .playback_order import ShuffleOrder
from .window_chrome import TitleBar, FramelessController
from .stream import StreamProxy
from .settings_dialog import SettingsDialog
from .widgets import PlaybackTitleLabel, SeekSlider
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


class AudioDurationReader(QObject):
    durationRead = pyqtSignal(str, int)

    def __init__(self, parent):
        super().__init__(parent)
        self._pending: dict[Path, None] = {}
        self._current: Path | None = None
        self._player = QMediaPlayer(self)
        self._player.durationChanged.connect(self._duration_changed)
        self._player.errorOccurred.connect(lambda *_: self._finish())
        self._advance_timer = QTimer(self, singleShot=True)
        self._advance_timer.timeout.connect(self._read_next)
        self._timeout = QTimer(self, singleShot=True, interval=5000)
        self._timeout.timeout.connect(self._finish)

    def add(self, path: Path):
        if path != self._current:
            self._pending[path] = None
            if self._current is None:
                self._advance_timer.start(0)

    def _read_next(self):
        self._player.setSource(QUrl())
        if not self._pending:
            return
        self._current = next(iter(self._pending))
        del self._pending[self._current]
        self._timeout.start()
        self._player.setSource(QUrl.fromLocalFile(str(self._current)))

    def _duration_changed(self, milliseconds):
        if milliseconds > 0:
            self._finish(max(1, milliseconds // 1000))

    def _finish(self, seconds=0):
        path, self._current = self._current, None
        if path is None:
            return
        self._timeout.stop()
        self._advance_timer.start(0)
        if seconds:
            self.durationRead.emit(path.name, seconds)

    def stop(self):
        self._current = None
        self._pending.clear()
        self._advance_timer.stop()
        self._timeout.stop()
        self._player.setSource(QUrl())


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
    """Queue occurrences have their own IDs and retain the song entity ID."""
    return [replace(track, id=str(uuid4()), song_id=track.song_id or track.id) for track in tracks]


def media_icon(kind: str, theme: str, primary: bool = False) -> QIcon:
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#202635" if primary else PALETTES[theme]["text"])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    if kind.startswith("mode"):
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1.7))
        mode = int(kind[-1])
        if mode == 3:
            for points in (((3, 5), (7, 5), (15, 17), (20, 17)), ((3, 17), (7, 17), (15, 5), (20, 5))):
                path = QPainterPath()
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
                painter.drawPath(path)
            for y in (5, 17):
                painter.drawLine(17, y - 3, 20, y)
                painter.drawLine(17, y + 3, 20, y)
        elif mode == 0:
            painter.drawLine(3, 11, 20, 11)
            painter.drawLine(15, 6, 20, 11)
            painter.drawLine(15, 16, 20, 11)
        else:
            painter.drawRoundedRect(QRectF(3, 5, 16, 12), 4, 4)
            painter.drawLine(15, 2, 19, 5)
            painter.drawLine(15, 8, 19, 5)
            if mode == 2:
                font = painter.font()
                font.setPixelSize(9)
                painter.setFont(font)
                painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "1")
    elif kind == "pin":
        path = QPainterPath()
        path.moveTo(7, 3)
        path.lineTo(15, 3)
        path.lineTo(14, 9)
        path.lineTo(18, 14)
        path.lineTo(4, 14)
        path.lineTo(8, 9)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawRect(10, 14, 2, 6)
    elif kind == "queue":
        for y in (4, 10, 16):
            painter.drawRoundedRect(3, y, 3, 3, 1, 1)
            painter.drawRoundedRect(8, y, 12, 3, 1, 1)
    elif kind == "pause":
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


class PlayerPages(QStackedWidget):
    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return self.currentWidget().sizeHint() if self.currentWidget() else QSize(0, 0)


class MainWindow(QMainWindow):
    libraryChanged = pyqtSignal()
    lyricsChanged = pyqtSignal(str)

    def __init__(self, store_path: Path | None = None):
        super().__init__()
        install_dialog_dimming()
        self._frameless_controller = FramelessController(self)
        self.resize(1220, 820)
        self.setMinimumSize(1020, 700)
        if store_path is None:
            store_path = Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation,
            )) / "library.json"
        self.store = Store(store_path)
        self.lyrics_store = LyricsStore(self.store.path.with_name("lyrics.json"))
        self.entries: list[LibraryEntry] = []
        tracks: list[Track] = []
        volume, load_error = 70, ""
        try:
            self.entries, tracks, volume = self.store.load()
        except (ValueError, OSError) as exc:
            load_error = str(exc)
        self.theme = self.store.settings["theme"]
        self.language = self.store.settings["language"]
        self.proxy_mode = self.store.settings.get("proxy_mode", "system")
        self.proxy_url = self.store.settings.get("proxy_url", "")
        self.youtube_use_chrome = self.store.settings.get("youtube_use_chrome", True)
        self.youtube_use_cookie_file = self.store.settings.get("youtube_use_cookie_file", False)
        self.youtube_cookie_file = self.store.settings.get("youtube_cookie_file", "")
        set_proxy(self.proxy_mode, self.proxy_url)
        self.mini_cover_style = self.store.settings.get("mini_cover_style", DEFAULT_MINI_COVER_STYLE)
        self.audio_output_id = self.store.settings.get("audio_output_id", "")
        self._settings_dialog: SettingsDialog | None = None
        self._clearing_audio_cache = False
        self.card_scale = float(self.store.settings.get("card_scale", 1.0))
        self._saved_play_mode = self.store.settings.get("play_mode", 0)
        self.shuffle_order = ShuffleOrder()
        self._play_next_priority = []
        self._bindings = []
        self._status_value = ("准备就绪 · 添加 BV 号，开始收集你的音乐", {})
        self._qt_translator = QTranslator(self)
        self.current_id: str | None = None
        self._request = 0
        self._library_request = 0
        self._entry_play_request = 0
        self._queue_epoch = 0
        self._closing = False
        self._quitting = False
        self._mini_mode = False
        self._always_on_top = False
        self._library_drag_group = str(uuid4())
        self._library_drag_active = False
        self._library_render_pending = False
        self._selection_mode = False
        self._selected_entry_ids: set[str] = set()
        self._selection_anchor = None
        self._visible_entry_ids = []
        self._normal_geometry = None
        self._normal_maximized = False
        self._want_playing = False
        self._loading_stream = False
        self._resume_position: int | None = None
        self._last_played_position = 0
        self._last_progress_at = time.monotonic()
        self._reconnect_attempts = 0
        self._tasks: set[Task] = set()
        self._save_error_shown = False
        self._cover_pixmaps: dict[str, QPixmap] = {}
        self._cover_waiters: dict[str, list] = {}
        self._editing_albums: set[str] = set()
        self._song_edit_requests = {}
        self._deferred_album_additions: dict[str, list] = {}
        self.covers = CoverCache(self.store.path.parent / "covers")
        self.audio_files = AudioFiles(self.store.path.parent / "audio-files")
        self._audio_duration_reader = AudioDurationReader(self)
        self._audio_duration_reader.durationRead.connect(self._save_local_audio_duration)
        self._audio_downloads = set()
        self.pool = QThreadPool.globalInstance()
        self.proxy = StreamProxy(self.store.path.parent / "audio-cache")
        self.audio = QAudioOutput(self)
        self.audio.setVolume(volume / 100)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(self._sync_audio_output)
        self._sync_audio_output()
        apply_theme(QApplication.instance(), self.theme)
        self._build(volume)
        self._build_mini()
        self._build_tray()
        self._append_tracks(tracks, persist=False)
        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(self._duration)
        self.player.playbackStateChanged.connect(self._playback_state)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._media_error)
        self.player.seekableChanged.connect(lambda seekable: QTimer.singleShot(0, self._resume_stream) if seekable else None)
        self.playback_watchdog = QTimer(self)
        self.playback_watchdog.setInterval(1000)
        self.playback_watchdog.timeout.connect(self._check_playback_progress)
        self.playback_watchdog.start()
        self._shortcut = QShortcut(QKeySequence("Space"), self)
        self._shortcut.activated.connect(self.toggle_play)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._save)
        self.queue.model().rowsMoved.connect(self._queue_changed)
        self._retranslate()
        self._read_local_audio_durations()
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
        self.pages = PlayerPages()
        shell = QWidget()
        shell.setObjectName("root")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(1, 1, 1, 1)
        shell_layout.setSpacing(0)
        self.title_bar = TitleBar(self, self.language)
        shell_layout.addWidget(self.title_bar)
        shell_layout.addWidget(self.pages, 1)
        self.setCentralWidget(shell)
        self.pages.addWidget(root)
        self._selection_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._selection_escape.setEnabled(False)
        self._selection_escape.activated.connect(self.exit_library_selection)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 20, 26, 18)
        outer.setSpacing(16)
        header = QHBoxLayout()
        header.addWidget(label("BILI music", "brand"))
        header.addSpacing(12)
        header.addWidget(self._text("Basic Integrated Listening Infrastructure", "muted"))
        header.addStretch()
        self.settings_button = self._button("设置", self.open_settings)
        header.addWidget(self.settings_button)
        self.pin_button = self._make_pin_button()
        header.addWidget(self.pin_button)
        self.collapse_button = self._button("折叠", lambda: self.set_mini_mode(True))
        self._bind(self.collapse_button, "切换到迷你播放器", "setToolTip")
        header.addWidget(self.collapse_button)
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
        self.import_library_button = self._button("导入音乐库", self.import_library)
        title_row.addWidget(self.import_library_button)
        title_row.addWidget(self._button("导入列表", self.import_list))
        self.import_audio_button = self._button("导入本地音乐", self.import_local_audio)
        title_row.addWidget(self.import_audio_button)
        title_row.addWidget(self._button("＋ 导入视频", self.add_entry, True))
        lib_layout.addLayout(title_row)
        filter_row = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setExpanding(False)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
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
        self.selection_tools = QWidget()
        batch_tools = QHBoxLayout(self.selection_tools)
        batch_tools.setContentsMargins(0, 0, 0, 0)
        self.exit_selection_button = self._button("退出多选", self.exit_library_selection)
        self.select_all_button = self._button("全选", self.select_all_library)
        self.batch_copy_button = self._button("复制", lambda: self.copy_library_entries(
            [entry.id for entry in self._selected_library_entries()]))
        self.batch_delete_button = self._button("删除", lambda: self.delete_library_entries(
            [entry.id for entry in self._selected_library_entries()]))
        self.batch_export_button = self._button("批量导出", self.export_selection)
        self.batch_export_button.setEnabled(False)
        for widget in (self.exit_selection_button, self.select_all_button, self.batch_copy_button,
                       self.batch_delete_button, self.batch_export_button):
            batch_tools.addWidget(widget)
        self.selection_count = label(name="muted")
        batch_tools.addWidget(self.selection_count)
        batch_tools.addStretch()
        self.selection_tools.hide()
        lib_layout.addWidget(self.selection_tools)
        scroll = QScrollArea()
        self.library_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards = LibraryGrid()
        self.cards.set_scroll_area(scroll)
        self.cards.set_scale(self.card_scale)
        self.cards.scaleChanged.connect(self.set_card_scale)
        self.grid = self.cards.grid
        scroll.setWidget(self.cards)
        lib_layout.addWidget(scroll, 1)
        splitter.addWidget(library)
        panel = QFrame()
        panel.setObjectName("queuePanel")
        panel.setMinimumWidth(260)
        panel.setMaximumWidth(390)
        queue_layout = QVBoxLayout(panel)
        self.queue_layout = queue_layout
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
        self.queue.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        self.now_title = PlaybackTitleLabel()
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
        self.mode.addItems(["", "", "", ""])
        self.mode.setCurrentIndex(self._saved_play_mode if type(self._saved_play_mode) is int and 0 <= self._saved_play_mode < 4 else 0)
        self.mode.currentIndexChanged.connect(self._mode_changed)
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
        self.seek = SeekSlider()
        self.seek.setRange(0, 0)
        self.seek.setEnabled(False)
        self.seek.seekRequested.connect(self._seek_to)
        self.seek.sliderMoved.connect(lambda value: self.elapsed.setText(timestamp(value)))
        self.player.seekableChanged.connect(self.seek.setEnabled)
        progress.addWidget(self.elapsed)
        progress.addWidget(self.seek, 1)
        progress.addWidget(self.total)
        controls.addLayout(progress)
        outer.addWidget(player_frame)

    def _build_mini(self):
        mini = QWidget()
        self.mini_surface = mini
        mini.installEventFilter(self)
        mini.setObjectName("root")
        layout = QVBoxLayout(mini)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(label("bili music", "section"))
        header.addStretch()
        self.mini_pin_button = self._make_pin_button()
        header.addWidget(self.mini_pin_button)
        self.expand_button = self._button("展开", lambda: self.set_mini_mode(False))
        self._bind(self.expand_button, "返回完整播放器", "setToolTip")
        header.addWidget(self.expand_button)
        layout.addLayout(header)
        self.mini_cover = CoverButton(self.theme)
        self.mini_cover.set_cover_style(self.mini_cover_style)
        self.mini_cover.setMinimumSize(240, 240)
        self.mini_cover.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.mini_cover.clicked.connect(self._toggle_mini_lyrics)
        self._bind(self.mini_cover, "显示歌词", "setToolTip")
        self.mini_lyrics = MiniLyrics()
        self.mini_lyrics.coverRequested.connect(self._toggle_mini_lyrics)
        self._bind(self.mini_lyrics, "返回封面", "setToolTip")
        self._bind(self.mini_lyrics, "返回封面", "setAccessibleName")
        self.mini_artwork = QStackedLayout()
        self.mini_artwork.addWidget(self.mini_cover)
        self.mini_artwork.addWidget(self.mini_lyrics)
        layout.addLayout(self.mini_artwork, 1)
        self.lyricsChanged.connect(self._on_lyrics_changed)
        self.mini_title = label(name="section")
        self.mini_title.setWordWrap(True)
        self.mini_title.setMaximumHeight(60)
        self.mini_title.setMinimumWidth(0)
        layout.addWidget(self.mini_title)
        self.mini_meta = label(name="muted")
        self.mini_meta.setWordWrap(True)
        self.mini_meta.setMaximumHeight(40)
        layout.addWidget(self.mini_meta)
        self.mini_seek = SeekSlider()
        self.mini_seek.setRange(0, 0)
        self.mini_seek.setEnabled(False)
        self.mini_seek.seekRequested.connect(self._seek_to)
        self.mini_seek.sliderMoved.connect(lambda value: self.mini_elapsed.setText(timestamp(value)))
        self.player.seekableChanged.connect(self.mini_seek.setEnabled)
        layout.addWidget(self.mini_seek)
        times = QHBoxLayout()
        self.mini_elapsed, self.mini_total = label("0:00", "muted"), label("0:00", "muted")
        times.addWidget(self.mini_elapsed)
        times.addStretch()
        times.addWidget(self.mini_total)
        layout.addLayout(times)
        controls = QHBoxLayout()
        controls.setSpacing(3)
        self.mini_mode_button = button("", self.show_playback_modes)
        self.mini_mode_button.setFixedSize(44, 44)
        self.mini_mode_button.setProperty("flatIcon", True)
        self.mini_mode_button.setFlat(True)
        controls.addWidget(self.mini_mode_button)
        controls.addStretch()
        self.mini_previous = button("", self.previous)
        self.mini_play = button("", self.toggle_play, True)
        self.mini_next = button("", self.next)
        for widget, text in ((self.mini_previous, "上一首"),
                             (self.mini_play, "播放 / 暂停（空格）"),
                             (self.mini_next, "下一首")):
            widget.setFixedSize(56, 44)
            self._bind(widget, text, "setToolTip")
            controls.addWidget(widget)
        controls.addStretch()
        self.mini_queue_button = button("", self.toggle_mini_queue)
        self.mini_queue_button.setFixedSize(44, 44)
        self.mini_queue_button.setProperty("flatIcon", True)
        self.mini_queue_button.setFlat(True)
        self.mini_queue_button.setCheckable(True)
        self._bind(self.mini_queue_button, "展开 / 收起播放队列", "setToolTip")
        self._bind(self.mini_queue_button, "播放队列", "setAccessibleName")
        controls.addWidget(self.mini_queue_button)
        layout.addLayout(controls)
        self.mini_queue_panel = QFrame(mini)
        self.mini_queue_panel.setObjectName("queueOverlay")
        self.mini_queue_panel.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.mini_queue_layout = QVBoxLayout(self.mini_queue_panel)
        self.mini_queue_layout.setContentsMargins(12, 10, 12, 10)
        queue_header = QHBoxLayout()
        self.mini_queue_title = label(name="section")
        queue_header.addWidget(self.mini_queue_title)
        queue_header.addStretch()
        queue_header.addWidget(self._button("清空", self.clear_queue))
        self.mini_queue_layout.addLayout(queue_header)
        self.mini_queue_empty = self._text("还没有歌曲，去音乐库选一首吧。", "muted")
        self.mini_queue_empty.setWordWrap(True)
        self.mini_queue_layout.addWidget(self.mini_queue_empty)
        self.mini_queue_panel.hide()
        self._mini_queue_open = False
        self._queue_overlay_filter = False
        self.pages.addWidget(mini)
        self._refresh_mini_theme()

    def _mode_changed(self, _index=None):
        self.shuffle_order.reset()
        self.shuffle_order.record(self.current_id)
        self._refresh_mode_button()
        if hasattr(self, "save_timer"):
            self.save_timer.start(200)

    def _refresh_mode_button(self):
        if not hasattr(self, "mini_mode_button"):
            return
        names = ("顺序播放", "列表循环", "单曲循环", "随机播放")
        index = self.mode.currentIndex()
        self.mini_mode_button.setText("")
        self.mini_mode_button.setIcon(media_icon(f"mode{index}", self.theme))
        self.mini_mode_button.setToolTip(self.tr("播放模式：{mode}", mode=self.tr(names[index])))
        self.mini_mode_button.setAccessibleName(self.tr(names[index]))

    def show_playback_modes(self):
        menu = QMenu(self)
        for index, text in enumerate(("顺序播放", "列表循环", "单曲循环", "随机播放")):
            action = menu.addAction(self.tr(text))
            action.setCheckable(True)
            action.setChecked(self.mode.currentIndex() == index)
            action.triggered.connect(lambda _checked=False, value=index: self.mode.setCurrentIndex(value))
        menu.exec(self.mini_mode_button.mapToGlobal(self.mini_mode_button.rect().topLeft()))
        menu.deleteLater()

    def _make_pin_button(self):
        widget = button("", lambda: self.set_always_on_top(not self._always_on_top))
        widget.setCheckable(True)
        widget.setProperty("flatIcon", True)
        widget.setFlat(True)
        widget.setFixedSize(36, 34)
        return widget

    def _refresh_pin_buttons(self):
        hint = self.tr("取消置顶" if self._always_on_top else "置顶播放器")
        for widget in (self.pin_button, self.mini_pin_button):
            widget.setChecked(self._always_on_top)
            widget.setToolTip(hint)
            widget.setAccessibleName(hint)
            widget.setIcon(media_icon("pin", self.theme))

    def set_always_on_top(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self._always_on_top:
            return
        visible, state, geometry = self.isVisible(), self.windowState(), self.geometry()
        self._always_on_top = enabled
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.setGeometry(geometry)
        self.setWindowState(state)
        if visible:
            self.show()
        self._refresh_pin_buttons()

    def _position_mini_queue(self):
        if sip.isdeleted(self) or self._closing or not hasattr(self, "mini_queue_panel") or not self._mini_mode:
            return
        surface = self.mini_surface
        bottom = self.mini_play.mapTo(surface, QPoint(0, 0)).y() - 8
        top_limit = self.expand_button.mapTo(surface, self.expand_button.rect().bottomLeft()).y() + 12
        height = max(80, min(300, bottom - top_limit))
        self.mini_queue_panel.setGeometry(20, max(top_limit, bottom - height),
                                          max(1, surface.width() - 40), height)
        if self._mini_queue_open:
            self.mini_queue_panel.raise_()

    def _set_mini_queue_open(self, opened: bool):
        self._mini_queue_open = opened
        self.mini_queue_button.setChecked(opened)
        self._position_mini_queue()
        self.mini_queue_panel.setVisible(opened and self._mini_mode)
        app = QApplication.instance()
        if opened and not self._queue_overlay_filter:
            app.installEventFilter(self)
            self._queue_overlay_filter = True
        elif not opened and self._queue_overlay_filter:
            app.removeEventFilter(self)
            self._queue_overlay_filter = False
        if opened:
            self.mini_queue_panel.raise_()
            current = self.queue.currentItem()
            if current is not None:
                self.queue.scrollToItem(current)

    def toggle_mini_queue(self):
        if self._mini_mode:
            self._set_mini_queue_open(not self._mini_queue_open)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "mini_surface", None) and event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            QTimer.singleShot(0, self._position_mini_queue)
        if getattr(self, "_mini_queue_open", False):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._set_mini_queue_open(False)
                return True
            if event.type() == QEvent.Type.MouseButtonPress:
                point = event.globalPosition().toPoint()
                panel = self.mini_queue_panel
                on_panel = panel.rect().contains(panel.mapFromGlobal(point))
                on_button = self.mini_queue_button.rect().contains(self.mini_queue_button.mapFromGlobal(point))
                if not on_panel and not on_button:
                    self._set_mini_queue_open(False)
                    # Dismiss clicks on the cover must not also toggle playback.
                    if isinstance(watched, QWidget) and watched.window() is self:
                        return True
        return super().eventFilter(watched, event)

    def _refresh_mini_theme(self):
        self.mini_cover.theme = self.theme
        self.mini_cover.update()
        self.mini_previous.setIcon(media_icon("previous", self.theme))
        self.mini_next.setIcon(media_icon("next", self.theme))
        self.mini_queue_button.setIcon(media_icon("queue", self.theme))
        self._refresh_mode_button()
        self._refresh_pin_buttons()
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.mini_play.setIcon(media_icon("pause" if playing else "play", self.theme, primary=True))

    def set_mini_mode(self, enabled: bool):
        if enabled == self._mini_mode:
            return
        self._mini_mode = enabled
        self._selection_escape.setEnabled(self._selection_mode and not enabled)
        if enabled:
            self._normal_maximized = self.isMaximized()
            self._normal_geometry = self.normalGeometry() if self._normal_maximized else self.geometry()
            self.showNormal()
            self.pages.setCurrentIndex(1)
            self.mini_queue_layout.insertWidget(1, self.queue, 1)
            self.queue.show()
            self.setMinimumSize(320, 500)
            self.resize(360, 560)
            QTimer.singleShot(0, self._position_mini_queue)
        else:
            self._set_mini_queue_open(False)
            self.queue_layout.insertWidget(2, self.queue, 1)
            self.queue.show()
            self.pages.setCurrentIndex(0)
            self.setMinimumSize(1020, 700)
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            if self._normal_maximized:
                self.showMaximized()
        self._sync_mini_text()

    def _sync_mini_text(self):
        self.mini_title.setText(self.now_title.text())
        self.mini_title.setToolTip(self.now_title.text())
        artist = self.now_meta.text()
        self.now_meta.setVisible(bool(artist))
        self.mini_meta.setText(artist)
        self.mini_meta.setVisible(bool(artist))
        self.mini_cover.setAccessibleName(self.now_title.text())
        self.tray.setToolTip(("Bili Music · " + self.now_title.text())[:127])

    def song_lyrics(self, song_id):
        try:
            saved = self.lyrics_store.load(song_id)
        except (OSError, ValueError):
            return (), None
        return saved.entries, saved.active_id

    def save_song_lyrics(self, song_id, entry):
        if find_song(self.entries, song_id) is None:
            return False
        try:
            self.lyrics_store.save(song_id, entry)
        except (OSError, ValueError):
            self._error(self.tr("无法保存歌词"))
            return False
        self.lyricsChanged.emit(song_id)
        return True

    def set_active_lyrics(self, song_id, entry_id):
        try:
            self.lyrics_store.set_active(song_id, entry_id)
        except (OSError, ValueError):
            self._error(self.tr("无法保存歌词"))
            return False
        self.lyricsChanged.emit(song_id)
        return True

    def _current_lyrics_song_id(self):
        item = self._queue_item_for_id(self.current_id) if self.current_id is not None else None
        if item is None:
            return None
        track = item.data(Qt.ItemDataRole.UserRole)
        return track.song_id or track.id

    def _refresh_mini_lyrics(self):
        if self.mini_artwork.currentWidget() is not self.mini_lyrics:
            return
        song_id = self._current_lyrics_song_id()
        entries, active_id = self.song_lyrics(song_id) if song_id else ((), None)
        active = next((entry for entry in entries if entry.id == active_id), None)
        self.mini_lyrics.set_lyrics(active.display() if active else None,
                                   self.tr("纯音乐" if active and active.instrumental else "暂无已保存歌词"))
        self.mini_lyrics.set_position(self.player.position())

    def _on_lyrics_changed(self, song_id):
        if song_id == self._current_lyrics_song_id():
            self._refresh_mini_lyrics()

    def _toggle_mini_lyrics(self):
        if self.mini_artwork.currentWidget() is self.mini_cover:
            self.mini_artwork.setCurrentWidget(self.mini_lyrics)
            self._refresh_mini_lyrics()
            self.mini_lyrics.setFocus()
        else:
            self.mini_artwork.setCurrentWidget(self.mini_cover)
            self.mini_cover.setFocus()

    def _update_current_cover(self, track=None):
        self._refresh_mini_lyrics()
        # Detach the previous song's pending cover before reusing this artwork widget.
        self.mini_cover.set_pixmap(QPixmap())
        for waiters in self._cover_waiters.values():
            waiters[:] = [ref for ref in waiters if ref() is not self.mini_cover]
        if track is None:
            return
        entry = find_song(self.entries, track.song_id) if track.song_id else None
        if entry is None:
            entry = next((e for e in self.entries if any(t.id == track.id for t in e.tracks)), None)
        if entry and entry.cover:
            self._load_cover(entry.cover, self.mini_cover)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self)
        icon = QPixmap(64, 64)
        icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(PALETTES[self.theme]["accent"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 60, 60, 16, 16)
        painter.drawPixmap(10, 10, 44, 44, media_icon("play", self.theme).pixmap(44, 44))
        painter.end()
        self.setWindowIcon(QIcon(icon))
        self.tray.setIcon(self.windowIcon())
        menu = QMenu(self)
        self._bind(menu.addAction(""), "显示播放器").triggered.connect(self.restore_window)
        self._bind(menu.addAction(""), "迷你播放器").triggered.connect(self.show_mini_player)
        menu.addSeparator()
        self._bind(menu.addAction(""), "播放 / 暂停（空格）").triggered.connect(self.toggle_play)
        self._bind(menu.addAction(""), "上一首").triggered.connect(self.previous)
        self._bind(menu.addAction(""), "下一首").triggered.connect(self.next)
        menu.addSeparator()
        self._bind(menu.addAction(""), "退出").triggered.connect(self.quit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_window()

    def restore_window(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def show_mini_player(self):
        self.set_mini_mode(True)
        self.restore_window()

    def quit_application(self):
        self._quitting = True
        self.close()
        QApplication.instance().quit()

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
        for index, text in enumerate(("顺序播放", "列表循环", "单曲循环", "随机播放")):
            self.mode.setItemText(index, self.tr(text))
        self.title_bar.set_language(self.language)
        self._refresh_mode_button()
        self._refresh_settings()
        if self.current_id is None:
            self.now_title.setText(self.tr("还没有正在播放的音乐"))
            self.now_meta.setText(self.tr("从音乐库点击播放"))
        self._sync_mini_text()
        self._refresh_mini_lyrics()
        self._refresh_pin_buttons()
        self._status(*self._status_value[:1], **self._status_value[1])
        self._queue_changed(persist=False)
        self._render_library()

    def set_theme(self, theme: str):
        if theme == self.theme:
            return
        self.theme = theme
        if self._settings_dialog is not None:
            self._settings_dialog.refresh_preferences()
        apply_theme(QApplication.instance(), theme)
        self.previous_button.setIcon(media_icon("previous", self.theme))
        self.next_button.setIcon(media_icon("next", self.theme))
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setIcon(media_icon("pause" if playing else "play", self.theme, primary=True))
        self._refresh_mini_theme()
        self._render_library()
        self._save()

    def set_card_scale(self, value: float):
        self.cards.set_scale(value)
        self.card_scale = self.cards.scale
        if hasattr(self, "save_timer"):
            self.save_timer.start(350)

    def set_language(self, language: str):
        if language == self.language:
            return
        self.language = language
        self._retranslate()
        self._save()

    def _status(self, text: str, **values):
        self._status_value = (text, values)
        self.status.setText(self.tr(text, **values))

    def _card_drag_state(self, active: bool):
        self._library_drag_active = active
        if not active and self._library_render_pending and not self._closing:
            QTimer.singleShot(0, self._refresh_library_after_drag)

    def _refresh_library_after_drag(self):
        if self._closing or self._library_drag_active or not self._library_render_pending:
            return
        bar = self.library_scroll.verticalScrollBar()
        position = bar.value()
        focus = QApplication.focusWidget()
        if focus is not None and self.cards.isAncestorOf(focus):
            self.library_scroll.setFocus()
        self._render_library()
        bar.setValue(position)

    def _render_library(self, *_):
        if not hasattr(self, "grid") or self._closing:
            return
        if self._library_drag_active:
            self._library_render_pending = True
            return
        self._library_render_pending = False
        self.cards.clear()
        query = self.search.text().strip().lower()
        kind = {1: "album", 2: "single"}.get(self.tabs.currentIndex())
        album_titles = {}
        albums = sorted((entry for entry in self.entries if entry.kind == "album"),
                        key=lambda entry: (entry.title.casefold(), entry.id))
        for album in albums:
            for track in album.tracks:
                album_titles.setdefault(track.song_id or track.id, album.title)
        entries = [e for e in self.entries if (not kind or e.kind == kind)
                   and (kind is not None or e.kind == "album" or e.id not in album_titles)
                   and (query in e.title.lower() or query in e.bvid.lower())]
        self._visible_entry_ids = [e.id for e in entries]
        self._selected_entry_ids.intersection_update(self._visible_entry_ids)
        self._refresh_library_selection()
        self.library_count.setText(self.tr("{total} 项收藏 · 当前显示 {shown} 项", total=len(self.entries), shown=len(entries)))
        if not entries:
            empty = QFrame()
            layout = QVBoxLayout(empty)
            layout.setContentsMargins(20, 65, 20, 40)
            title = label(self.tr("给喜欢的音乐，留一个位置"), "section")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(title)
            hint = label(self.tr("导入视频，或新建一个空专辑开始整理。" if not self.entries else "没有匹配的音乐，换个关键词试试。"), "muted")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            layout.addWidget(hint)
            self.cards.show_empty(empty)
            return
        cards = []
        for entry in entries:
            card = LibraryCard(entry.id, self._library_drag_group, self.tr("拖动到另一张卡片以交换位置"))
            card.swapRequested.connect(self.swap_library_entries)
            card.dragActiveChanged.connect(self._card_drag_state)
            card.setAccessibleName(entry.title)
            card.openRequested.connect(lambda _id, e=entry: self.open_entry(e))
            card.playRequested.connect(lambda _id, e=entry: self.resolve_entry(e, True))
            card.menuRequested.connect(lambda _id, pos, e=entry: self._entry_menu(e, pos))
            card.selectionRequested.connect(self.select_library_entry)
            art = CardArtwork(self.theme)
            art.setAccessibleName(entry.title)
            art.setToolTip(self.tr("打开专辑" if entry.kind == "album" else "打开单曲详情"))
            if entry.kind == "album":
                subtitle = self.tr("专辑 · {count} 首", count=len(entry.tracks)) if entry.resolved else self.tr("专辑 · 等待导入")
            else:
                artist = entry.artist.strip()
                if not artist and entry.tracks:
                    artist = entry.tracks[0].artist.strip()
                album_title = album_titles.get(entry.id, "").strip()
                subtitle = " · ".join(filter(None, (album_title, artist))) or self.tr("单曲")
            card.set_content(art, entry.title, subtitle, self.tr("播放"))
            card.set_selection(self._selection_mode, entry.id in self._selected_entry_ids)
            card.selection_box.setAccessibleName(self.tr("选择 {title}", title=entry.title))
            card.subtitle_label.setToolTip(subtitle + ("\n" + entry.bvid if entry.video_url else ""))
            if entry.cover:
                self._load_cover(entry.cover, art)
            cards.append(card)
        self.cards.show_cards(cards)

    def enter_library_selection(self, identity):
        if identity not in self._visible_entry_ids:
            return
        self._selection_mode = True
        self._selected_entry_ids.add(identity)
        self._selection_anchor = identity
        self._refresh_library_selection()

    def exit_library_selection(self):
        self._selection_mode = False
        self._selected_entry_ids.clear()
        self._selection_anchor = None
        self._refresh_library_selection()

    def _refresh_library_selection(self):
        self.selection_tools.setVisible(self._selection_mode)
        self._selection_escape.setEnabled(self._selection_mode and not self._mini_mode)
        self.select_all_button.setEnabled(any(
            identity not in self._selected_entry_ids for identity in self._visible_entry_ids
        ))
        self.selection_count.setText(self.tr("已选择 {count} 项", count=len(self._selected_entry_ids)))
        for widget in (self.batch_copy_button, self.batch_delete_button, self.batch_export_button):
            widget.setEnabled(bool(self._selected_entry_ids))
        for card in self.cards._cards:
            card.set_selection(self._selection_mode, card.entry_id in self._selected_entry_ids)

    def select_library_entry(self, identity, modifiers=Qt.KeyboardModifier.NoModifier):
        if identity not in self._visible_entry_ids:
            return
        self._selection_mode = True
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self._selection_anchor in self._visible_entry_ids:
            first = self._visible_entry_ids.index(self._selection_anchor)
            last = self._visible_entry_ids.index(identity)
            self._selected_entry_ids.update(self._visible_entry_ids[min(first, last):max(first, last) + 1])
        elif identity in self._selected_entry_ids:
            self._selected_entry_ids.remove(identity)
        else:
            self._selected_entry_ids.add(identity)
        self._selection_anchor = identity
        self._refresh_library_selection()

    def select_all_library(self):
        self._selection_mode = True
        self._selected_entry_ids = set(self._visible_entry_ids)
        self._refresh_library_selection()

    def clear_library_selection(self):
        self._selected_entry_ids.clear()
        self._selection_anchor = None
        self._refresh_library_selection()

    def _selected_library_entries(self):
        return [entry for entry in self.entries if entry.id in self._selected_entry_ids]

    def show_library_batch_menu(self, position=None):
        entries = self._selected_library_entries()
        if not entries:
            return
        menu = QMenu(self)
        identities = [entry.id for entry in entries]
        menu.addAction(self.tr("添加所选到队列"), lambda: self.queue_library_entries(identities))
        menu.addAction(self.tr("复制所选"), lambda: self.copy_library_entries(identities))
        add = menu.addAction(self.tr("加入专辑…"), lambda: self.choose_album_for_entries(identities))
        add.setEnabled(all(entry.kind == "single" for entry in entries))
        menu.addSeparator()
        menu.addAction(self.tr("删除所选"), lambda: self.delete_library_entries(identities))
        menu.exec(position if position is not None else self.cursor().pos())
        menu.deleteLater()

    def queue_library_entries(self, identities):
        # Resolve sequentially so queue ordering matches the selected card order.
        pending = list(identities)
        epoch = self._queue_epoch
        def append_next():
            if self._closing or epoch != self._queue_epoch or not pending:
                return
            entry = self._find(pending.pop(0))
            if entry is None:
                append_next()
                return
            def ready(current):
                if epoch != self._queue_epoch:
                    return
                if self._find(current.id) is not None:
                    self._append_unique_tracks(current.tracks)
                append_next()
            self._ensure_entry(entry, ready, lambda message: (self._error(message), append_next()))
        append_next()

    def choose_album_for_entries(self, identities):
        albums = [entry for entry in self.entries if entry.kind == "album"]
        if not albums:
            self._error(self.tr("还没有专辑，请先新建一个。"))
            return
        choices = [f"{index + 1}. {entry.title}" for index, entry in enumerate(albums)]
        chosen, accepted = QInputDialog.getItem(self._dialog_host(), self.tr("加入专辑"), self.tr("请选择专辑"), choices, 0, False)
        if accepted:
            album = albums[choices.index(chosen)]
            for identity in identities:
                song = self._find(identity)
                if song is not None and song.kind == "single":
                    self.add_single_to_album(song, album)

    def copy_library_entries(self, identities):
        self._normalize_library()
        copied = copy_entries(self.entries, identities)
        if not copied:
            return []
        self.entries.extend(copied)
        self._normalize_library()
        self._render_library()
        saved = self._save()
        self.libraryChanged.emit()
        if saved:
            self._status("已复制 {count} 项", count=len(identities))
        return copied

    def batch_queue_tracks(self, album_id, song_ids):
        album = self._find(album_id)
        if album is None or album.kind != "album":
            return
        selected = set(song_ids)
        self._append_unique_tracks(track for track in album.tracks if (track.song_id or track.id) in selected)

    def batch_copy_tracks(self, album_id, song_ids):
        album = self._find(album_id)
        if album is None or album.kind != "album":
            return
        selected = set(song_ids)
        identities = [track.song_id or track.id for track in album.tracks if (track.song_id or track.id) in selected]
        copied = copy_entries(self.entries, identities)
        self.entries.extend(copied)
        album.tracks.extend(entry.tracks[0] for entry in copied if entry.kind == "single" and entry.tracks)
        self._normalize_library()
        self._render_library()
        self._save()
        self.libraryChanged.emit()

    def batch_remove_album_tracks(self, album_id, song_ids):
        album = self._find(album_id)
        if album is None or album.kind != "album":
            return
        selected = set(song_ids)
        album.tracks[:] = [track for track in album.tracks if (track.song_id or track.id) not in selected]
        self._render_library()
        self._save()
        self.libraryChanged.emit()

    def swap_library_entries(self, source_id: str, target_id: str):
        if source_id == target_id:
            return
        indexes = {entry.id: index for index, entry in enumerate(self.entries)}
        if source_id not in indexes or target_id not in indexes:
            return
        source, target = indexes[source_id], indexes[target_id]
        self.entries[source], self.entries[target] = self.entries[target], self.entries[source]
        visible = set(self._visible_entry_ids)
        ordered_ids = [entry.id for entry in self.entries if entry.id in visible]
        if len(ordered_ids) == len(visible):
            self.cards.reorder_cards(ordered_ids)
            self._visible_entry_ids = ordered_ids
        else:
            self._library_render_pending = True
            self._refresh_library_after_drag()
        self._save()

    def _load_cover(self, source: str, artwork):
        artwork._requested_cover = source
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
                if target is not None and not sip.isdeleted(target) and getattr(target, "_requested_cover", None) == source:
                    target.set_pixmap(pixmap)

        def failed(_message):
            for ref in self._cover_waiters.pop(source, []):
                target = ref()
                if target is not None and not sip.isdeleted(target) and getattr(target, "_requested_cover", None) == source:
                    target.setToolTip(self.tr("封面暂时无法加载"))

        self._submit(lambda: self.covers.fetch(source), done, failed)

    def open_settings(self):
        if self._settings_dialog is not None:
            return
        dialog = SettingsDialog(self)
        self._settings_dialog = dialog
        try:
            self._refresh_settings()
            dialog.exec()
        finally:
            self._settings_dialog = None
            if not sip.isdeleted(dialog):
                dialog.deleteLater()

    def set_mini_cover_style(self, style: str):
        if style not in MINI_COVER_STYLES or style == self.mini_cover_style:
            return
        self.mini_cover_style = style
        self.mini_cover.set_cover_style(style)
        if self._settings_dialog is not None:
            self._settings_dialog.refresh_mini_cover()
        self._save()

    def _refresh_settings(self):
        if self._settings_dialog is not None:
            self._settings_dialog.refresh_preferences()
            self._sync_audio_output()
            self._refresh_cache_settings()

    def _sync_audio_output(self):
        if self._closing:
            return
        devices = self.media_devices.audioOutputs()
        default = self.media_devices.defaultAudioOutput()
        selected = next((device for device in devices
                         if bytes(device.id()).hex() == self.audio_output_id), default)
        if not selected.isNull() and selected != self.audio.device():
            self.audio.setDevice(selected)
        if self._settings_dialog is not None:
            self._settings_dialog.refresh_outputs(devices, default, self.audio_output_id)

    def set_audio_output(self, device_id: str):
        if self.audio_output_id == device_id:
            return
        self.audio_output_id = device_id
        self._sync_audio_output()
        self._save()

    def set_youtube_use_chrome(self, enabled: bool):
        self.set_youtube_session(enabled)

    def set_youtube_session(self, choice: bool | str):
        previous = self.youtube_use_chrome, self.youtube_use_cookie_file
        manual = choice == "file"
        chrome = self.youtube_use_chrome if manual else choice is True
        if previous == (chrome, manual):
            return
        self.youtube_use_chrome, self.youtube_use_cookie_file = chrome, manual
        if not self._save():
            self.youtube_use_chrome, self.youtube_use_cookie_file = previous
        if self._settings_dialog is not None:
            self._settings_dialog.refresh_youtube_session()

    def set_youtube_cookie_file(self, filename: str) -> str | None:
        if filename:
            try:
                filename = str(Path(filename).expanduser().resolve())
                read_youtube_cookie_file(filename)
            except YouTubeError as exc:
                return str(exc)
            except (OSError, RuntimeError, ValueError):
                return "无法读取 YouTube Cookie 文件，请重新选择"
        previous = self.youtube_cookie_file
        if previous == filename:
            return None
        self.youtube_cookie_file = filename
        if not self._save():
            self.youtube_cookie_file = previous
            return "无法保存 YouTube Cookie 文件设置"
        return None

    def set_proxy_settings(self, mode: str, address: str) -> str | None:
        try:
            if mode not in ("system", "direct", "manual"):
                return "不支持的代理模式"
            address = validate_proxy_url(address) if mode == "manual" else ""
        except ValueError as exc:
            return str(exc)
        previous = self.proxy_mode, self.proxy_url
        if previous == (mode, address):
            return None
        self.proxy_mode, self.proxy_url = mode, address
        if not self._save():
            self.proxy_mode, self.proxy_url = previous
            return "无法保存代理设置"
        set_proxy(mode, address)
        return None

    def _refresh_cache_settings(self):
        if self._settings_dialog is None:
            return
        try:
            info = self.proxy.cache_info()
        except OSError:
            info = None
        self._settings_dialog.refresh_cache(info, self._clearing_audio_cache)

    def clear_audio_cache(self):
        if self._clearing_audio_cache:
            return
        answer = QMessageBox.question(
            self._dialog_host(), self.tr("清除音频缓存"),
            self.tr("清除已下载的音频缓存？音乐库和封面会保留；正在使用的缓存将在切歌或退出后清理。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._clearing_audio_cache = True
        self._refresh_cache_settings()

        def done(result):
            self._clearing_audio_cache = False
            self._refresh_cache_settings()
            self._status("已清除 {size} MiB 音频缓存 · 待释放 {pending} MiB",
                         size=f"{result['removed_bytes'] / 1024 ** 2:.1f}",
                         pending=f"{result['pending_bytes'] / 1024 ** 2:.1f}")
        def failed(message):
            self._clearing_audio_cache = False
            self._refresh_cache_settings()
            self._error(self.tr("清除音频缓存失败：{message}", message=message))
        self._submit(self.proxy.clear_cache, done, failed)

    def export_selection(self):
        entries = self._selected_library_entries()
        if not self._selection_mode or not entries:
            return
        dialog = QFileDialog(self, self.tr("批量导出"), "bili-music-library.zip", self.tr("音乐库 ZIP 包 (*.zip)"))
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("zip")
        include_downloads = QCheckBox(self.tr("包含已下载的音频"))
        include_downloads.setToolTip(self.tr("本地导入的音频和所有封面始终包含在 ZIP 中。"))
        layout = dialog.layout()
        layout.addWidget(include_downloads, layout.rowCount(), 0, 1, layout.columnCount())
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.export_selection_path(Path(dialog.selectedFiles()[0]), [entry.id for entry in entries],
                                           include_downloads=include_downloads.isChecked(), background=True)
        finally:
            dialog.deleteLater()

    def export_selection_path(self, path: Path, identities: list[str], *, include_downloads=False, background=False):
        selected = set(identities)
        identities = [entry.id for entry in self.entries if entry.id in selected]
        if not identities:
            return False
        if path.resolve() == self.store.path.resolve():
            self._error(self.tr("请选择其他路径，不能覆盖播放器正在使用的音乐库。"))
            return False
        entries = deepcopy(self.entries) if background else self.entries

        def write():
            write_library_package(path, entries, identities, covers=self.covers,
                                  audio_files=self.audio_files, include_downloads=include_downloads)

        def done(_):
            self._status("已导出所选 {count} 项", count=len(identities))

        def failed(message):
            self._error(self.tr("导出失败：{message}", message=translate_error(message, self.language)))

        if background:
            self._status("正在导出音乐库…")
            self._submit(write, done, failed)
        else:
            try:
                write()
            except (ValueError, OSError) as exc:
                failed(str(exc))
                return False
            done(None)
        return True

    def import_library(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, self.tr("导入音乐库"), "", self.tr("音乐库 ZIP 包 (*.zip)"),
        )
        if filename:
            self.import_library_path(Path(filename), background=True)

    def import_library_path(self, path: Path, *, background=False):
        def read():
            return read_library_package(path, self.covers, self.audio_files)

        def failed(message):
            self._error(self.tr("导入失败：{message}", message=translate_error(message, self.language)))

        if background:
            self._status("正在导入音乐库…")
            self._submit(read, self._commit_library_import, failed)
            return True
        try:
            entries = read()
        except (ValueError, OSError) as exc:
            failed(str(exc))
            return False
        return self._commit_library_import(entries)

    def _commit_library_import(self, entries):
        if not entries:
            self._status("音乐库包中没有可导入的曲目")
            return True
        original = self.entries
        combined = deepcopy(original)
        # A ZIP can replace an offline copy that has disappeared from disk.
        incoming_ids = {entry.id for entry in entries if entry.kind == "single" and entry.tracks
                        and entry.tracks[0].audio_file}
        for entry in combined:
            if entry.id in incoming_ids and entry.kind == "single" and entry.tracks:
                track = entry.tracks[0]
                if track.provider != "local" and track.audio_file and self.audio_files.path(track.audio_file) is None:
                    track.audio_file = ""
        merge_libraries(combined, entries)
        self.entries = combined
        if not self._save():
            self.entries = original
            return False
        self._render_library()
        self.libraryChanged.emit()
        self._status("已导入 {count} 项，已有曲目保留", count=len(entries))
        self._read_local_audio_durations()
        return True

    def import_list(self):
        dialog = ListImportDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.import_result is not None:
            self.commit_list_import(dialog.import_result)

    def commit_list_import(self, result):
        album = replace(result.album, tracks=list(result.album.tracks))
        for song in result.songs:
            if song.tracks:
                song.tracks[0].id = song.id
                song.tracks[0].song_id = song.id
        self.entries.append(album)
        self.entries.extend(result.songs)
        self._normalize_library()
        self._render_library()
        if not self._save():
            return
        self._status("已导入列表专辑 {title} · {count} 首 · 跳过 {skipped} 个视频",
                     title=album.title, count=len(album.tracks), skipped=len(result.skipped))
        if result.skipped:
            message = QMessageBox(self)
            message.setWindowTitle(self.tr("列表导入完成"))
            message.setIcon(QMessageBox.Icon.Information)
            message.setText(self.tr("专辑已保存，跳过 {count} 个不可用视频。", count=len(result.skipped)))
            message.setDetailedText("\n".join(translate_error(reason, self.language) for reason in result.skipped))
            message.exec()

    def add_entry(self):
        dialog = EntryDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.import_entry(dialog.value())

    def import_local_audio(self):
        patterns = " ".join(f"*.{extension}" for extension in AUDIO_EXTENSIONS)
        filenames, _ = QFileDialog.getOpenFileNames(
            self, self.tr("导入本地音乐"), "", self.tr("音频文件 ({patterns})", patterns=patterns),
        )
        if filenames:
            self.import_local_audio_paths([Path(name) for name in filenames])

    def import_local_audio_paths(self, paths: list[Path]):
        def load():
            songs, errors = [], []
            for path in paths:
                try:
                    audio_file = self.audio_files.import_file(path)
                except (OSError, ValueError) as exc:
                    errors.append(f"{path.name} · {translate_error(str(exc), self.language)}")
                    continue
                identifier = uuid4().hex
                title = path.stem
                track = Track(title, identifier, 0, 1, provider="local", audio_file=audio_file)
                songs.append(LibraryEntry(title, identifier, tracks=[track], resolved=True, provider="local"))
            return songs, errors

        def done(result):
            songs, errors = result
            if songs:
                original = self.entries
                self.entries = [*original, *songs]
                if self._save():
                    self._render_library()
                    self._status("已导入 {count} 首本地歌曲", count=len(songs))
                    self._read_local_audio_durations()
                else:
                    self.entries = original
            if errors:
                self._error(self.tr("部分音频文件导入失败：{message}", message="\n".join(errors)))

        self._status("正在导入本地音乐…")
        self._submit(load, done)

    def _read_local_audio_durations(self):
        tracks = [song.tracks[0] for song in self.entries if song.kind == "single" and song.tracks]
        tracks.extend(self._tracks())
        for name in dict.fromkeys(track.audio_file for track in tracks if not track.duration and track.audio_file):
            path = self.audio_files.path(name)
            if path is not None:
                self._audio_duration_reader.add(path)

    def _save_local_audio_duration(self, name: str, seconds: int):
        updated = False
        songs = {}
        for entry in self.entries:
            if entry.kind == "single" and entry.tracks and entry.tracks[0].audio_file == name:
                track = entry.tracks[0]
                songs[entry.id] = track
                if not track.duration:
                    track.duration = seconds
                    updated = True
        for track in self._tracks():
            if track.duration:
                continue
            canonical = songs.get(track.song_id or track.id)
            if track.audio_file == name or (
                canonical is not None and self._audio_cache_identity(canonical)[1] == self._audio_cache_identity(track)[1]
            ):
                track.duration = seconds
                updated = True
        if updated:
            self.save_timer.start(350)
            self.libraryChanged.emit()

    def local_audio_path(self, track: Track) -> Path | None:
        song = find_song(self.entries, track.song_id or track.id)
        if song and song.tracks:
            canonical = song.tracks[0]
            if self._audio_cache_identity(canonical)[1] == self._audio_cache_identity(track)[1]:
                return self.audio_files.path(canonical.audio_file)
        return self.audio_files.path(track.audio_file)

    def audio_download_pending(self, entry: LibraryEntry) -> bool:
        return entry.id in self._audio_downloads

    def download_audio(self, entry: LibraryEntry):
        current = self._find(entry.id)
        if (current is None or current.kind != "single" or current.provider == "local"
                or not current.tracks or self.audio_download_pending(current)):
            return
        track = replace(current.tracks[0])
        if self.local_audio_path(track) is not None:
            return
        identity = current.id
        cache_key, source_key = self._audio_cache_identity(track)
        self._audio_downloads.add(identity)
        self._status("正在下载 {title}…", title=current.title)
        self.libraryChanged.emit()

        def load():
            if track.provider == "youtube":
                try:
                    cached = self.proxy.save_cached_audio(cache_key, source_key, self.audio_files.store_stream)
                except ValueError:
                    cached = None
                if cached is not None:
                    return cached
            client = self._source_client(track)
            url = client.get_stream(track, audio_only=True) if track.provider == "bilibili" else client.get_stream(track)
            return self.audio_files.download(url)

        def done(audio_file):
            self._audio_downloads.discard(identity)
            song = self._find(identity)
            if song and song.tracks and self._audio_cache_identity(song.tracks[0])[1] == source_key:
                canonical = song.tracks[0]
                previous = canonical.audio_file
                canonical.audio_file = audio_file
                if self._save():
                    self._status("已下载 {title}，可以离线播放", title=song.title)
                    self._read_local_audio_durations()
                else:
                    canonical.audio_file = previous
            self.libraryChanged.emit()

        def failed(message):
            self._audio_downloads.discard(identity)
            self.libraryChanged.emit()
            self._error(self.tr("下载音频失败：{message}", message=translate_error(message, self.language)))

        self._submit(load, done, failed)

    def _source_client(self, entry):
        if entry.provider == "youtube":
            return YouTubeClient(use_chrome=self.youtube_use_chrome and not self.youtube_use_cookie_file,
                                 cookie_file=self.youtube_cookie_file if self.youtube_use_cookie_file else None)
        return BilibiliClient()

    def import_entry(self, entry: LibraryEntry):
        self._status("正在导入 {title}…", title=entry.title or entry.bvid)

        def done(info):
            custom_title = entry.title and entry.title != entry.bvid
            title = entry.title if custom_title else (info.title if entry.kind == "album" else info.tracks[0].title)
            tracks = copy_tracks(info.tracks)
            if entry.kind == "single":
                if custom_title:
                    tracks[0].title = entry.title
                tracks[0].artist = entry.artist
                tracks[0].date = entry.date
            imported = replace(entry, title=title, cover=info.cover, tracks=tracks, resolved=True)
            self.entries.append(imported)
            self._normalize_library()
            self._render_library()
            self._save()
            self._status("已导入 {title}", title=imported.title)

        self._submit(lambda: self._source_client(entry).get_video(entry), done)

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

    def _normalize_library(self):
        normalize_library(self.entries)

    def song_for_track(self, track: Track, album=None):
        song = find_song(self.entries, track.song_id or track.id)
        if song is None:
            self._normalize_library()
            song = find_song(self.entries, track.song_id or track.id)
        return song

    def _find(self, identity: str):
        direct = next((entry for entry in self.entries if entry.id == identity), None)
        return direct or find_song(self.entries, identity)

    def _ensure_entry(self, entry: LibraryEntry, done, failed=None):
        if entry.resolved or entry.provider == "local":
            done(entry)
            return
        source = (entry.provider, entry.bvid, entry.page, entry.kind)
        was_present = self._find(entry.id) is not None
        self._status("正在读取 {title}…", title=entry.title)

        def loaded(info):
            current = self._find(entry.id)
            if current is None:
                if was_present:
                    return
                current = entry
            if source != (current.provider, current.bvid, current.page, current.kind):
                return
            if not current.resolved:
                tracks = copy_tracks(info.tracks)
                if current.kind == "single":
                    if current.title and current.title != current.bvid:
                        tracks[0].title = current.title
                    tracks[0].artist = current.artist
                    tracks[0].date = current.date
                current.tracks = tracks
                current.cover = current.cover or info.cover
                current.resolved = True
                if not current.title or current.title == current.bvid:
                    current.title = info.title if current.kind == "album" else tracks[0].title
                if was_present:
                    self._normalize_library()
                    current = self._find(current.id) or current
                    self._save()
                    self._render_library()
            done(current)

        self._submit(lambda: self._source_client(entry).get_video(entry), loaded, failed)

    def _dialog_host(self):
        dialog = QApplication.activeModalWidget()
        ancestor = dialog
        while ancestor is not None and ancestor is not self:
            ancestor = ancestor.parentWidget()
        return dialog if ancestor is self else self

    def open_entry(self, entry: LibraryEntry):
        if entry is None:
            return

        def opened(current):
            host = self._dialog_host()
            dialog = (EntryDetailsDialog(self, current) if host is self else
                      EntryDetailsDialog(host, current, owner=self))
            try:
                dialog.exec()
            finally:
                if not sip.isdeleted(dialog):
                    dialog.deleteLater()

        self._ensure_entry(entry, opened)

    def open_track_details(self, track: Track, album: LibraryEntry):
        song = self.song_for_track(track, album)
        if song is not None:
            self.open_entry(song)

    def edit_album_track(self, album_id: str, track_id: str):
        album = self._find(album_id)
        if album is None or album.kind != "album":
            return
        track = next((t for t in album.tracks if t.id == track_id or t.song_id == track_id), None)
        song = self.song_for_track(track, album) if track is not None else None
        if song is not None:
            self.edit_single(song)

    def open_album(self, entry: LibraryEntry):
        def opened(album):
            if album.id in self._editing_albums:
                return
            self._editing_albums.add(album.id)
            try:
                dialog = AlbumDialog(self._dialog_host(), album, [e for e in self.entries if e.kind == "single"])
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
            known = {t.id for t in target.tracks}
            target.tracks.extend(t for t in tracks if t.id not in known)
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
        current = self._find(album.id)
        members = []
        for track in album.tracks:
            song = self.song_for_track(track, album)
            members.append(song.tracks[0] if song and song.tracks else track)
        current.title, current.cover = album.title, album.cover
        current.artist, current.date = album.artist, album.date
        current.tracks = members
        current.resolved = True
        self._normalize_library()
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
                tracks = list(source.tracks)
                if target.id in self._editing_albums:
                    self._deferred_album_additions.setdefault(target.id, []).append((tracks, source.cover, source.title))
                    return
                known = {t.id for t in target.tracks}
                target.tracks.extend(t for t in tracks if t.id not in known)
                if not target.cover:
                    target.cover = source.cover
                self._normalize_library()
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
        chosen, accepted = QInputDialog.getItem(self._dialog_host(), self.tr("加入专辑"), self.tr("请选择专辑"), choices, 0, False)
        if accepted:
            self.add_single_to_album(entry, albums[choices.index(chosen)])

    def _commit_song_edit(self, current, updated):
        song = self._find(current.id)
        if song is None or song.kind != "single":
            return
        canonical = song.tracks[0] if song.tracks else None
        if updated.tracks:
            candidate = updated.tracks[0]
            if canonical is None:
                canonical = replace(candidate, id=song.id, song_id=song.id)
            else:
                old_source = self._audio_cache_identity(canonical)[1]
                canonical.provider = candidate.provider
                canonical.bvid, canonical.cid, canonical.page = candidate.bvid, candidate.cid, candidate.page
                if old_source != self._audio_cache_identity(candidate)[1]:
                    canonical.audio_file = candidate.audio_file
                canonical.duration = candidate.duration
            canonical.title, canonical.artist, canonical.date = updated.title, updated.artist, updated.date
            canonical.id = canonical.song_id = song.id
            song.tracks = [canonical]
        song.provider = updated.provider
        song.title, song.bvid, song.page = updated.title, updated.bvid, updated.page
        song.artist, song.date, song.cover = updated.artist, updated.date, updated.cover
        song.resolved = updated.resolved
        self._normalize_library()
        self._sync_song_queue(song)
        self._render_library()
        self._save()
        self.libraryChanged.emit()

    def _sync_song_queue(self, song):
        if not song.tracks:
            return
        canonical = song.tracks[0]
        for index in range(self.queue.count()):
            item = self.queue.item(index)
            queued = item.data(Qt.ItemDataRole.UserRole)
            if queued.song_id == song.id:
                queued = replace(canonical, id=queued.id, song_id=song.id)
                self._set_queue_item_track(item, queued)
                if queued.id == self.current_id:
                    self.now_title.setText(song.title)
                    self.now_meta.setText(queued.artist.strip())
                    self._sync_mini_text()
                    self._update_current_cover(queued)

    def edit_single(self, entry: LibraryEntry):
        self._normalize_library()
        current = self._find(entry.id)
        if current is None or current.kind != "single":
            return
        dialog = EntryDialog(self._dialog_host(), current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.value()
        if updated.cover and updated.cover != current.cover and not updated.cover.startswith(("http://", "https://")):
            try:
                updated.cover = str(self.covers.fetch(updated.cover))
            except (OSError, ValueError) as exc:
                self._error(str(exc))
                return
        revision = self._song_edit_requests.get(current.id, 0) + 1
        self._song_edit_requests[current.id] = revision
        updated.kind = "single"
        if (updated.provider, updated.bvid, updated.page) != (current.provider, current.bvid, current.page):
            # Validate the replacement before changing the shared song entity.
            def loaded(info):
                if self._song_edit_requests.get(current.id) != revision:
                    return
                updated.tracks = info.tracks
                updated.cover = updated.cover or info.cover
                updated.resolved = True
                self._commit_song_edit(current, updated)
            self._submit(lambda: self._source_client(updated).get_video(updated), loaded)
        else:
            updated.tracks = list(current.tracks)
            self._commit_song_edit(current, updated)

    def delete_entry(self, entry: LibraryEntry):
        return self.delete_library_entries([entry.id])

    def _ask_album_deletion(self, albums):
        message = QMessageBox(self)
        message.setWindowTitle(self.tr("删除专辑"))
        message.setIcon(QMessageBox.Icon.Question)
        message.setText(self.tr("删除 {count} 个专辑时，如何处理其中的单曲？", count=len(albums)))
        message.setInformativeText(self.tr("保留单曲会将它们移出所选专辑，未属于其他专辑的歌曲会成为无专辑单曲。一并删除会从音乐库及其他专辑中删除这些实体。"))
        keep = message.addButton(self.tr("保留单曲"), QMessageBox.ButtonRole.AcceptRole)
        remove = message.addButton(self.tr("一并删除单曲"), QMessageBox.ButtonRole.DestructiveRole)
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.setDefaultButton(keep)
        message.exec()
        if message.clickedButton() == keep:
            return "keep"
        if message.clickedButton() == remove:
            return "delete"
        return None

    def delete_library_entries(self, identities, album_contents=None):
        self._normalize_library()
        selected = [entry for entry in self.entries if entry.id in set(identities)]
        albums = [entry for entry in selected if entry.kind == "album"]
        if albums and album_contents is None:
            album_contents = self._ask_album_deletion(albums)
            if album_contents is None:
                return False
        if album_contents not in (None, "keep", "delete"):
            return False
        song_ids = {entry.id for entry in selected if entry.kind == "single"}
        if album_contents == "delete":
            song_ids.update(track.song_id or track.id for album in albums for track in album.tracks)
        for identity in song_ids:
            delete_song(self.entries, identity)
        album_ids = {entry.id for entry in albums}
        self.entries[:] = [entry for entry in self.entries if entry.id not in album_ids]
        # Queue occurrences are snapshots: unlink deleted entities to avoid future cross-updates.
        for index in range(self.queue.count()):
            item = self.queue.item(index)
            track = item.data(Qt.ItemDataRole.UserRole)
            if track.song_id in song_ids:
                track.song_id = ""
                item.setData(Qt.ItemDataRole.UserRole, track)
        self._render_library()
        self._save()
        self.libraryChanged.emit()
        return True

    def _build_entry_menu(self, entry, *, parent=None, select=None):
        menu = QMenu(self if parent is None else parent)
        select = self.enter_library_selection if select is None else select

        def action(key, text, callback):
            item = menu.addAction(self.tr(text))
            item.setData(key)
            item.triggered.connect(callback)
            return item

        action("play", "播放", lambda: self.resolve_entry(entry, True))
        action("queue", "加入播放列表", lambda: self.resolve_entry(entry, False))
        menu.addSeparator()
        action("details", "打开专辑" if entry.kind == "album" else "打开单曲详情", lambda: self.open_entry(entry))
        action("edit", "编辑专辑" if entry.kind == "album" else "编辑", lambda: self.open_album(entry) if entry.kind == "album" else self.edit_single(entry))
        action("select", "多选", lambda: select(entry.id))
        action("copy", "复制", lambda: self.copy_library_entries([entry.id]))
        if entry.kind == "single":
            action("add_album", "加入专辑…", lambda: self._choose_album(entry))
        if entry.provider != "local":
            web = action("source", "在 YouTube 打开" if entry.provider == "youtube" else "在 B站打开",
                         lambda: QDesktopServices.openUrl(QUrl(entry.video_url)))
            web.setEnabled(bool(entry.bvid))
        menu.addSeparator()
        action("delete", "从音乐库删除", lambda: self.delete_entry(entry))
        return menu

    def _entry_menu(self, entry, position=None):
        if entry.id in self._selected_entry_ids and len(self._selected_entry_ids) > 1:
            self.show_library_batch_menu(position)
            return
        menu = self._build_entry_menu(entry)
        try:
            menu.exec(position if position is not None else self.cursor().pos())
        finally:
            menu.deleteLater()

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
            self._entry_play_request += 1
        generation, epoch = self._entry_play_request, self._queue_epoch

        def done(snapshot):
            if epoch != self._queue_epoch or (play and generation != self._entry_play_request):
                return
            if not snapshot.tracks:
                self._status("这个专辑还没有歌曲，先添加几首吧。")
                return
            if play:
                self.play_tracks(snapshot.tracks)
                return
            count = self._append_unique_tracks(snapshot.tracks)
            if count:
                self._status("已加入队列 · {count} 首", count=count)
            else:
                self._status("这些歌曲已在播放队列中")

        def failed(message):
            if epoch != self._queue_epoch:
                return
            if (play and generation == self._entry_play_request) or (not play and request == self._library_request):
                self._error(message)

        self._ensure_entry(entry, done, failed)

    def play_tracks(self, tracks: list[Track]):
        if not tracks:
            return
        queued = copy_tracks(tracks)
        self._discard_queue_items()
        self._append_tracks(queued)
        self.play_item(self.queue.item(0))

    def _append_unique_tracks(self, tracks):
        queued_ids = {track.song_id or track.id for track in self._tracks()}
        missing = []
        for track in tracks:
            identity = track.song_id or track.id
            if identity not in queued_ids:
                queued_ids.add(identity)
                missing.append(track)
        if missing:
            self._append_tracks(copy_tracks(missing))
        return len(missing)

    @staticmethod
    def _set_queue_item_track(item: QListWidgetItem, track: Track):
        artist = track.artist.strip()
        text = f"{track.title}\n{artist}" if artist else track.title
        item.setData(Qt.ItemDataRole.UserRole, track)
        item.setText(text)
        item.setToolTip(text)

    def _append_tracks(self, tracks: list[Track], persist=True):
        for track in tracks:
            item = QListWidgetItem()
            self._set_queue_item_track(item, track)
            self.queue.addItem(item)
        self._queue_changed(persist=persist)

    def insert_track_next(self, track: Track) -> QListWidgetItem:
        queued = replace(track, id=str(uuid4()), song_id=track.song_id or track.id)
        item = QListWidgetItem()
        self._set_queue_item_track(item, queued)
        row = self._current_row()
        self.queue.insertItem(row + 1 if row >= 0 else self.queue.count(), item)
        self._play_next_priority.insert(0, queued.id)
        self._queue_changed()
        return item

    def _tracks(self) -> list[Track]:
        return [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue.count())]

    def _current_row(self) -> int:
        return next((i for i, t in enumerate(self._tracks()) if t.id == self.current_id), -1)

    def _queue_changed(self, *_, persist=True):
        self.queue_title.setText(self.tr("播放队列 · {count}", count=self.queue.count()))
        self.mini_queue_title.setText(self.queue_title.text())
        self.queue_empty.setVisible(self.queue.count() == 0)
        self.mini_queue_empty.setVisible(self.queue.count() == 0)
        if persist:
            self._save()

    def play_item(self, item: QListWidgetItem | None):
        if item is None:
            return
        track = item.data(Qt.ItemDataRole.UserRole)
        self._entry_play_request += 1
        self._reset_playback_recovery()
        self._want_playing = True
        self._loading_stream = True
        self._request += 1
        self.player.stop()
        self.player.setSource(QUrl())
        self.proxy.release_stream()
        self.current_id = track.id
        self._play_next_priority = [identity for identity in self._play_next_priority if identity != track.id]
        self.shuffle_order.record(track.id)
        self.queue.setCurrentItem(item)
        self.now_title.setText(track.title)
        self.now_meta.setText(track.artist.strip())
        self._sync_mini_text()
        self._update_current_cover(track)
        self._load_stream(track)

    def _reset_playback_recovery(self):
        self._want_playing = False
        self._loading_stream = False
        self._resume_position = None
        self._last_played_position = 0
        self._reconnect_attempts = 0
        self._last_progress_at = time.monotonic()

    @staticmethod
    def _audio_cache_identity(track):
        return track.song_id or track.id, f"{track.provider}:{track.bvid}:{track.cid}:{track.page}:best-audio-v1"

    def _load_stream(self, track):
        request = self._request
        cache_key, source_key = self._audio_cache_identity(track)
        self._loading_stream = True
        self._last_progress_at = time.monotonic()

        def ready(url, status):
            self._loading_stream = False
            self.player.setSource(QUrl(url))
            self._last_progress_at = time.monotonic()
            QTimer.singleShot(0, self._resume_stream)
            if self._want_playing:
                self.player.play()
                self._status(status)

        def done(url):
            if request != self._request or track.id != self.current_id:
                return
            ready(self.proxy.set_stream(url, cache_key=cache_key, source_key=source_key), "正在缓冲音频…")

        def failed(message):
            if request != self._request or track.id != self.current_id:
                return
            self._loading_stream = False
            if self._reconnect_attempts:
                self._recover_stream()
            else:
                self._want_playing = False
                self._error(message)

        local = self.local_audio_path(track)
        if local is not None:
            self.proxy.release_stream()
            ready(QUrl.fromLocalFile(str(local)), "正在播放本地音频")
            return
        if track.provider == "local":
            failed(self.tr("找不到本地音频文件"))
            return
        if not self._reconnect_attempts:
            self._status("正在获取音频地址…")
        try:
            cached_url = self.proxy.get_cached_stream(cache_key, source_key)
        except OSError:
            cached_url = None
        if cached_url is not None:
            ready(cached_url, "正在播放缓存音频")
            return
        self._submit(lambda: self._source_client(track).get_stream(track), done, failed)

    def _resume_stream(self):
        if self._resume_position is not None and self.player.isSeekable():
            position = self._resume_position
            self._resume_position = None
            self.player.setPosition(position)
            self._last_progress_at = time.monotonic()

    def _recover_stream(self):
        row = self._current_row()
        if self._closing or not self._want_playing or row < 0 or self._loading_stream:
            return
        if self.player.source().isLocalFile():
            self._want_playing = False
            self.player.pause()
            self._status("本地音频播放中断")
            return
        if self._reconnect_attempts >= 3:
            self._want_playing = False
            self.player.pause()
            self._status("音频重连失败，请检查网络后点击播放重试。")
            return
        self._reconnect_attempts += 1
        if self._resume_position is None:
            self._resume_position = self._last_played_position
        self._request += 1
        self._loading_stream = True
        self.player.stop()
        self.player.setSource(QUrl())
        self._status("播放中断，正在重新连接（{attempt}/3）…", attempt=self._reconnect_attempts)
        self._load_stream(self.queue.item(row).data(Qt.ItemDataRole.UserRole))

    def _check_playback_progress(self):
        if self._closing or not self._want_playing or not self.current_id or self._loading_stream:
            return
        if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            return
        last_progress = max(self._last_progress_at, self.proxy.last_progress_time())
        if time.monotonic() - last_progress >= 20:
            self._recover_stream()

    def toggle_play(self):
        if self._want_playing or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._want_playing = False
            self.player.pause()
        elif self.current_id and (self._resume_position is not None or self._reconnect_attempts >= 3):
            self._want_playing = True
            self._reconnect_attempts = 0
            self._recover_stream()
        elif self.current_id and self._loading_stream:
            self._want_playing = True
        elif self.current_id and self.player.source().isValid() and self.player.mediaStatus() != QMediaPlayer.MediaStatus.InvalidMedia:
            self._want_playing = True
            self._last_progress_at = time.monotonic()
            self.player.play()
        else:
            self.play_item(self.queue.currentItem() or self.queue.item(0))

    def _queue_item_for_id(self, identity):
        return next((self.queue.item(i) for i in range(self.queue.count())
                     if self.queue.item(i).data(Qt.ItemDataRole.UserRole).id == identity), None)

    def previous(self):
        if self.mode.currentIndex() == 3:
            identity = self.shuffle_order.previous([t.id for t in self._tracks()], self.current_id)
            self.play_item(self._queue_item_for_id(identity))
        else:
            self.play_item(self.queue.item(max(0, self._current_row() - 1)))

    def next(self, _checked=False, automatic=False):
        count = self.queue.count()
        if not count:
            return
        row = self._current_row()
        if self.mode.currentIndex() == 3:
            while self._play_next_priority:
                item = self._queue_item_for_id(self._play_next_priority.pop(0))
                if item is not None:
                    self.play_item(item)
                    return
            identity = self.shuffle_order.next([t.id for t in self._tracks()], self.current_id)
            self.play_item(self._queue_item_for_id(identity))
            return
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

    def _discard_queue_items(self):
        self._play_next_priority.clear()
        self.shuffle_order.reset()
        self._library_request += 1
        self._queue_epoch += 1
        self.current_id = None
        self.queue.clear()

    def clear_queue(self):
        self._reset_playback_recovery()
        self._request += 1
        self._discard_queue_items()
        self.player.stop()
        self.player.setSource(QUrl())
        self.proxy.release_stream()
        self.now_title.setText(self.tr("还没有正在播放的音乐"))
        self.now_meta.setText(self.tr("从音乐库点击播放"))
        self._sync_mini_text()
        self._update_current_cover()
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
            selected = self.queue.selectedItems() if item.isSelected() else [item]
            self.remove_queue_items(selected)
        menu.deleteLater()

    def remove_queue_items(self, items):
        if not items:
            return
        rows = sorted({self.queue.row(item) for item in items if self.queue.row(item) >= 0})
        if not rows:
            return
        is_current = any(self.queue.item(row).data(Qt.ItemDataRole.UserRole).id == self.current_id for row in rows)
        for row in reversed(rows):
            self.queue.takeItem(row)
        if is_current:
            self._reset_playback_recovery()
            self._request += 1
            self.player.stop()
            self.player.setSource(QUrl())
            self.proxy.release_stream()
            self.current_id = None
            if self.queue.count():
                self.play_item(self.queue.item(min(rows[0], self.queue.count() - 1)))
            else:
                self.now_title.setText(self.tr("还没有正在播放的音乐"))
                self.now_meta.setText(self.tr("从音乐库点击播放"))
                self._sync_mini_text()
                self._update_current_cover()
        self._queue_changed()

    def _seek_to(self, position: int):
        position = max(0, min(position, self.seek.maximum()))
        if self._resume_position is not None:
            self._resume_position = position
        self._last_played_position = position
        self._last_progress_at = time.monotonic()
        self.player.setPosition(position)
        self._position(position)

    def _position(self, position):
        if not self._loading_stream and self._resume_position is None and position > 0:
            if position != self._last_played_position:
                self._last_progress_at = time.monotonic()
            self._last_played_position = position
        self.mini_lyrics.set_position(position)
        for slider, elapsed in ((self.seek, self.elapsed), (self.mini_seek, self.mini_elapsed)):
            if not slider.isSliderDown():
                slider.setValue(position)
                elapsed.setText(timestamp(position))

    def _duration(self, duration):
        for slider, total in ((self.seek, self.total), (self.mini_seek, self.mini_total)):
            slider.setRange(0, duration)
            total.setText(timestamp(duration))

    def _playback_state(self, state):
        self.mini_cover.set_playing(state == QMediaPlayer.PlaybackState.PlayingState)
        for widget in (self.play_button, self.mini_play):
            widget.setIcon(media_icon(
                "pause" if state == QMediaPlayer.PlaybackState.PlayingState else "play", self.theme, primary=True,
            ))
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._status("正在播放")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._status("已暂停")

    def _media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            QTimer.singleShot(0, self._resume_stream)
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            duration = self.player.duration()
            if self._want_playing and duration > 0 and self._last_played_position + 2000 < duration:
                self._recover_stream()
                return
            self._want_playing = False
            self.next(automatic=True)

    def _media_error(self, error, message):
        if error != QMediaPlayer.Error.NoError and self.current_id:
            if self.player.source().isLocalFile():
                self._want_playing = False
                self._status("本地音频播放失败：{message}", message=message)
            elif self._want_playing:
                self._recover_stream()
            else:
                self._status("播放失败：{message}。可重新双击歌曲获取地址。", message=message)

    def _volume(self, value):
        self.audio.setVolume(value / 100)
        self.volume.setToolTip(f"{value}%")
        if hasattr(self, "save_timer"):
            self.save_timer.start(350)

    def _save(self):
        try:
            self._normalize_library()
            self.store.save(self.entries, self._tracks(), self.volume.value(),
                            {"theme": self.theme, "language": self.language, "card_scale": self.card_scale,
                             "play_mode": self.mode.currentIndex(), "audio_output_id": self.audio_output_id,
                             "mini_cover_style": self.mini_cover_style, "proxy_mode": self.proxy_mode,
                             "proxy_url": self.proxy_url, "youtube_use_chrome": self.youtube_use_chrome,
                             "youtube_use_cookie_file": self.youtube_use_cookie_file,
                             "youtube_cookie_file": self.youtube_cookie_file})
            return True
        except (ValueError, OSError) as exc:
            self._status("保存失败：{message}", message=translate_error(str(exc), self.language))
            if not self._save_error_shown:
                self._save_error_shown = True
                self._error(self.tr("无法保存音乐库：{message}", message=str(exc)))
            return False

    def _error(self, message: str):
        message = translate_error(message, self.language)
        self.status.setText(message.replace("\n", " "))
        QMessageBox.warning(self._dialog_host(), "Bili Music", message)

    def closeEvent(self, event):
        if not self._quitting and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            self.save_timer.stop()
            self._save()
            self.hide()
            event.ignore()
            return
        self._closing = True
        self._audio_duration_reader.stop()
        self._frameless_controller.dispose()
        self._set_mini_queue_open(False)
        self.playback_watchdog.stop()
        self._reset_playback_recovery()
        self.tray.hide()
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

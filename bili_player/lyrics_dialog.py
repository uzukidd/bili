"""Search LRCLIB and preview a candidate before saving its full lyrics."""
from __future__ import annotations

from collections import deque
from functools import partial
from itertools import islice
from math import ceil
from threading import Event
from time import monotonic
from weakref import ref

from PyQt6 import sip
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from .i18n import translate
from .lyrics import LRCLIBClient, LRCLIBResult, LyricsCooldownError, LyricsEntry, LyricsError
from .window_chrome import FramelessController, TitleBar


_DEFAULT_CLIENT = LRCLIBClient()  # Keep the server's cooldown when a picker is reopened.


class LyricsSearchDialog(QDialog):
    """Use the owner's worker for at most one active search or preview request."""

    DEBOUNCE_MS = 400
    PREVIEW_INTERVAL_MS = 350
    RESULT_LIMIT = 20

    def __init__(self, parent, title: str, *, submit, language=None, client=None):
        super().__init__(parent)
        self._language = language or getattr(parent, "language", "zh_CN") or "zh_CN"
        self._submit = submit
        self._client = client if client is not None else _DEFAULT_CLIENT
        self._closed = Event()
        self._cancelled = Event()
        self._in_flight = False
        self._search: tuple[str, int] | None = None
        self._previews: deque[tuple[int, int]] = deque()
        self._rows: dict[int, tuple[LRCLIBResult, QListWidgetItem]] = {}
        self._loaded: dict[int, LyricsEntry] = {}
        self._cooldown_until = 0.0
        self.selected_lyrics: LyricsEntry | None = None
        closed = self._closed
        self.destroyed.connect(lambda *_: closed.set())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dispatch)

        self.setObjectName("root")
        self.setWindowTitle(self.tr("搜索 LRCLIB 歌词"))
        self._frameless_controller = FramelessController(self)
        self.setMinimumSize(460, 350)
        self.resize(600, 525)
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(1, 1, 1, 1)
        window_layout.setSpacing(0)
        window_layout.addWidget(TitleBar(self, self._language))
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 8, 20, 16)
        layout.setSpacing(10)
        window_layout.addLayout(layout, 1)

        search_row = QHBoxLayout()
        self.query_input = QLineEdit(title)
        self.query_input.setPlaceholderText(self.tr("输入歌曲名称"))
        self.query_input.setAccessibleName(self.query_input.placeholderText())
        self.query_input.setClearButtonEnabled(True)
        self.query_input.textChanged.connect(self._query_changed)
        self.query_input.returnPressed.connect(self._search_now)
        self.search_button = self._button("搜索", self._search_now)
        search_row.addWidget(self.query_input, 1)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.results_list = QListWidget()
        self.results_list.setAccessibleName(self.tr("搜索 LRCLIB 歌词"))
        self.results_list.setWordWrap(True)
        self.results_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list.currentItemChanged.connect(self._update_confirm)
        layout.addWidget(self.results_list, 1)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self._button("取消", self.reject))
        self.confirm_button = self._button("保存歌词", self.accept)
        self.confirm_button.setObjectName("primary")
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)
        self.query_input.setFocus()
        self._queue_query(self.DEBOUNCE_MS)

    def tr(self, text: str, **values) -> str:
        return translate(text, self._language, **values)

    def _button(self, text, callback):
        button = QPushButton(self.tr(text))
        button.setAutoDefault(False)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _query_changed(self, _text):
        self._queue_query(self.DEBOUNCE_MS)

    def _search_now(self):
        self._queue_query(0)

    def _queue_query(self, delay):
        self._cancelled.set()
        self._cancelled = Event()
        self._timer.stop()
        self._previews.clear()
        self._rows.clear()
        self._loaded.clear()
        self.results_list.clear()
        self._update_confirm()
        query = self.query_input.text().strip()
        self._search = (query, 0) if query else None
        self.search_button.setEnabled(bool(query))
        self.status_label.setText(self.tr("正在搜索歌词…") if query else "")
        if query:
            self._timer.start(delay)

    def _dispatch(self):
        if self._closed.is_set() or self._in_flight or not (self._search or self._previews):
            return
        remaining = self._cooldown_until - monotonic()
        if remaining > 0:
            self.status_label.setText(self.tr("LRCLIB 请求过于频繁，请在 {seconds} 秒后重试。",
                                              seconds=ceil(remaining)))
            self._timer.start(min(ceil(remaining * 1000), 2_147_483_647))
            return
        if self._search is not None:
            key, attempt = self._search
            self._search = None
            operation = partial(self._client.search, key, limit=self.RESULT_LIMIT)
            self.status_label.setText(self.tr("正在搜索歌词…"))
        else:
            key, attempt = self._previews.popleft()
            operation = partial(self._client.get, key)
            self.status_label.clear()
        self._in_flight = True
        cancelled, closed = self._cancelled, self._closed
        dialog_ref = ref(self)

        def run():
            if closed.is_set() or cancelled.is_set():
                return None, None
            try:
                return operation(), None
            except LyricsError as error:
                return None, error

        def complete(outcome):
            dialog = dialog_ref()
            if dialog is not None and not closed.is_set() and not sip.isdeleted(dialog):
                dialog._request_finished(key, attempt, cancelled, *outcome)

        self._submit(run, complete, lambda _message: complete((None, True)))

    def _request_finished(self, key, attempt, cancelled, value, error):
        self._in_flight = False
        rate_limited = isinstance(error, LyricsCooldownError)
        if rate_limited:
            self._cooldown_until = max(self._cooldown_until, monotonic() + error.retry_after)
        if cancelled.is_set():
            # A newer query owns the UI. Start it if its debounce already expired.
            if not self._timer.isActive():
                self._dispatch()
            return

        if rate_limited and attempt == 0:
            if isinstance(key, str):
                self._search = (key, 1)
            else:
                self._previews.appendleft((key, 1))
        elif isinstance(key, str):
            if error is not None:
                self.status_label.setText(self.tr("歌词搜索失败，请重试。"))
            else:
                self._show_results(value)
        else:
            self._show_preview(key, value)

        if self._search or self._previews:
            self._timer.start(self.PREVIEW_INTERVAL_MS)

    def _show_results(self, results: list[LRCLIBResult]):
        for result in results:
            if result.id in self._rows:
                continue
            item = QListWidgetItem(self._row_text(result, self.tr("正在加载预览…")))
            item.setData(Qt.ItemDataRole.UserRole, result.id)
            self.results_list.addItem(item)
            self._rows[result.id] = result, item
            self._previews.append((result.id, 0))
        if self._rows:
            self.status_label.clear()
            self.results_list.setCurrentRow(0)
        else:
            self.status_label.setText(self.tr("未找到歌词"))

    def _row_text(self, result: LRCLIBResult, preview: str) -> str:
        artist = result.artist or self.tr("未知艺术家")
        return f"{result.title} · {artist}\n{preview}"

    def _show_preview(self, identity: int, lyrics: LyricsEntry | None):
        result, item = self._rows[identity]
        if lyrics is None:
            preview = self.tr("歌词预览加载失败")
        else:
            self._loaded[identity] = lyrics
            if lyrics.instrumental:
                preview = self.tr("纯音乐")
            else:
                lines = islice((line.text.strip() for line in lyrics.display().lines if line.text.strip()), 3)
                preview = "\n".join(line if len(line) <= 160 else line[:159] + "…" for line in lines)
                preview = preview or self.tr("暂无歌词预览")
        item.setText(self._row_text(result, preview))
        self._update_confirm()

    def _current_lyrics(self) -> LyricsEntry | None:
        item = self.results_list.currentItem()
        return self._loaded.get(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _update_confirm(self, *_args):
        self.confirm_button.setEnabled(self._current_lyrics() is not None)

    def accept(self):
        lyrics = self._current_lyrics()
        if lyrics is not None:
            self.selected_lyrics = lyrics
            super().accept()

    def done(self, result):
        self._closed.set()
        self._cancelled.set()
        self._timer.stop()
        super().done(result)

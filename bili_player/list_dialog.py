"""Preview a public list and build an album without saving; Chinese follows.

预览空间列表并在后台生成专辑；仅返回完整快照，不更改或保存音乐库。
"""
from __future__ import annotations

from dataclasses import replace
from threading import Event

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QVBoxLayout,
)

from .details import DetailCover
from .i18n import translate, translate_error
from .lists import BilibiliListClient, ListImportResult, Listing, ListSource, parse_list_url


class _ProgressSignals(QObject):
    progress = pyqtSignal(int, int, str)


class ListImportDialog(QDialog):
    """Async preview/import; the owner alone commits / 异步预览与导入，由父窗口保存。"""

    def __init__(self, parent, initial_url: str = "", language=None):
        super().__init__(parent)
        self._owner = parent
        self.language = language or getattr(parent, "language", "zh_CN") or "zh_CN"
        self._alive = True
        self._busy = False
        self._disposed = False
        self._source: ListSource | None = None
        self._listing: Listing | None = None
        self._cancelled = Event()
        self._client = BilibiliListClient()
        # No QObject parent: an outstanding worker retains this emitter even if
        # the owner is destroyed. The receiving slot lives on the GUI thread.
        self._signals = _ProgressSignals()
        self._signals.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self.import_result: ListImportResult | None = None
        self.setObjectName("root")
        self.setWindowTitle(self.tr("导入空间列表"))
        self.setMinimumWidth(560)
        self.resize(640, 440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit(initial_url)
        self.url_input.setPlaceholderText("https://space.bilibili.com/1459104794/lists/2029798")
        self.url_input.setAccessibleName(self.tr("空间列表链接"))
        self.url_input.returnPressed.connect(self.read_list)
        self.read_button = self._button("读取列表", self.read_list)
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.read_button)
        layout.addWidget(self._label(self.tr("空间列表链接"), "section"))
        layout.addLayout(url_row)

        hero = QFrame()
        hero.setObjectName("queuePanel")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(14, 14, 14, 14)
        hero_layout.setSpacing(18)
        self.cover_widget = DetailCover(hero)
        self.cover_widget.setFixedSize(96, 96)
        hero_layout.addWidget(self.cover_widget)
        information = QVBoxLayout()
        self.preview_title = self._label(name="heading")
        self.preview_title.setWordWrap(True)
        self.count_label = self._label(name="muted")
        information.addWidget(self.preview_title)
        information.addWidget(self.count_label)
        information.addStretch()
        hero_layout.addLayout(information, 1)
        layout.addWidget(hero)

        form = QFormLayout()
        self.title_input = QLineEdit()
        self.title_input.returnPressed.connect(self.start_import)
        form.addRow(self.tr("专辑名称"), self.title_input)
        layout.addLayout(form)
        self.expand_parts = QCheckBox(self.tr("导入全部分 P（取消勾选则仅导入每个视频的 P1）"))
        self.expand_parts.setChecked(True)
        layout.addWidget(self.expand_parts)
        hint = self._label(self.tr("导入后可编辑曲目；列表更新不会自动同步。"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.status_label = self._label(self.tr("先读取列表，确认内容后再导入。"), "muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.error_label = self._label(name="dialogError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = self._button("取消", self.reject)
        self.import_button = self._button("导入为专辑", self.start_import)
        self.import_button.setObjectName("primary")
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.import_button)
        layout.addLayout(actions)
        self.url_input.textChanged.connect(self._url_changed)
        self._update_controls()

    def tr(self, text: str, **values) -> str:
        return translate(text, language=self.language, **values)

    @staticmethod
    def _label(text: str = "", name: str = "") -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName(name)
        return label

    def _button(self, text: str, callback) -> QPushButton:
        button = QPushButton(self.tr(text))
        button.setAutoDefault(False)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _live(self) -> bool:
        return self._alive and not sip.isdeleted(self)

    def _update_controls(self) -> None:
        available = self._alive and not self._busy
        ready = available and self._source is not None and self._listing is not None
        self.url_input.setEnabled(available)
        self.read_button.setEnabled(available)
        self.title_input.setEnabled(ready)
        self.expand_parts.setEnabled(ready)
        self.import_button.setEnabled(ready)

    def _clear_preview(self) -> None:
        self._source = None
        self._listing = None
        self.preview_title.clear()
        self.count_label.clear()
        self.title_input.clear()
        self.cover_widget._requested_cover = ""
        self.cover_widget.set_pixmap(QPixmap())
        self.cover_widget.setToolTip("")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

    def _url_changed(self, *_args) -> None:
        if not self._live() or self._busy:
            return
        self._clear_preview()
        self.error_label.clear()
        self.status_label.setText(self.tr("先读取列表，确认内容后再导入。"))
        self._update_controls()

    def _submit(self, fn, done, operation: str) -> None:
        submit = getattr(self._owner, "_submit", None)
        self._busy = True
        self._cancelled.clear()
        self._update_controls()
        def failed(message):
            self._busy = False
            if not self._live():
                self._dispose()
                return
            self.error_label.setText(translate_error(str(message), self.language))
            self.status_label.setText(self.tr(
                "读取列表失败，请重试。" if operation == "preview" else "导入失败，请重试。",
            ))
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self._update_controls()
        if not callable(submit):
            failed(self.tr("当前窗口无法启动后台任务。"))
            return
        try:
            submit(fn, done, failed)
        except Exception as exc:
            failed(str(exc))

    def read_list(self) -> None:
        if not self._live() or self._busy:
            return
        self._clear_preview()
        self.error_label.clear()
        try:
            source = parse_list_url(self.url_input.text())
        except ValueError as exc:
            self.error_label.setText(translate_error(str(exc), self.language))
            self.status_label.setText(self.tr("先读取列表，确认内容后再导入。"))
            self._update_controls()
            return
        self.status_label.setText(self.tr("正在读取列表…"))
        self.progress_bar.setRange(0, 0)
        client, cancelled = self._client, self._cancelled
        def done(listing: Listing):
            self._busy = False
            if not self._live():
                self._dispose()
                return
            self._source = source
            self._listing = listing
            self.preview_title.setText(listing.title)
            self.title_input.setText(listing.title)
            self.count_label.setText(self.tr(
                "合集 · {count} 个视频" if listing.kind == "season" else "系列 · {count} 个视频",
                count=listing.total,
            ))
            self.cover_widget._requested_cover = listing.cover
            load_cover = getattr(self._owner, "_load_cover", None)
            if listing.cover and callable(load_cover):
                load_cover(listing.cover, self.cover_widget)
            self.progress_bar.setRange(0, max(1, len(listing.bvids)))
            self.progress_bar.setValue(0)
            self.status_label.setText(self.tr("列表已就绪，可以导入。"))
            self._update_controls()
        self._submit(lambda: client.get_listing(source, cancelled=cancelled.is_set), done, "preview")

    def start_import(self) -> None:
        if not self._live() or self._busy:
            return
        self.error_label.clear()
        if self._source is None or self._listing is None:
            self.error_label.setText(self.tr("请先读取空间列表。"))
            return
        title = self.title_input.text().strip()
        if not title:
            self.error_label.setText(self.tr("请输入专辑名称。"))
            self.title_input.setFocus()
            return
        self.import_result = None
        self.status_label.setText(self.tr("正在准备导入…"))
        self.progress_bar.setRange(0, 0)
        source, expand = self._source, self.expand_parts.isChecked()
        client, cancelled, signals = self._client, self._cancelled, self._signals
        def done(result: ListImportResult):
            self._busy = False
            if not self._live():
                self._dispose()
                return
            self.import_result = replace(result, album=replace(result.album, title=title))
            self.accept()
        self._submit(lambda: client.import_album(
            source, expand_parts=expand, progress=signals.progress.emit, cancelled=cancelled.is_set,
        ), done, "import")

    @pyqtSlot(int, int, str)
    def _on_progress(self, done: int, total: int, bvid: str) -> None:
        if not self._live() or not self._busy:
            return
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(max(0, min(done, total)))
        if bvid:
            self.status_label.setText(self.tr(
                "正在导入视频 {done}/{total} · {bvid}", done=done, total=total, bvid=bvid,
            ))
        else:
            self.status_label.setText(self.tr("正在准备导入…"))

    def accept(self) -> None:
        if not self._live() or self._busy or self.import_result is None:
            return
        self._alive = False
        self._cancelled.set()
        super().accept()
        self._dispose()

    def reject(self) -> None:
        if not self._live():
            return
        self._alive = False
        self._cancelled.set()
        super().reject()
        if not self._busy:
            self._dispose()

    def closeEvent(self, event) -> None:
        self.reject()
        event.accept()

    def _dispose(self) -> None:
        if self._disposed or self._busy:
            return
        self._disposed = True
        session = getattr(self._client, "session", None)
        if session is not None:
            session.close()
        self._signals.deleteLater()
        if not sip.isdeleted(self):
            self.deleteLater()

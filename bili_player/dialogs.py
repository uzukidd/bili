from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Callable
from uuid import uuid4

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from .api import BilibiliClient
from .i18n import translate
from .models import LibraryEntry, Track, normalize_bvid


class _Dialog(QDialog):
    def __init__(self, parent=None, language: str | None = None):
        super().__init__(parent)
        self.language = language or getattr(parent, "language", "zh_CN") or "zh_CN"

    def tr(self, text: str, **values) -> str:
        return translate(text, language=self.language, **values)

    def _label(self, text: str = "", name: str = "") -> QLabel:
        widget = QLabel(text)
        widget.setTextFormat(Qt.TextFormat.PlainText)
        widget.setObjectName(name)
        return widget

    def _button(self, text: str, callback: Callable) -> QPushButton:
        widget = QPushButton(self.tr(text))
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.clicked.connect(callback)
        return widget

    def _buttons(self, layout: QVBoxLayout) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText(self.tr("保存"))
        self.save_button.setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.buttons = buttons
        return buttons


class EntryDialog(_Dialog):
    """Import a BV as a single page or all pages, or edit a saved single."""

    def __init__(self, parent=None, entry: LibraryEntry | None = None, language=None):
        super().__init__(parent, language)
        self.entry = deepcopy(entry)
        self.setWindowTitle(self.tr("编辑音乐") if entry else self.tr("添加音乐"))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_input = QLineEdit(entry.title if entry else "")
        self.title_input.setPlaceholderText(self.tr("名称可留空，导入时自动读取"))
        cached = entry.tracks[0] if entry and entry.tracks else None
        self.artist_input = QLineEdit(
            (entry.artist or (cached.artist if cached else "")) if entry else "",
        )
        self.artist_input.setPlaceholderText(self.tr("手动填写作者（可留空）"))
        self.date_input = QLineEdit(
            (entry.date or (cached.date if cached else "")) if entry else "",
        )
        self.date_input.setPlaceholderText(self.tr("手动填写日期，例如 2025-01-01（可留空）"))
        self.artist_label = self._label(self.tr("作者"))
        self.date_label = self._label(self.tr("日期"))
        self.bvid_input = QLineEdit(entry.bvid if entry else "")
        self.bvid_input.setPlaceholderText(self.tr("BV 号或哔哩哔哩视频链接"))
        self.kind_input = QComboBox()
        self.kind_input.addItem(self.tr("单曲 · 导入指定分 P"), "single")
        self.kind_input.addItem(self.tr("专辑 · 导入全部分 P"), "album")
        self.kind_input.setCurrentIndex(1 if entry and entry.kind == "album" else 0)
        self.kind_input.setEnabled(not entry or entry.kind != "single")
        self.page_input = QSpinBox()
        self.page_input.setRange(1, 10000)
        self.page_input.setValue(entry.page if entry else 1)
        self.kind_input.currentIndexChanged.connect(self._kind_changed)
        form.addRow(self.tr("名称"), self.title_input)
        form.addRow(self.artist_label, self.artist_input)
        form.addRow(self.date_label, self.date_input)
        form.addRow(self.tr("BV / 链接"), self.bvid_input)
        form.addRow(self.tr("类型"), self.kind_input)
        form.addRow(self.tr("分 P"), self.page_input)
        self._kind_changed()
        layout.addLayout(form)
        hint = self._label(self.tr("选择专辑会导入该视频的全部分 P。"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error = self._label()
        self.error.setObjectName("dialogError")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self._buttons(layout)

    def _kind_changed(self, *_):
        single = self.kind_input.currentData() == "single"
        self.page_input.setEnabled(single)
        for widget in (self.artist_input, self.artist_label, self.date_input, self.date_label):
            widget.setVisible(single)

    def value(self) -> LibraryEntry:
        bvid = normalize_bvid(self.bvid_input.text())
        changes = {
            "title": self.title_input.text().strip() or bvid,
            "bvid": bvid,
            "kind": self.kind_input.currentData(),
            "page": self.page_input.value(),
        }
        if changes["kind"] == "single":
            changes.update(
                artist=self.artist_input.text().strip(), date=self.date_input.text().strip(),
            )
        if self.entry is None:
            return LibraryEntry(**changes)
        if (bvid, changes["page"], changes["kind"]) != (
            self.entry.bvid, self.entry.page, self.entry.kind,
        ):
            changes.update(tracks=[], cover="", resolved=False)
        return replace(deepcopy(self.entry), **changes)

    def accept(self):
        try:
            self.value()
        except ValueError:
            self.error.setText(self.tr("请输入有效的 BV 号或哔哩哔哩视频链接。"))
            self.bvid_input.setFocus()
            return
        self.error.clear()
        super().accept()


class TrackDialog(_Dialog):
    """Edit metadata locally; the album resolves a changed BV/page asynchronously."""

    def __init__(self, parent, track: Track, language=None):
        super().__init__(parent, language)
        self.track = deepcopy(track)
        self.setWindowTitle(self.tr("编辑曲目"))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_input = QLineEdit(track.title)
        self.artist_input = QLineEdit(track.artist)
        self.artist_input.setPlaceholderText(self.tr("手动填写作者（可留空）"))
        self.date_input = QLineEdit(track.date)
        self.date_input.setPlaceholderText(self.tr("手动填写日期，例如 2025-01-01（可留空）"))
        self.bvid_input = QLineEdit(track.bvid)
        self.bvid_input.setPlaceholderText(self.tr("BV 号或哔哩哔哩视频链接"))
        self.page_input = QSpinBox()
        self.page_input.setRange(1, 10000)
        self.page_input.setValue(track.page)
        form.addRow(self.tr("曲目名称"), self.title_input)
        form.addRow(self.tr("作者"), self.artist_input)
        form.addRow(self.tr("日期"), self.date_input)
        form.addRow(self.tr("BV / 链接"), self.bvid_input)
        form.addRow(self.tr("分 P"), self.page_input)
        layout.addLayout(form)
        hint = self._label(self.tr("更改 BV 或分 P 后会重新读取音频信息。"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error = self._label()
        self.error.setObjectName("dialogError")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self._buttons(layout)

    def value(self) -> Track:
        title = self.title_input.text().strip()
        if not title:
            raise ValueError(self.tr("请输入曲目名称。"))
        return replace(
            deepcopy(self.track), title=title, artist=self.artist_input.text().strip(),
            date=self.date_input.text().strip(),
            bvid=normalize_bvid(self.bvid_input.text()), page=self.page_input.value(),
        )

    def accept(self):
        if not self.title_input.text().strip():
            self.error.setText(self.tr("请输入曲目名称。"))
            self.title_input.setFocus()
            return
        try:
            self.value()
        except ValueError:
            self.error.setText(self.tr("请输入有效的 BV 号或哔哩哔哩视频链接。"))
            self.bvid_input.setFocus()
            return
        self.error.clear()
        super().accept()


class _SinglesDialog(_Dialog):
    def __init__(self, parent, singles: list[LibraryEntry], language=None):
        super().__init__(parent, language)
        self.setWindowTitle(self.tr("添加已有单曲"))
        self.setMinimumSize(440, 340)
        layout = QVBoxLayout(self)
        hint = self._label(self.tr("勾选要添加到专辑的单曲，同一首歌曲的修改会同步到所有专辑。"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.singles_list = QListWidget()
        for entry in singles:
            item = QListWidgetItem(f"{entry.title}\n{entry.bvid} · P{entry.page}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, deepcopy(entry))
            self.singles_list.addItem(item)
        layout.addWidget(self.singles_list)
        self._buttons(layout)
        self.save_button.setText(self.tr("添加所选"))
        self.save_button.setEnabled(False)
        self.singles_list.itemChanged.connect(
            lambda *_: self.save_button.setEnabled(bool(self.value()))
        )

    def value(self) -> list[LibraryEntry]:
        return [
            deepcopy(self.singles_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.singles_list.count())
            if self.singles_list.item(row).checkState() == Qt.CheckState.Checked
        ]


class AlbumDialog(_Dialog):
    """Stage album membership; canonical song edits are saved separately."""

    def __init__(self, parent, entry: LibraryEntry, singles: list[LibraryEntry], language=None):
        super().__init__(parent, language)
        self._owner = getattr(parent, "_owner", parent)
        self.entry = deepcopy(entry)
        self.singles = deepcopy([single for single in singles if single.kind == "single"])
        self._alive = True
        self._pending = 0
        self._committed: LibraryEntry | None = None
        self.finished.connect(self._finished)
        self.setWindowTitle(self.tr("编辑专辑"))
        self.setMinimumSize(660, 520)
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_input = QLineEdit(self.entry.title)
        self.title_input.setPlaceholderText(self.tr("请输入专辑名称"))
        form.addRow(self.tr("专辑名称"), self.title_input)
        self.cover_input = QLineEdit(self.entry.cover)
        self.cover_input.setPlaceholderText(self.tr("图片链接或本地图片路径"))
        cover_row = QHBoxLayout()
        cover_row.addWidget(self.cover_input, 1)
        cover_row.addWidget(self._button("选择图片", self.choose_cover))
        form.addRow(self.tr("封面"), cover_row)
        layout.addLayout(form)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._button("添加 BV", self.add_bv))
        self.add_singles_button = self._button("添加已有单曲", self.add_existing_singles)
        self.add_singles_button.setEnabled(bool(self.singles))
        toolbar.addWidget(self.add_singles_button)
        toolbar.addStretch()
        self.count_label = self._label(name="muted")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)
        selection = QHBoxLayout()
        self.select_all_button = self._button("全选", lambda: self.tracks_list.selectAll())
        self.clear_selection_button = self._button("清除选择", lambda: self.tracks_list.clearSelection())
        self.selection_label = self._label(name="muted")
        for widget in (self.select_all_button, self.clear_selection_button, self.selection_label):
            selection.addWidget(widget)
        selection.addStretch()
        layout.addLayout(selection)
        self.tracks_list = QListWidget()
        self.tracks_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tracks_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tracks_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tracks_list.setDragEnabled(True)
        self.tracks_list.setAcceptDrops(True)
        self.tracks_list.setDropIndicatorShown(True)
        self.tracks_list.itemDoubleClicked.connect(self.edit_selected)
        self.tracks_list.itemSelectionChanged.connect(self._update_controls)
        self.tracks_list.model().rowsMoved.connect(self._update_controls)
        layout.addWidget(self.tracks_list, 1)
        actions = QHBoxLayout()
        self.edit_button = self._button("编辑曲目", self.edit_selected)
        self.remove_button = self._button("移除所选", self.remove_selected)
        self.up_button = self._button("上移", lambda: self.move_selected(-1))
        self.down_button = self._button("下移", lambda: self.move_selected(1))
        for widget in (self.edit_button, self.remove_button, self.up_button, self.down_button):
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        hint = self._label(self.tr("拖动曲目可调整顺序；双击可编辑名称、作者、日期和来源。"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.song_edit_hint = self._label(self.tr(
            "编辑歌曲会同步到所有专辑；取消此窗口只撤销专辑的增删和排序。",
        ), "muted")
        self.song_edit_hint.setWordWrap(True)
        layout.addWidget(self.song_edit_hint)
        self.status_label = self._label(name="muted")
        layout.addWidget(self.status_label)
        self.error = self._label()
        self.error.setObjectName("dialogError")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self._buttons(layout)
        for track in self.entry.tracks:
            self._append_track(deepcopy(track))
        self._update_controls()

    def _finished(self, *_):
        self._alive = False

    def _update_controls(self, *_):
        count = self.tracks_list.count()
        selected = self.tracks_list.selectedItems()
        rows = sorted(self.tracks_list.row(item) for item in selected)
        self.count_label.setText(self.tr("{count} 首曲目", count=count))
        self.selection_label.setText(self.tr("已选择 {count} 首", count=len(selected)))
        self.select_all_button.setEnabled(count > len(selected))
        self.clear_selection_button.setEnabled(bool(selected))
        self.edit_button.setEnabled(len(selected) == 1 and self._pending == 0)
        self.remove_button.setEnabled(bool(selected))
        self.up_button.setEnabled(bool(rows) and rows[0] > 0)
        self.down_button.setEnabled(bool(rows) and rows[-1] < count - 1)
        self.save_button.setEnabled(self._pending == 0)
        self.status_label.setText(
            self.tr("正在处理 {count} 项任务…", count=self._pending) if self._pending else ""
        )

    def _format_item(self, item: QListWidgetItem, track: Track):
        item.setData(Qt.ItemDataRole.UserRole, track)
        artist = track.artist or self.tr("未知艺术家")
        item.setText(f"{track.title}\n{artist} · {track.bvid} · P{track.page}")
        item.setToolTip(f"{track.title}\n{artist}\n{track.bvid} · P{track.page}")

    def _append_track(self, track: Track):
        item = QListWidgetItem()
        self._format_item(item, track)
        self.tracks_list.addItem(item)

    def _tracks(self) -> list[Track]:
        return [
            deepcopy(self.tracks_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.tracks_list.count())
        ]

    def add_tracks(self, tracks: list[Track]):
        if not self._alive:
            return
        identities = {track.song_id or track.id for track in self._tracks()}
        for track in tracks:
            identity = track.song_id or track.id
            if identity in identities:
                continue
            self._append_track(deepcopy(track))
            identities.add(identity)
        self._update_controls()

    def remove_selected(self, *_):
        for row in sorted(
            (self.tracks_list.row(item) for item in self.tracks_list.selectedItems()), reverse=True,
        ):
            self.tracks_list.takeItem(row)
        self._update_controls()

    def move_selected(self, offset: int):
        if offset == 0:
            return
        rows = sorted(self.tracks_list.row(item) for item in self.tracks_list.selectedItems())
        if not rows:
            return
        # Move a selection as a block, preserving its order and its selection state.
        distance = max(-rows[0], min(offset, self.tracks_list.count() - 1 - rows[-1]))
        if not distance:
            return
        current = self.tracks_list.currentItem()
        ordered_rows = rows if distance < 0 else list(reversed(rows))
        for row in ordered_rows:
            item = self.tracks_list.takeItem(row)
            self.tracks_list.insertItem(row + distance, item)
            item.setSelected(True)
        if current is not None:
            self.tracks_list.setCurrentItem(
                current, QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        self._update_controls()

    def choose_cover(self, *_):
        filename, _ = QFileDialog.getOpenFileName(
            self, self.tr("选择专辑封面"), "",
            self.tr("图片文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*)"),
        )
        if filename:
            self.cover_input.setText(str(Path(filename).expanduser().resolve()))

    def _run(self, fn: Callable, done: Callable):
        if not self._alive:
            return
        submit = getattr(self._owner, "_submit", None)
        if not callable(submit):
            self._show_error(self.tr("当前窗口无法启动后台任务。"))
            return
        self._pending += 1
        self._update_controls()
        settled = False

        def finish():
            nonlocal settled
            if settled:
                return False
            settled = True
            if not self._alive:
                return False
            self._pending -= 1
            self._update_controls()
            return True

        def succeeded(result):
            if finish():
                try:
                    done(result)
                except Exception as exc:
                    self._show_error(str(exc))

        def failed(message):
            if finish():
                self._show_error(str(message))

        try:
            submit(fn, succeeded, failed)
        except Exception as exc:
            failed(str(exc))

    def _show_error(self, message: str):
        if self._alive:
            QMessageBox.warning(
                self, self.tr("操作失败"),
                self.tr("无法完成操作，请稍后重试。\n{error}", error=message),
            )

    @staticmethod
    def _single_title(entry: LibraryEntry, tracks: list[Track]) -> list[Track]:
        changes = {}
        if entry.kind == "single":
            if entry.title.strip() and entry.title != entry.bvid:
                changes["title"] = entry.title
            if entry.artist:
                changes["artist"] = entry.artist
            if entry.date:
                changes["date"] = entry.date
        return [replace(deepcopy(track), **changes) for track in tracks]

    def add_bv(self, *_):
        dialog = EntryDialog(self, language=self.language)
        if dialog.exec() != QDialog.DialogCode.Accepted or not self._alive:
            return
        source = dialog.value()

        def load():
            info = BilibiliClient().get_video(source)
            tracks = [replace(track, id=str(uuid4()), song_id="") for track in info.tracks]
            return self._single_title(source, tracks), info.cover

        def added(result):
            tracks, cover = result
            self.add_tracks(tracks)
            if not self.cover_input.text().strip() and cover:
                self.cover_input.setText(cover)

        self._run(load, added)

    def add_existing_singles(self, *_):
        dialog = _SinglesDialog(self, self.singles, language=self.language)
        if dialog.exec() != QDialog.DialogCode.Accepted or not self._alive:
            return
        selected = dialog.value()
        if not selected:
            return

        def load():
            tracks = []
            for entry in selected:
                if entry.tracks:
                    resolved = [replace(deepcopy(track), song_id=entry.id) for track in entry.tracks]
                else:
                    resolved = [replace(track, id=entry.id, song_id=entry.id)
                                for track in BilibiliClient().get_tracks(entry)]
                tracks.extend(self._single_title(entry, resolved))
            return tracks

        self._run(load, self.add_tracks)

    def edit_selected(self, *_):
        selected = self.tracks_list.selectedItems()
        if len(selected) != 1 or self._pending or not self._alive:
            return
        original = deepcopy(selected[0].data(Qt.ItemDataRole.UserRole))
        lookup = getattr(self._owner, "song_for_track", None)
        song = lookup(original, self.entry) if callable(lookup) else None
        if song is not None:
            self._owner.edit_single(song)
            if not self._alive:
                return
            refreshed = lookup(original, self.entry)
            for row in range(self.tracks_list.count()):
                item = self.tracks_list.item(row)
                member = item.data(Qt.ItemDataRole.UserRole)
                if (member.song_id or member.id) == (original.song_id or original.id):
                    if refreshed is not None and refreshed.tracks:
                        self._format_item(item, replace(deepcopy(refreshed.tracks[0]), song_id=refreshed.id))
                    else:
                        self.tracks_list.takeItem(row)
                    break
            self._update_controls()
            return
        dialog = TrackDialog(self, original, language=self.language)
        if dialog.exec() != QDialog.DialogCode.Accepted or not self._alive:
            return
        edited = dialog.value()

        def apply(track):
            # The user may remove or reorder this track while the API is working.
            for row in range(self.tracks_list.count()):
                item = self.tracks_list.item(row)
                member = item.data(Qt.ItemDataRole.UserRole)
                if (member.song_id or member.id) == (original.song_id or original.id):
                    self._format_item(item, track)
                    break

        if (edited.bvid, edited.page) == (original.bvid, original.page):
            apply(edited)
            return
        source = LibraryEntry(
            title=edited.title, bvid=edited.bvid, kind="single", page=edited.page,
        )

        def load():
            tracks = BilibiliClient().get_tracks(source)
            if not tracks:
                raise ValueError(self.tr("这个视频没有可播放的分 P。"))
            return replace(
                deepcopy(tracks[0]), id=original.id, title=edited.title, artist=edited.artist,
                date=edited.date, song_id=original.song_id,
            )

        self._run(load, apply)

    def value(self) -> LibraryEntry:
        if self._committed is not None:
            return deepcopy(self._committed)
        title = self.title_input.text().strip()
        if not title:
            raise ValueError(self.tr("请输入专辑名称。"))
        return replace(
            deepcopy(self.entry), title=title, cover=self.cover_input.text().strip(),
            tracks=self._tracks(), kind="album", resolved=True,
        )

    def accept(self):
        if not self._alive or self._pending:
            return
        try:
            result = self.value()
        except ValueError:
            self.error.setText(self.tr("请输入专辑名称。"))
            self.title_input.setFocus()
            return
        self.error.clear()
        self._committed = result
        super().accept()

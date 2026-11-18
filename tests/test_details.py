"""Offline tests for non-destructive entry details and delegated playback/editing."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from dataclasses import asdict, replace
import re
import unittest
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QListWidget, QListWidgetItem, QPushButton, QWidget, QStyleOptionViewItem

from bili_player.details import DetailCover, EntryDetailsDialog
from bili_player.models import LibraryEntry, Track

BV = "BV1GJ411x7h7"


class DetailsParent(QWidget):
    """Only the dialog's parent protocol; no media player, storage, or network."""

    def __init__(self, entries):
        super().__init__()
        self.language = "zh_CN"
        self.entries = list(entries)
        self.queue = QListWidget(self)
        self.appended = []
        self.resolve_entry = Mock()
        self.play_tracks = Mock()
        self.play_item = Mock()
        self.open_album = Mock()
        self.edit_single = Mock()
        self.open_track_details = Mock()
        self.insert_track_next = Mock()
        self.edit_album_track = Mock()
        self._load_cover = Mock()

    def _find(self, identity):
        return next((entry for entry in self.entries if entry.id == identity), None)

    def _append_tracks(self, tracks):
        self.appended.extend(tracks)
        for track in tracks:
            item = QListWidgetItem(track.title)
            item.setData(Qt.ItemDataRole.UserRole, track)
            self.queue.addItem(item)


class CanonicalDetailsParent(DetailsParent):
    def __init__(self, entries):
        super().__init__(entries)
        for entry in entries:
            if entry.kind == "single":
                for track in entry.tracks:
                    track.song_id = entry.id

    def song_for_track(self, track, album=None):
        identity = getattr(track, "song_id", "") or track.id
        return next((entry for entry in self.entries
                     if entry.kind == "single" and entry.id == identity), None)


class DetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tracks = [
            Track("First song", BV, 123, 1, artist="Saved artist", duration=65),
            Track("Second song", BV, 456, 2, duration=125),
        ]
        self.album = LibraryEntry("My album", BV, "album", tracks=self.tracks,
                                  cover="local-cover.png", resolved=True)
        self.single = LibraryEntry("Saved single", BV, tracks=[deepcopy(self.tracks[0])], resolved=True)
        self.parent = DetailsParent([self.album, self.single])
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.close()
                dialog.deleteLater()
        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()

    def dialog(self, entry=None, language=None):
        dialog = EntryDetailsDialog(self.parent, entry or self.album, language=language)
        self.dialogs.append(dialog)
        return dialog

    def test_opening_displays_album_without_playback_or_mutating_library_or_queue(self):
        existing = Track("Already queued", BV, 789, 3)
        self.parent._append_tracks([existing])
        before = [asdict(entry) for entry in self.parent.entries]
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.windowTitle(), "专辑详情")
        self.assertEqual(dialog.title_label.text(), "My album")
        self.assertEqual(dialog.meta_label.text(), "专辑 · 2 首 · 3:10")
        self.assertEqual(dialog.artist_label.text(), "Saved artist")
        self.assertEqual(dialog.source_label.text(), f"来源：{BV}")
        self.assertEqual(dialog.tracks_list.count(), 2)
        self.assertIn("First song", dialog.tracks_list.item(0).text())
        self.assertNotIn("未知艺术家", dialog.tracks_list.item(1).text())
        self.assertNotIn("Saved artist", dialog.tracks_list.item(1).text())
        self.assertEqual([asdict(entry) for entry in self.parent.entries], before)
        self.assertEqual(self.parent.appended, [existing])
        self.assertEqual(self.parent.queue.count(), 1)
        self.parent.resolve_entry.assert_not_called()
        self.parent.play_item.assert_not_called()
        self.parent.open_album.assert_not_called()
        self.parent._load_cover.assert_called_once_with("local-cover.png", dialog.cover_widget)
        self.assertEqual(dialog.cover_widget.width(), dialog.cover_widget.height())

    def test_whole_entry_buttons_delegate_play_and_queue_without_copying_in_view(self):
        dialog = self.dialog()
        dialog.play_button.click()
        dialog.queue_button.click()
        self.assertEqual(self.parent.resolve_entry.call_args_list[0].args, (self.album, True))
        self.assertEqual(self.parent.resolve_entry.call_args_list[1].args, (self.album, False))
        self.assertEqual(len(self.parent.resolve_entry.call_args_list), 2)
        self.assertEqual(self.parent.appended, [])
        self.parent.play_item.assert_not_called()

    def test_track_play_button_uses_shared_playback_for_selected_song(self):
        dialog = self.dialog()
        dialog.track_rows[1].play_button.click()
        self.parent.play_tracks.assert_called_once_with([self.tracks[1]])
        self.assertEqual(self.parent.appended, [])
        self.parent.play_item.assert_not_called()
        self.parent.resolve_entry.assert_not_called()
        self.parent.open_track_details.assert_not_called()

    def test_double_click_opens_track_details_without_queueing_or_playing(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        row = dialog.track_rows[1]
        QTest.mouseDClick(row.title_label, Qt.MouseButton.LeftButton, pos=QPoint(20, 10))
        self.assertGreater(row.play_button.y(), row.metadata_label.y())
        self.assertEqual(row.play_button.y(), row.insert_button.y())
        self.parent.open_track_details.assert_called_once_with(self.tracks[1], self.album)
        self.parent.play_item.assert_not_called()
        self.parent.resolve_entry.assert_not_called()
        self.assertEqual(self.parent.queue.count(), 0)
        self.assertEqual(self.parent.appended, [])

    def test_track_insert_button_does_not_start_playback(self):
        dialog = self.dialog()
        dialog.track_rows[0].insert_button.click()
        self.parent.insert_track_next.assert_called_once_with(self.tracks[0])
        self.parent.play_item.assert_not_called()
        self.parent.resolve_entry.assert_not_called()
        self.parent.open_track_details.assert_not_called()

    def test_album_edit_delegates_and_refreshes_replaced_saved_entry(self):
        dialog = self.dialog()
        edited = replace(self.album, title="Edited album", cover="new-cover.png",
                         tracks=[replace(self.tracks[0], title="Edited song", duration=3601)])
        self.parent.open_album.side_effect = lambda entry: self.parent.entries.__setitem__(0, edited)
        dialog.edit_button.click()
        self.parent.open_album.assert_called_once_with(self.album)
        self.parent.edit_single.assert_not_called()
        self.assertIs(dialog.entry, edited)
        self.assertEqual(dialog.title_label.text(), "Edited album")
        self.assertEqual(dialog.meta_label.text(), "专辑 · 1 首 · 1:00:01")
        self.assertEqual(dialog.tracks_list.count(), 1)
        self.assertIn("Edited song", dialog.tracks_list.item(0).text())
        self.assertEqual(self.parent._load_cover.call_args.args, ("new-cover.png", dialog.cover_widget))
        self.assertEqual(self.album.title, "My album")

    def test_single_edit_and_cancellation_keep_details_open(self):
        dialog = self.dialog(self.single)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.windowTitle(), "单曲详情")
        before = asdict(self.single)
        dialog.edit_button.click()  # A canceled editor saves nothing.
        self.parent.edit_single.assert_called_once_with(self.single)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(asdict(self.single), before)
        edited = replace(self.single, title="Renamed single", tracks=[replace(self.single.tracks[0], title="New title")])
        self.parent.edit_single.side_effect = lambda entry: self.parent.entries.__setitem__(1, edited)
        dialog.edit_button.click()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.title_label.text(), "Renamed single")
        self.assertEqual(dialog.tracks_list.count(), 0)
        self.assertTrue(dialog.tracks_list.isHidden())
        self.assertTrue(dialog.track_heading.isHidden())
        self.assertIn("作者：", dialog.artist_label.text())
        self.assertEqual(dialog.date_label.text(), "日期：未填写")
        self.parent.open_album.assert_not_called()

    def test_edit_return_tolerates_native_destruction_of_details(self):
        dialog = self.dialog()
        self.parent.open_album.side_effect = lambda entry: sip.delete(dialog)
        dialog._edit()
        self.assertTrue(sip.isdeleted(dialog))
        self.parent.open_album.assert_called_once_with(self.album)
        self.assertEqual(self.parent.appended, [])

    def test_empty_custom_album_has_no_source_and_disabled_play_actions(self):
        custom = LibraryEntry("Empty album", kind="album", resolved=True)
        self.parent.entries.append(custom)
        dialog = self.dialog(custom)
        self.assertEqual(dialog.source_label.text(), "")
        self.assertTrue(dialog.source_label.isHidden())
        self.assertFalse(dialog.source_label.openExternalLinks())
        self.assertEqual(dialog.tracks_list.count(), 0)
        self.assertFalse(dialog.empty_label.isHidden())
        self.assertFalse(dialog.play_button.isEnabled())
        self.assertFalse(dialog.queue_button.isEnabled())
        self.assertTrue(dialog.edit_button.isEnabled())
        self.parent._load_cover.assert_not_called()

    def test_single_uses_saved_title_fallback_and_omits_missing_artist(self):
        track = replace(self.single.tracks[0], title="", artist="")
        self.parent.entries[1] = replace(self.single, tracks=[track])
        dialog = self.dialog(self.single)
        self.assertEqual(dialog.title_label.text(), "Saved single")
        self.assertTrue(dialog.tracks_list.isHidden())
        self.assertEqual(dialog.tracks_list.count(), 0)
        self.assertEqual(dialog.artist_label.text(), "作者：未填写")
        self.assertEqual(dialog.date_label.text(), "日期：未填写")
        self.assertFalse(dialog.artist_label.isHidden())

    def test_refresh_of_deleted_entry_closes_without_recreating_or_playing(self):
        dialog = self.dialog()
        dialog.show()
        self.parent.entries.remove(self.album)
        dialog.refresh()
        self.assertFalse(dialog.isVisible())
        self.assertFalse(dialog.play_button.isEnabled())
        dialog._play_track(dialog.tracks_list.item(0))
        self.assertNotIn(self.album, self.parent.entries)
        self.assertEqual(self.parent.appended, [])
        self.parent.resolve_entry.assert_not_called()

    def test_single_information_page_shows_manual_author_and_freeform_date(self):
        single = replace(self.single, artist="作者文字", date="2025 春 / 未定", tracks=[replace(self.single.tracks[0], date="旧日期")])
        self.parent.entries[1] = single
        dialog = self.dialog(single)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.artist_label.text(), "作者：作者文字")
        self.assertEqual(dialog.date_label.text(), "日期：2025 春 / 未定")
        self.assertTrue(dialog.track_heading.isHidden())
        self.assertTrue(dialog.tracks_list.isHidden())
        self.assertEqual(dialog.track_rows, [])
        self.assertEqual(dialog.cover_widget.width(), 280)
        self.assertEqual(dialog.cover_widget.width(), dialog.cover_widget.height())
        self.assertEqual(dialog.cover_opacity.opacity(), 1)
        self.assertTrue(dialog.sticky_header.isHidden())
        dialog.play_button.click()
        self.parent.resolve_entry.assert_called_once_with(single, True)

    def test_explicit_english_override_translates_all_owned_visible_text(self):
        dialog = self.dialog(language="en_US")
        self.assertEqual(self.parent.language, "zh_CN")
        self.assertEqual(dialog.windowTitle(), "Album details")
        self.assertEqual(dialog.meta_label.text(), "Album · 2 tracks · 3:10")
        texts = [dialog.windowTitle()]
        texts.extend(widget.text() for widget in dialog.findChildren(QLabel))
        texts.extend(widget.text() for widget in dialog.findChildren(QPushButton))
        for text in texts:
            self.assertIsNone(re.search(r"[\u3400-\u9fff]", text), text)
        single = self.dialog(self.single, language="en_US")
        self.assertEqual(single.windowTitle(), "Single details")
        self.assertEqual(single.meta_label.text(), "Single · 1:05")
        self.assertEqual(single.artist_label.text(), "Author: Saved artist")
        self.assertEqual(single.date_label.text(), "Date: Not set")

    def test_scroll_gradually_collapses_cover_and_keeps_album_title(self):
        many = [replace(self.tracks[0], id=f"track-{i}", title=f"Song {i}") for i in range(20)]
        self.parent.entries[0] = replace(self.album, tracks=many)
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        bar = dialog.scroll_area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 300)
        self.assertEqual(dialog.cover_widget.width(), 212)
        self.assertTrue(dialog.sticky_header.isHidden())
        initial_maximum = bar.maximum()
        bar.setValue(90)
        self.app.processEvents()
        self.assertLess(dialog.cover_widget.width(), 212)
        self.assertGreater(dialog.cover_widget.width(), 32)
        self.assertGreater(dialog.cover_opacity.opacity(), 0)
        self.assertLess(dialog.cover_opacity.opacity(), 1)
        bar.setValue(240)
        self.app.processEvents()
        self.assertEqual(dialog.cover_opacity.opacity(), 0)
        self.assertFalse(dialog.sticky_header.isHidden())
        self.assertEqual(dialog.sticky_title.text(), "My album")
        self.assertEqual(bar.maximum(), initial_maximum)
        self.assertEqual(dialog.hero.height(), 256)
        bar.setValue(0)
        self.app.processEvents()
        self.assertEqual(dialog.cover_widget.width(), 212)
        self.assertEqual(dialog.cover_opacity.opacity(), 1)
        self.assertTrue(dialog.sticky_header.isHidden())
        self.assertTrue(all(row.play_button.isEnabled() and row.insert_button.isEnabled() for row in dialog.track_rows))

    def test_album_track_details_follow_album_edits_and_do_not_require_library_single(self):
        track = self.tracks[1]
        transient = LibraryEntry(track.title, track.bvid, page=track.page, id=track.id,
                                 tracks=[track], cover=self.album.cover, resolved=True)
        dialog = EntryDetailsDialog(self.parent, transient, album_id=self.album.id, track_id=track.id)
        self.dialogs.append(dialog)
        self.assertEqual(dialog.windowTitle(), "单曲详情")
        self.assertEqual(dialog.title_label.text(), "Second song")
        self.assertEqual(len(self.parent.entries), 2)
        changed = replace(track, title="Changed in album", duration=333)
        self.parent.entries[0] = replace(self.album, tracks=[self.tracks[0], changed])
        dialog.refresh()
        self.assertEqual(dialog.title_label.text(), changed.title)
        self.assertIs(dialog.entry.tracks[0], changed)
        dialog.edit_button.click()
        self.parent.edit_single.assert_called_once_with(dialog.entry)
        self.parent.edit_album_track.assert_not_called()
        self.parent.open_album.assert_not_called()
        self.parent.entries[0].tracks = []
        dialog.refresh()
        self.assertFalse(dialog.play_button.isEnabled())
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)

    def test_album_song_details_edit_canonical_single_and_refresh_all_references(self):
        song = replace(self.single, id=self.tracks[0].id, tracks=[self.tracks[0]],
                       cover="song-cover.png", artist="Song author", date="Song date")
        other_album = LibraryEntry("Other folder", kind="album", tracks=[self.tracks[0]], resolved=True)
        parent = CanonicalDetailsParent([self.album, other_album, song])
        self.addCleanup(parent.deleteLater)
        self.addCleanup(parent.close)
        contextual = EntryDetailsDialog(parent, song, album_id=self.album.id, track_id=song.id)
        standalone = EntryDetailsDialog(parent, song)
        folder = EntryDetailsDialog(parent, other_album)
        self.dialogs.extend([contextual, standalone, folder])
        self.assertIs(contextual.entry, song)
        self.assertEqual(contextual.title_label.text(), "Saved single")
        self.assertEqual(contextual.artist_label.text(), "作者：Song author")
        self.assertEqual(contextual.date_label.text(), "日期：Song date")
        self.assertEqual(contextual._cover_source, "song-cover.png")

        def edit_single(entry):
            self.assertIs(entry, song)
            entry.title = entry.tracks[0].title = "Shared new title"
            entry.artist = entry.tracks[0].artist = "Shared author"
            entry.date = entry.tracks[0].date = "Shared date"

        parent.edit_single.side_effect = edit_single
        contextual.edit_button.click()
        parent.edit_single.assert_called_once_with(song)
        parent.open_album.assert_not_called()
        parent.edit_album_track.assert_not_called()
        standalone.refresh()
        folder.refresh()
        for dialog in (contextual, standalone):
            self.assertIs(dialog.entry, song)
            self.assertEqual(dialog.title_label.text(), "Shared new title")
            self.assertEqual(dialog.artist_label.text(), "作者：Shared author")
            self.assertEqual(dialog.date_label.text(), "日期：Shared date")
        self.assertIn("Shared new title", folder.track_rows[0].title_label.text())
        self.assertIs(self.album.tracks[0], song.tracks[0])
        self.assertIs(other_album.tracks[0], song.tracks[0])
        self.assertEqual((self.album.title, other_album.title), ("My album", "Other folder"))

        self.album.tracks = []
        contextual.refresh()
        self.assertIs(contextual.entry, song)
        self.assertTrue(contextual.edit_button.isEnabled())
        parent.entries.remove(song)
        contextual.show()
        contextual.refresh()
        self.assertFalse(contextual.isVisible())
        self.assertFalse(contextual.edit_button.isEnabled())
        self.assertEqual(contextual.result(), contextual.DialogCode.Rejected)

    def test_canonical_single_details_edit_without_album_context(self):
        song = replace(self.single, id=self.tracks[0].id, tracks=[self.tracks[0]])
        parent = CanonicalDetailsParent([self.album, song])
        self.addCleanup(parent.deleteLater)
        self.addCleanup(parent.close)
        dialog = EntryDetailsDialog(parent, song)
        self.dialogs.append(dialog)
        dialog.edit_button.click()
        parent.edit_single.assert_called_once_with(song)
        parent.open_album.assert_not_called()
        parent.edit_album_track.assert_not_called()
        parent.entries.remove(song)
        dialog.refresh()
        self.assertFalse(dialog.edit_button.isEnabled())

    def test_buttons_and_doubleclick_still_work_after_collapsing_header(self):
        many = [replace(self.tracks[0], id=f"track-{i}", title=f"Song {i}") for i in range(12)]
        self.parent.entries[0] = replace(self.album, tracks=many)
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        dialog.scroll_area.verticalScrollBar().setValue(250)
        dialog.track_rows[2].insert_button.click()
        self.parent.insert_track_next.assert_called_once_with(many[2])
        dialog.tracks_list.itemDoubleClicked.emit(dialog.tracks_list.item(2))
        self.parent.open_track_details.assert_called_once_with(many[2], self.parent.entries[0])
        self.parent.play_item.assert_not_called()

    def test_wheel_over_track_widgets_scrolls_outer_album(self):
        many = [replace(self.tracks[0], id=f"track-{i}") for i in range(20)]
        self.parent.entries[0] = replace(self.album, tracks=many)
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        bar = dialog.scroll_area.verticalScrollBar()
        for target in (dialog.tracks_list.viewport(), dialog.track_rows[0].title_label, dialog.track_rows[0].insert_button):
            bar.setValue(0)
            point = QPointF(10, 10)
            event = QWheelEvent(point, QPointF(target.mapToGlobal(QPoint(10, 10))),
                                QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(target, event)
            self.app.processEvents()
            self.assertGreater(bar.value(), 0, target.objectName())

    def test_custom_track_rows_do_not_paint_duplicate_list_text(self):
        dialog = self.dialog()
        option = QStyleOptionViewItem()
        index = dialog.tracks_list.model().index(0, 0)
        dialog.tracks_list.itemDelegate().initStyleOption(option, index)
        self.assertEqual(option.text, "")
        self.assertIn("First song", dialog.tracks_list.item(0).text())
        self.assertIn("First song", dialog.track_rows[0].title_label.text())

    def test_cover_center_crops_wide_artwork_instead_of_stretching(self):
        dialog = self.dialog()
        cover = dialog.cover_widget
        self.assertIsInstance(cover, DetailCover)
        pixmap = QPixmap(636, 212)
        pixmap.fill(QColor("red"))
        painter = QPainter(pixmap)
        painter.fillRect(212, 0, 212, 212, QColor("green"))
        painter.fillRect(424, 0, 212, 212, QColor("blue"))
        painter.end()
        cover.set_pixmap(pixmap)
        image = cover.grab().toImage()
        for x in (20, 106, 190):
            self.assertEqual(image.pixelColor(x, 106).name(), QColor("green").name())
        self.assertNotEqual(image.pixelColor(0, 0).name(), QColor("green").name())
        self.assertEqual(pixmap.size().width(), 636)


if __name__ == "__main__":
    unittest.main()

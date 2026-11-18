"""Album multiselection and ID-based batch delegation without playback or network."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import unittest
from unittest.mock import Mock

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QWidget

from bili_player.details import EntryDetailsDialog
from bili_player.models import LibraryEntry, Track

BV = "BV1GJ411x7h7"


class BatchParent(QWidget):
    libraryChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.language = "zh_CN"
        self.tracks = [Track(f"Song {i}", BV, 123, 1, id=f"reference-{i}", song_id=f"song-{i}")
                       for i in range(4)]
        self.album = LibraryEntry("Folder", kind="album", tracks=self.tracks, resolved=True)
        self.single = LibraryEntry("Single", BV, id="song-0", tracks=[self.tracks[0]], resolved=True)
        self.entries = [self.album, self.single]
        for name in ("batch_queue_tracks", "batch_copy_tracks", "batch_remove_album_tracks",
                     "copy_library_entries", "open_track_details", "insert_track_next", "_load_cover"):
            setattr(self, name, Mock())

    def _find(self, identity):
        return next((entry for entry in self.entries if entry.id == identity), None)


class AlbumBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = BatchParent()
        self.dialogs = []
        self.dialog = self.open_details(self.parent.album)

    def open_details(self, entry):
        dialog = EntryDetailsDialog(self.parent, entry)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()

    def click_row(self, row, modifiers=Qt.KeyboardModifier.NoModifier, field="title_label"):
        widget = getattr(self.dialog.track_rows[row], field)
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, modifiers, QPoint(12, 10))
        self.app.processEvents()

    def test_plain_click_does_not_select_and_selection_mode_supports_ctrl_and_shift(self):
        self.assertEqual(self.dialog.tracks_list.selectionMode(), QAbstractItemView.SelectionMode.NoSelection)
        self.click_row(0)
        self.click_row(2, Qt.KeyboardModifier.ControlModifier, "metadata_label")
        self.assertEqual(self.dialog.selected_song_ids(), [])
        self.assertTrue(self.dialog.selection_toolbar.isHidden())
        self.dialog.enter_track_selection("song-0")
        self.assertEqual(self.dialog.selected_song_ids(), ["song-0"])
        self.click_row(2, Qt.KeyboardModifier.ControlModifier, "metadata_label")
        self.assertEqual(self.dialog.selected_song_ids(), ["song-0", "song-2"])
        self.click_row(3, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.dialog.selected_song_ids(), ["song-0", "song-2", "song-3"])
        self.click_row(1)
        self.assertEqual(self.dialog.selected_song_ids(), ["song-0", "song-1", "song-2", "song-3"])

    def test_batch_toolbar_hides_until_selection_and_escape_exits_selection(self):
        dialog = self.dialog
        self.assertTrue(dialog.batch_toolbar.isHidden())
        self.dialog.enter_track_selection("song-0")
        self.click_row(2, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(dialog.batch_toolbar.isVisible())
        self.assertEqual(dialog.selected_song_ids(), ["song-0", "song-2"])
        dialog.tracks_list.setFocus()
        QTest.keyClick(dialog.tracks_list, Qt.Key.Key_Escape)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.selected_song_ids(), [])
        self.assertTrue(dialog.batch_toolbar.isHidden())
        QTest.keyClick(dialog.tracks_list, Qt.Key.Key_Escape)
        self.assertFalse(dialog.isVisible())

    def test_album_and_single_details_use_frameless_title_controls(self):
        for dialog in (self.dialog, self.open_details(self.parent.single)):
            with self.subTest(kind=dialog.entry.kind):
                self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
                self.assertEqual(dialog.title_bar.title_label.text(), dialog.windowTitle())
                QTest.mouseClick(dialog.title_bar.maximize_button, Qt.MouseButton.LeftButton)
                self.assertTrue(dialog.isMaximized())
                QTest.mouseClick(dialog.title_bar.maximize_button, Qt.MouseButton.LeftButton)
                self.assertFalse(dialog.isMaximized())
                QTest.mouseClick(dialog.title_bar.close_button, Qt.MouseButton.LeftButton)
                self.assertFalse(dialog.isVisible())

    def test_select_all_clear_and_batch_controls(self):
        dialog = self.dialog
        self.assertFalse(dialog.batch_queue_button.isEnabled())
        self.assertFalse(dialog.batch_copy_button.isEnabled())
        self.assertFalse(dialog.batch_remove_button.isEnabled())
        dialog.enter_track_selection("song-0")
        dialog.select_all_button.click()
        self.assertEqual(dialog.selected_song_ids(), [f"song-{i}" for i in range(4)])
        self.assertEqual(dialog.selection_label.text(), "已选择 4 首")
        self.assertFalse(dialog.select_all_button.isEnabled())
        dialog.clear_selection_button.click()
        self.assertEqual(dialog.selected_song_ids(), [])
        self.assertFalse(dialog.clear_selection_button.isEnabled())
        self.assertFalse(dialog.batch_copy_button.isEnabled())

    def test_bulk_actions_delegate_song_ids_in_album_order_without_source_deduplication(self):
        dialog = self.dialog
        dialog.enter_track_selection("song-1")
        for row in (3, 1):
            dialog.tracks_list.item(row).setSelected(True)
        for button, method in ((dialog.batch_queue_button, self.parent.batch_queue_tracks),
                               (dialog.batch_copy_button, self.parent.batch_copy_tracks),
                               (dialog.batch_remove_button, self.parent.batch_remove_album_tracks)):
            button.click()
            method.assert_called_once_with(self.parent.album.id, ["song-1", "song-3"])
        self.assertEqual(dialog.selected_song_ids(), ["song-1", "song-3"])
        self.assertEqual(len(self.parent.album.tracks), 4)

    def test_refresh_preserves_selection_by_song_id_after_reference_ids_and_order_change(self):
        self.dialog.enter_track_selection("song-1")
        self.parent.album.tracks = [replace(track, id=f"new-{track.id}")
                                    for track in reversed(self.parent.tracks)]
        self.parent.libraryChanged.emit()
        self.assertEqual(self.dialog.selected_song_ids(), ["song-1"])
        self.assertEqual(self.dialog.tracks_list.row(self.dialog.tracks_list.selectedItems()[0]), 2)

    def test_remove_refreshes_rows_and_keeps_song_entity(self):
        dialog = self.dialog
        dialog.enter_track_selection("song-0")
        dialog.tracks_list.item(2).setSelected(True)

        def remove(album_id, song_ids):
            self.parent.album.tracks = [track for track in self.parent.album.tracks if track.song_id not in song_ids]
            self.parent.libraryChanged.emit()

        self.parent.batch_remove_album_tracks.side_effect = remove
        dialog.batch_remove_button.click()
        self.assertEqual(dialog.tracks_list.count(), 2)
        self.assertEqual(dialog.selected_song_ids(), [])
        self.assertFalse(dialog.batch_remove_button.isEnabled())
        self.assertIn(self.parent.single, self.parent.entries)

    def test_stale_removed_selection_is_not_sent_to_parent(self):
        self.dialog.enter_track_selection("song-0")
        self.parent.album.tracks = self.parent.tracks[1:]
        self.dialog.batch_queue_button.click()
        self.parent.batch_queue_tracks.assert_not_called()
        self.assertEqual(self.dialog.tracks_list.count(), 3)
        self.assertEqual(self.dialog.selected_song_ids(), [])

    def test_row_buttons_do_not_change_selection_and_double_click_opens_details(self):
        self.dialog.enter_track_selection("song-1")
        self.dialog.track_rows[0].insert_button.click()
        self.parent.insert_track_next.assert_called_once_with(self.parent.tracks[0])
        self.assertEqual(self.dialog.selected_song_ids(), ["song-1"])
        self.dialog.exit_track_selection()
        QTest.mouseDClick(self.dialog.track_rows[0].title_label, Qt.MouseButton.LeftButton, pos=QPoint(12, 10))
        self.parent.open_track_details.assert_called_once_with(self.parent.tracks[0], self.parent.album)

    def test_album_and_single_copy_buttons_delegate_entry_ids_and_hide_single_batch_ui(self):
        self.dialog.copy_button.click()
        self.parent.copy_library_entries.assert_called_once_with([self.parent.album.id])
        self.parent.copy_library_entries.reset_mock()
        single = self.open_details(self.parent.single)
        single.copy_button.click()
        self.parent.copy_library_entries.assert_called_once_with([self.parent.single.id])
        self.assertTrue(single.selection_toolbar.isHidden())
        self.assertTrue(single.batch_toolbar.isHidden())

    def test_row_action_resolves_song_id_when_independent_entities_share_a_cached_track_id(self):
        self.parent.album.tracks = [replace(track, id="cached-reference") for track in self.parent.tracks]
        self.dialog.refresh()
        self.dialog.track_rows[2].insert_button.click()
        self.parent.insert_track_next.assert_called_once_with(self.parent.album.tracks[2])
        self.assertEqual(self.parent.insert_track_next.call_args.args[0].song_id, "song-2")

    def test_empty_album_has_disabled_batch_controls_and_can_be_copied(self):
        self.parent.album.tracks = []
        self.dialog.refresh()
        self.assertFalse(self.dialog.select_all_button.isEnabled())
        self.assertFalse(self.dialog.batch_remove_button.isEnabled())
        self.dialog.copy_button.click()
        self.parent.copy_library_entries.assert_called_once_with([self.parent.album.id])

    def test_deleted_album_disables_copy_and_all_batch_actions(self):
        self.dialog.select_all_button.click()
        self.parent.entries.remove(self.parent.album)
        self.dialog.refresh()
        self.assertFalse(self.dialog.copy_button.isEnabled())
        self.assertFalse(self.dialog.batch_copy_button.isEnabled())
        self.assertFalse(self.dialog.select_all_button.isEnabled())


if __name__ == "__main__":
    unittest.main()

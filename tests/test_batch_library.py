"""Entity identity and batch UI regression / 实体 ID 与批量操作回归。"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from bili_player.app import MainWindow
from bili_player.details import EntryDetailsDialog
from bili_player.models import LibraryEntry, Track, Store

BV = "BV1xx411c7mD"


class BatchLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = MainWindow(Path(self.directory.name) / "library.json")
        self.window._load_cover = lambda *_: None
        self.window._error = Mock()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window._quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def song(self, title="Same song"):
        track = Track(title, BV, 123, 1)
        return LibraryEntry(title, BV, tracks=[track], resolved=True)

    def populate(self):
        first, second = self.song(), self.song()
        self.window.entries = [first, second]
        self.window._normalize_library()
        self.window._render_library()
        return first, second

    def test_card_selection_ctrl_shift_and_select_all(self):
        first, second = self.populate()
        self.window.entries.append(self.song("Third"))
        self.window._normalize_library()
        self.window._render_library()
        self.window.enter_library_selection(first.id)
        self.window.clear_library_selection()
        self.app.processEvents()
        self.assertTrue(all(card.selection_box.isVisible() for card in self.window.cards._cards))
        QTest.mouseClick(self.window.cards._cards[0].selection_box, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window._selected_entry_ids, {first.id})
        self.window.select_library_entry(self.window.entries[2].id, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(len(self.window._selected_entry_ids), 3)
        self.window.select_library_entry(second.id, Qt.KeyboardModifier.ControlModifier)
        self.assertNotIn(second.id, self.window._selected_entry_ids)
        self.window.select_all_button.click()
        self.assertEqual(len(self.window._selected_entry_ids), 3)
        self.assertFalse(self.window.select_all_button.isEnabled())
        self.window.clear_library_selection()
        self.assertFalse(self.window.batch_copy_button.isEnabled())
        self.assertFalse(self.window.batch_delete_button.isEnabled())
        self.window.search.setFocus()
        QTest.keyClick(self.window.search, Qt.Key.Key_Escape)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window._selection_mode)
        self.assertFalse(self.window.selection_tools.isVisible())
        self.assertTrue(all(card.selection_box.isHidden() for card in self.window.cards._cards))

    def test_select_all_button_only_selects_filtered_cards(self):
        first, second = self.populate()
        second.title = "Other song"
        self.window.search.setText(first.title)
        self.window.enter_library_selection(first.id)
        self.assertFalse(self.window.select_all_button.isEnabled())
        self.window.clear_library_selection()
        self.window.select_all_button.click()
        self.assertEqual(self.window._selected_entry_ids, {first.id})

    def test_context_menu_enters_selection_for_songs_and_albums_with_temporary_toolbar(self):
        first, _ = self.populate()
        album = LibraryEntry("Folder", kind="album", resolved=True)
        self.window.entries.append(album)
        self.window._render_library()
        self.app.processEvents()
        self.assertFalse(hasattr(self.window, "select_button"))
        self.assertFalse(self.window.selection_tools.isVisible())
        for entry in (first, album):
            with self.subTest(kind=entry.kind):
                menu = self.window._build_entry_menu(entry)
                next(action for action in menu.actions() if action.data() == "select").trigger()
                self.assertTrue(self.window.selection_tools.isVisible())
                self.assertEqual(self.window._selected_entry_ids, {entry.id})
                self.assertTrue(self.window.batch_copy_button.isEnabled())
                self.assertTrue(self.window.batch_delete_button.isEnabled())
                self.assertFalse(self.window.batch_export_button.isEnabled())
                self.assertEqual(self.window.batch_export_button.toolTip(), "暂未实现")
                with patch.object(self.window, "copy_library_entries") as copy, \
                     patch.object(self.window, "delete_library_entries") as delete:
                    self.window.batch_copy_button.click()
                    self.window.batch_delete_button.click()
                    copy.assert_called_once_with([entry.id])
                    delete.assert_called_once_with([entry.id])
                self.window.exit_selection_button.click()
                self.assertFalse(self.window._selection_mode)
                self.assertFalse(self.window.selection_tools.isVisible())
                self.assertFalse(self.window._selected_entry_ids)
                self.assertIsNone(self.window._selection_anchor)
                self.assertTrue(all(card.selection_box.isHidden() for card in self.window.cards._cards))
                menu.deleteLater()
        self.window.set_language("en_US")
        self.assertEqual(self.window.exit_selection_button.text(), "Exit selection")
        self.assertEqual(self.window.batch_export_button.text(), "Export selected")

    def test_ctrl_cover_click_selects_without_opening_and_mode_click_toggles(self):
        first, second = self.populate()
        self.app.processEvents()
        with patch.object(self.window, "open_entry") as opened:
            QTest.mouseClick(self.window.cards._cards[0].artwork, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(self.window._selected_entry_ids, {first.id})
            QTest.mouseClick(self.window.cards._cards[1].artwork, Qt.MouseButton.LeftButton)
            self.assertEqual(self.window._selected_entry_ids, {first.id, second.id})
            opened.assert_not_called()

    def test_album_row_context_menu_uses_single_actions_and_selects_in_album(self):
        song, _ = self.populate()
        album = LibraryEntry("Folder", kind="album", tracks=[song.tracks[0]], resolved=True)
        self.window.entries.append(album)
        self.window._normalize_library()
        dialog = EntryDetailsDialog(self.window, album)
        dialog.show()
        self.app.processEvents()
        title = dialog.track_rows[0].title_label
        point = QPoint(10, 10)
        actions = []

        def choose_select(menu, _position):
            items = {action.data(): action for action in menu.actions() if action.data()}
            actions.extend(items)
            items["select"].trigger()

        try:
            QTest.mouseClick(title, Qt.MouseButton.LeftButton, pos=point)
            self.assertEqual(dialog.selected_song_ids(), [])
            with patch.object(QMenu, "exec", choose_select):
                self.app.sendEvent(title, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                                           title.mapToGlobal(point)))
            self.assertIn("edit", actions)
            self.assertIn("details", actions)
            self.assertIn("add_album", actions)
            self.assertIn("delete", actions)
            self.assertEqual(dialog.selected_song_ids(), [song.id])
            self.assertFalse(self.window._selection_mode)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_nested_song_details_dim_each_host_and_restore_on_close(self):
        song, _ = self.populate()
        album = LibraryEntry("Folder", kind="album", tracks=[song.tracks[0]], resolved=True)
        self.window.entries.append(album)
        self.window._normalize_library()
        snapshots = []

        def check_album():
            details = QApplication.activeModalWidget()
            try:
                main_layer = self.window.findChild(QWidget, "dialogDimLayer", Qt.FindChildOption.FindDirectChildrenOnly)
                snapshots.append(main_layer is not None and main_layer.isVisible())

                def check_song():
                    child = QApplication.activeModalWidget()
                    try:
                        layer = details.findChild(QWidget, "dialogDimLayer", Qt.FindChildOption.FindDirectChildrenOnly)
                        snapshots.extend((child.parentWidget() is details,
                                          layer is not None and layer.isVisible(),
                                          main_layer.isVisible()))
                    finally:
                        child.reject()

                QTimer.singleShot(0, check_song)
                self.window.open_track_details(album.tracks[0], album)
                snapshots.append(not any(layer.isVisible() for layer in details.findChildren(QWidget, "dialogDimLayer")))
                snapshots.append(main_layer.isVisible())
            finally:
                details.reject()

        QTimer.singleShot(0, check_album)
        self.window.open_entry(album)
        self.assertEqual(snapshots, [True] * 6)
        self.assertFalse(any(layer.isVisible() for layer in self.window.findChildren(QWidget, "dialogDimLayer")))

    def test_same_source_entities_and_edits_stay_independent(self):
        first, second = self.populate()
        self.assertEqual(len(self.window.entries), 2)
        self.assertIsNot(first.tracks[0], second.tracks[0])
        updated = deepcopy(first)
        updated.title = "Edited"
        self.window._commit_song_edit(first, updated)
        self.assertEqual(second.title, "Same song")
        self.assertEqual(second.tracks[0].title, "Same song")
        restored, _, _ = Store(self.window.store.path).load()
        self.assertEqual([song.title for song in restored], ["Edited", "Same song"])

    def test_copy_album_has_new_entities_and_delete_keep_exposes_unfiled(self):
        first, second = self.populate()
        album = LibraryEntry("Folder", kind="album", tracks=[first.tracks[0], second.tracks[0]], resolved=True)
        self.window.entries.append(album)
        self.window._normalize_library()
        copied = self.window.copy_library_entries([album.id])
        new_album = next(entry for entry in copied if entry.kind == "album")
        self.assertTrue({t.song_id for t in album.tracks}.isdisjoint(t.song_id for t in new_album.tracks))
        self.assertEqual(len(new_album.tracks), 2)
        self.window.delete_library_entries([album.id], album_contents="keep")
        self.assertIsNotNone(self.window._find(first.id))
        self.assertIn(first.id, self.window._visible_entry_ids)
        self.assertNotIn(new_album.tracks[0].song_id, self.window._visible_entry_ids)

    def test_delete_album_contents_removes_exact_ids_only(self):
        first, second = self.populate()
        album = LibraryEntry("Delete", kind="album", tracks=[first.tracks[0]], resolved=True)
        other = LibraryEntry("Other", kind="album", tracks=[first.tracks[0], second.tracks[0]], resolved=True)
        self.window.entries.extend([album, other])
        self.window._normalize_library()
        self.window.delete_library_entries([album.id], album_contents="delete")
        self.assertIsNone(self.window._find(first.id))
        self.assertIsNotNone(self.window._find(second.id))
        self.assertEqual([t.song_id for t in other.tracks], [second.id])

    def test_album_deletion_prompt_exposes_keep_delete_and_cancel(self):
        album = LibraryEntry("Folder", kind="album", resolved=True)
        for text, expected in (("保留单曲", "keep"), ("一并删除单曲", "delete"), (None, None)):
            with self.subTest(choice=text):
                def choose():
                    message = QApplication.activeModalWidget()
                    selected = next((b for b in message.buttons() if b.text() == text), None)
                    if selected is None:
                        message.reject()
                    else:
                        selected.click()
                QTimer.singleShot(0, choose)
                self.assertEqual(self.window._ask_album_deletion([album]), expected)

    def test_cancel_album_deletion_does_not_change_library(self):
        first, second = self.populate()
        album = LibraryEntry("Folder", kind="album", tracks=[first.tracks[0]], resolved=True)
        self.window.entries.append(album)
        self.window._normalize_library()
        ids = [entry.id for entry in self.window.entries]
        with patch.object(self.window, "_ask_album_deletion", return_value=None) as ask:
            self.assertFalse(self.window.delete_library_entries([album.id, second.id]))
            ask.assert_called_once()
        self.assertEqual([entry.id for entry in self.window.entries], ids)

    def test_album_batch_copy_remove_and_queue_use_ids(self):
        first, second = self.populate()
        album = LibraryEntry("Folder", kind="album", tracks=[first.tracks[0], second.tracks[0]], resolved=True)
        self.window.entries.append(album)
        self.window._normalize_library()
        self.window.batch_queue_tracks(album.id, [second.id])
        self.assertEqual(self.window._tracks()[0].song_id, second.id)
        self.window.batch_copy_tracks(album.id, [first.id])
        self.assertEqual(len(album.tracks), 3)
        self.assertEqual(len({track.song_id for track in album.tracks}), 3)
        self.window.batch_remove_album_tracks(album.id, [first.id])
        self.assertEqual(len(album.tracks), 2)
        self.assertIsNotNone(self.window._find(first.id))
        self.window.copy_library_entries([first.id, second.id])
        self.assertEqual(len([e for e in self.window.entries if e.kind == "single"]), 5)

    def test_queue_batch_removal_and_entity_link_preserved_on_reload(self):
        first, second = self.populate()
        self.window.queue_library_entries([first.id, second.id])
        self.assertEqual([track.song_id for track in self.window._tracks()], [first.id, second.id])
        self.window._save()
        _, queue, _ = Store(self.window.store.path).load()
        self.assertEqual([track.song_id for track in queue], [first.id, second.id])
        self.window.remove_queue_items([self.window.queue.item(0), self.window.queue.item(1)])
        self.assertEqual(self.window.queue.count(), 0)
        self.assertEqual(len(self.window.entries), 2)


if __name__ == "__main__":
    unittest.main()

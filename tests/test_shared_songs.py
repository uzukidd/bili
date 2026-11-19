"""Canonical-song integration and shuffle behavior through the main window."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import random
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from bili_player.app import MainWindow
from bili_player.catalog import normalize_library, find_song
from bili_player.details import EntryDetailsDialog
from bili_player.models import LibraryEntry, Track, Store
from bili_player.playback_order import ShuffleOrder

BV = "BV1GJ411x7h7"


class SharedSongTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = MainWindow(Path(self.temp.name) / "library.json")
        self.window._load_cover = Mock()
        self.window._error = Mock()
        track = Track("Song", BV, 123, 1, artist="Artist", date="Old date")
        self.first = LibraryEntry("First folder", kind="album", tracks=[track], resolved=True)
        self.second = LibraryEntry("Second folder", kind="album", tracks=[replace(track)], resolved=True)
        self.window.entries = [self.first, self.second]
        self.window._normalize_library()
        self.song = next(e for e in self.window.entries if e.kind == "single")
        self.window._render_library()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window._quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_edit_from_album_song_targets_single_and_updates_all_references(self):
        self.window.resolve_entry(self.first, False)
        occurrence = self.window._tracks()[0]
        updated = deepcopy(self.song)
        updated.title, updated.artist, updated.date = "Changed song", "New author", "2026 autumn"
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.value.return_value = updated
        with patch("bili_player.app.EntryDialog", return_value=dialog), patch.object(self.window, "open_album") as folder_editor:
            self.window.edit_album_track(self.first.id, self.song.id)
            folder_editor.assert_not_called()
        self.assertEqual(self.song.title, "Changed song")
        self.assertIs(self.first.tracks[0], self.song.tracks[0])
        self.assertIs(self.second.tracks[0], self.song.tracks[0])
        self.assertEqual(self.second.tracks[0].date, "2026 autumn")
        self.assertEqual(self.window._tracks()[0].id, occurrence.id)
        self.assertEqual(self.window._tracks()[0].song_id, self.song.id)
        self.assertEqual(self.window._tracks()[0].title, "Changed song")
        saved = Store(self.window.store.path).load()[0]
        canonical = find_song(saved, self.song.id)
        folders = [e for e in saved if e.kind == "album"]
        self.assertIs(folders[0].tracks[0], canonical.tracks[0])
        self.assertIs(folders[1].tracks[0], canonical.tracks[0])

    def test_edit_single_caches_cover_and_refreshes_open_details(self):
        picture = Path(self.temp.name) / "custom-cover.png"
        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(QColor("#ed7099"))
        self.assertTrue(image.save(str(picture)))
        detail = EntryDetailsDialog(self.window, self.song)
        try:
            editor = Mock()
            editor.exec.return_value = QDialog.DialogCode.Accepted
            editor.value.return_value = replace(self.song, cover=str(picture))
            with patch("bili_player.app.EntryDialog", return_value=editor):
                self.window.edit_single(self.song)

            cached = Path(self.song.cover)
            self.assertEqual(cached.parent, self.window.covers.directory)
            self.assertEqual(cached.read_bytes(), picture.read_bytes())
            saved, _, _ = Store(self.window.store.path).load()
            self.assertEqual(find_song(saved, self.song.id).cover, str(cached))
            self.window._load_cover.assert_any_call(str(cached), detail.cover_widget)
        finally:
            detail.close()
            detail.deleteLater()

    def test_clearing_artist_updates_current_song_and_queue(self):
        self.window.resolve_entry(self.song, False)
        item = self.window.queue.item(0)
        with patch.object(self.window, "_load_stream"):
            self.window.play_item(item)
        self.assertEqual(self.window.now_meta.text(), "Artist")
        self.assertEqual(self.window.mini_meta.text(), "Artist")
        self.assertEqual(item.text(), "Song\nArtist")

        updated = replace(self.song, artist="")
        self.window._commit_song_edit(self.song, updated)

        self.assertEqual(item.text(), "Song")
        self.assertEqual(item.toolTip(), "Song")
        for label in (self.window.now_meta, self.window.mini_meta):
            self.assertEqual(label.text(), "")
            self.assertTrue(label.isHidden())

    def test_open_details_update_when_shared_song_is_saved(self):
        detail = EntryDetailsDialog(self.window, self.song)
        try:
            detail.show()
            updated = deepcopy(self.song)
            updated.title = "Live update"
            updated.artist = "Live author"
            self.window._commit_song_edit(self.song, updated)
            self.assertEqual(detail.title_label.text(), "Live update")
            self.assertEqual(detail.artist_label.text(), "作者：Live author")
            self.window.delete_entry(self.song)
            self.assertFalse(detail.isVisible())
        finally:
            detail.close()
            detail.deleteLater()

    def test_late_source_resolution_cannot_overwrite_a_newer_metadata_edit(self):
        from bili_player.api import VideoInfo
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append(done)
        changed_source = deepcopy(self.song)
        changed_source.page = 2
        changed_source.tracks = []
        changed_source.resolved = False
        editor = Mock()
        editor.exec.return_value = QDialog.DialogCode.Accepted
        editor.value.return_value = changed_source
        with patch("bili_player.app.EntryDialog", return_value=editor):
            self.window.edit_single(self.song)
        newer = deepcopy(self.song)
        newer.title = "Latest manual title"
        editor.value.return_value = newer
        with patch("bili_player.app.EntryDialog", return_value=editor):
            self.window.edit_single(self.song)
        jobs[0](VideoInfo("Remote title", "", [Track("Remote", BV, 999, 2)]))
        self.assertEqual(self.song.page, 1)
        self.assertEqual(self.song.title, "Latest manual title")
        self.assertIs(self.first.tracks[0], self.song.tracks[0])

    def test_stale_folder_save_never_overwrites_new_song_metadata(self):
        snapshot = deepcopy(self.first)
        self.song.title = self.song.tracks[0].title = "Latest title"
        self.song.artist = self.song.tracks[0].artist = "Latest author"
        snapshot.title = "Renamed folder"
        snapshot.artist, snapshot.date = "Album artist", "2025 spring"
        self.window.save_album(snapshot)
        self.assertEqual(self.first.title, "Renamed folder")
        self.assertEqual(self.first.tracks[0].title, "Latest title")
        self.assertEqual(self.song.artist, "Latest author")
        saved_entries, _, _ = Store(self.window.store.path).load()
        saved_album = next(entry for entry in saved_entries if entry.id == self.first.id)
        self.assertEqual((saved_album.artist, saved_album.date), ("Album artist", "2025 spring"))
        detail = EntryDetailsDialog(self.window, self.first)
        try:
            self.assertIn("Album artist", detail.artist_label.text())
            self.assertIn("2025 spring", detail.date_label.text())
        finally:
            detail.close()
            detail.deleteLater()

    def test_folder_remove_and_delete_leave_song_entity_available(self):
        snapshot = deepcopy(self.first)
        snapshot.tracks = []
        self.window.save_album(snapshot)
        self.assertEqual(self.first.tracks, [])
        self.assertIs(self.window._find(self.song.id), self.song)
        self.window.delete_library_entries([self.second.id], album_contents="keep")
        self.assertIs(self.window._find(self.song.id), self.song)
        self.assertTrue(self.song.tracks)
        self.window.delete_entry(self.song)
        self.assertIsNone(self.window._find(self.song.id))
        self.assertEqual(self.first.tracks, [])

    def test_open_song_from_album_passes_canonical_entry(self):
        with patch.object(self.window, "open_entry") as details:
            self.window.open_track_details(self.first.tracks[0], self.first)
            details.assert_called_once_with(self.song)
        with patch.object(self.window, "edit_single") as editor:
            self.window.edit_album_track(self.second.id, self.song.id)
            editor.assert_called_once_with(self.song)

    def test_adding_existing_song_to_folder_reuses_entity_without_duplicates(self):
        self.first.tracks = []
        self.window.add_single_to_album(self.song, self.first)
        self.window.add_single_to_album(self.song, self.first)
        self.assertEqual(len(self.first.tracks), 1)
        self.assertIs(self.first.tracks[0], self.song.tracks[0])
        self.assertEqual(len([e for e in self.window.entries if e.kind == "single"]), 1)

    def test_shuffle_cycles_without_reordering_and_returns_previous(self):
        tracks = [Track(f"Q{i}", BV, 100 + i, i) for i in range(1, 5)]
        self.window._append_tracks(tracks)
        self.window._submit = lambda *_: None
        self.window.mode.setCurrentIndex(3)
        self.window.shuffle_order = ShuffleOrder(random.Random(7))
        self.window.play_item(self.window.queue.item(0))
        order = [self.window.current_id]
        for _ in range(3):
            self.window.next(automatic=True)
            order.append(self.window.current_id)
        self.assertEqual(len(set(order)), 4)
        self.assertEqual([t.id for t in self.window._tracks()], [t.id for t in tracks])
        self.window.previous()
        self.assertEqual(self.window.current_id, order[-2])
        self.window.set_mini_mode(True)
        self.assertEqual(self.window.mini_mode_button.accessibleName(), "随机播放")
        self.window.set_language("en_US")
        self.assertEqual(self.window.mini_mode_button.accessibleName(), "Shuffle")
        self.window._save()
        saved = Store(self.window.store.path)
        saved.load()
        self.assertEqual(saved.settings["play_mode"], 3)

    def test_play_next_overrides_shuffle_choice_without_reordering_other_items(self):
        queued = [Track(f"Q{i}", BV, 100 + i, i) for i in range(1, 4)]
        self.window._append_tracks(queued)
        self.window._submit = lambda *_: None
        self.window.mode.setCurrentIndex(3)
        self.window.play_item(self.window.queue.item(0))
        inserted = self.window.insert_track_next(self.song.tracks[0])
        identity = inserted.data(Qt.ItemDataRole.UserRole).id
        self.window.next(automatic=True)
        self.assertEqual(self.window.current_id, identity)
        self.assertEqual([t.id for t in self.window._tracks() if t.id != identity], [t.id for t in queued])

    def test_shuffle_respects_explicit_play_next_insertion(self):
        queued = [Track(f"Q{i}", BV, i, i) for i in range(1, 4)]
        self.window._append_tracks(queued)
        self.window._submit = lambda *_: None
        self.window.mode.setCurrentIndex(3)
        self.window.play_item(self.window.queue.item(0))
        inserted = self.window.insert_track_next(self.song.tracks[0])
        expected = inserted.data(Qt.ItemDataRole.UserRole).id
        self.window.next(automatic=True)
        self.assertEqual(self.window.current_id, expected)
        self.assertEqual(inserted.data(Qt.ItemDataRole.UserRole).song_id, self.song.id)

    def test_source_change_to_existing_source_resolves_and_preserves_independent_entities(self):
        from bili_player.api import VideoInfo

        other = LibraryEntry("Other", BV, page=2, artist="Other author", date="Other date",
                             tracks=[Track("Other", BV, 456, 2)], resolved=True)
        self.window.entries.append(other)
        self.window._normalize_library()
        self.window.resolve_entry(self.first, False)
        self.window.resolve_entry(other, False)
        queued_before = deepcopy(self.window._tracks())
        other_before = deepcopy(other)
        original_id = self.song.id
        updated = deepcopy(self.song)
        updated.bvid, updated.page = other.bvid, other.page
        updated.title, updated.artist, updated.date = "Independent edited title", "Edited author", "Edited date"
        updated.tracks, updated.resolved = [], False
        editor = Mock()
        editor.exec.return_value = QDialog.DialogCode.Accepted
        editor.value.return_value = updated
        info = VideoInfo("Remote title", "resolved-cover.png", [Track("Remote", BV, 456, 2)])
        with patch("bili_player.app.EntryDialog", return_value=editor), patch.object(self.window, "_submit") as submit:
            self.window.edit_single(self.song)
            submit.assert_called_once()
            load, loaded = submit.call_args.args
            self.assertEqual(self.song.page, 1)
            with patch("bili_player.app.BilibiliClient") as client:
                client.return_value.get_video.return_value = info
                self.assertIs(load(), info)
                client.return_value.get_video.assert_called_once_with(updated)
            loaded(info)

        self.window._error.assert_not_called()
        self.assertEqual(self.song.id, original_id)
        self.assertEqual((self.song.bvid, self.song.page), (other.bvid, other.page))
        self.assertEqual((self.song.title, self.song.artist, self.song.date),
                         ("Independent edited title", "Edited author", "Edited date"))
        self.assertEqual((self.song.tracks[0].id, self.song.tracks[0].song_id), (original_id, original_id))
        self.assertIs(self.first.tracks[0], self.song.tracks[0])
        self.assertIs(self.second.tracks[0], self.song.tracks[0])
        self.assertIsNot(self.song.tracks[0], other.tracks[0])
        self.assertEqual(other, other_before)
        self.assertIs(self.window._find(other.id), other)
        self.assertEqual(len([entry for entry in self.window.entries if entry.kind == "single"]), 2)
        queued = self.window._tracks()
        self.assertEqual([track.id for track in queued], [track.id for track in queued_before])
        self.assertEqual((queued[0].song_id, queued[0].title, queued[0].page),
                         (original_id, updated.title, 2))
        self.assertEqual(queued[1], queued_before[1])

        saved = Store(self.window.store.path).load()[0]
        saved_song, saved_other = find_song(saved, original_id), find_song(saved, other.id)
        self.assertIsNotNone(saved_song)
        self.assertIsNotNone(saved_other)
        self.assertEqual((saved_song.bvid, saved_song.page), (saved_other.bvid, saved_other.page))
        self.assertEqual(saved_other, other_before)
        self.assertIsNot(saved_song.tracks[0], saved_other.tracks[0])

    def test_custom_title_controls_and_symmetric_mini_mode_button(self):
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(self.window.title_bar.close_button.isVisible())
        self.assertGreater(self.window.title_bar.close_button.x(), self.window.title_bar.maximize_button.x())
        QTest.mouseClick(self.window.title_bar.maximize_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.isMaximized())
        QTest.mouseClick(self.window.title_bar.maximize_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.isMaximized())
        self.window.set_mini_mode(True)
        self.app.processEvents()
        self.assertEqual(self.window.mini_mode_button.size(), self.window.mini_queue_button.size())
        self.assertLess(self.window.mini_mode_button.x(), self.window.mini_previous.x())
        self.assertGreater(self.window.mini_queue_button.x(), self.window.mini_next.x())


if __name__ == "__main__":
    unittest.main()

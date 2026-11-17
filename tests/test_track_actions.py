"""Offline checks for album-track actions and the shared playback queue."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from bili_player.app import MainWindow
from bili_player.models import LibraryEntry, Store, Track

BV = "BV1GJ411x7h7"


class TrackActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "library.json"
        # No proxy server, playback, API jobs, or artwork downloads are needed.
        with patch("bili_player.app.StreamProxy"):
            self.window = MainWindow(self.path)
        self.window.playback_watchdog.stop()
        self.window._load_cover = Mock()
        self.window._submit = Mock(side_effect=AssertionError("Unexpected network task"))
        self.window._error = Mock()
        self.source = Track("Album-local title", BV, 123, 2, artist="Local artist", duration=125)
        self.album = LibraryEntry("Album", BV, "album", tracks=[self.source],
                                  cover="album-cover.png", resolved=True)
        self.window.entries = [self.album]
        self.window._normalize_library()
        self.song = self.window.song_for_track(self.source, self.album)
        self.source = self.song.tracks[0]

    def tearDown(self):
        self.window._quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def queued_tracks(self):
        tracks = [Track(f"Queued {i}", BV, 200 + i, i) for i in (1, 2, 3)]
        self.window._append_tracks(tracks, persist=False)
        return tracks

    def test_insert_follows_playing_track_and_preserves_selection_and_playback(self):
        tracks = self.queued_tracks()
        self.window.current_id = tracks[1].id
        selected = self.window.queue.item(0)
        self.window.queue.setCurrentItem(selected)
        self.window._request = 17
        self.window._want_playing = True
        self.window._loading_stream = True
        self.window._resume_position = 4500
        before = (self.window.player.playbackState(), self.window.player.source(),
                  self.window.player.position(), self.window.now_title.text())
        with ExitStack() as stack:
            calls = [stack.enter_context(patch.object(self.window.player, method))
                     for method in ("play", "pause", "stop", "setSource", "setPosition")]
            play_item = stack.enter_context(patch.object(self.window, "play_item"))
            item = self.window.insert_track_next(self.source)
            for call in calls:
                call.assert_not_called()
            play_item.assert_not_called()
        self.assertEqual([track.title for track in self.window._tracks()],
                         ["Queued 1", "Queued 2", "Album-local title", "Queued 3"])
        self.assertIs(item, self.window.queue.item(2))
        self.assertIs(self.window.queue.currentItem(), selected)
        self.assertEqual(self.window.current_id, tracks[1].id)
        self.assertEqual(self.window._request, 17)
        self.assertTrue(self.window._want_playing)
        self.assertTrue(self.window._loading_stream)
        self.assertEqual(self.window._resume_position, 4500)
        self.assertEqual((self.window.player.playbackState(), self.window.player.source(),
                          self.window.player.position(), self.window.now_title.text()), before)
        self.window._submit.assert_not_called()

    def test_repeated_inserts_have_independent_ids_and_preserve_source_metadata(self):
        original = asdict(self.source)
        first_item = self.window.insert_track_next(self.source)
        second_item = self.window.insert_track_next(self.source)
        first = first_item.data(Qt.ItemDataRole.UserRole)
        second = second_item.data(Qt.ItemDataRole.UserRole)
        self.assertEqual(len({first.id, second.id, self.source.id}), 3)
        self.assertEqual((first.song_id, second.song_id), (self.song.id, self.song.id))
        self.assertIsNot(first, self.source)
        self.assertIsNot(second, first)
        self.assertEqual(replace(first, id=self.source.id), self.source)
        self.assertEqual(replace(second, id=self.source.id), self.source)
        self.assertEqual(first_item.text(), "Album-local title\nP2 · Local artist")
        self.assertEqual(first_item.toolTip(), f"Album-local title\n{BV} · P2")
        first.title = "Queue-only edit"
        self.assertEqual(asdict(self.source), original)
        self.assertEqual(second.title, original["title"])
        self.assertEqual(self.album.tracks, [self.source])

    def test_no_playing_track_appends_even_when_another_queue_item_is_selected(self):
        tracks = self.queued_tracks()
        selected = self.window.queue.item(0)
        self.window.queue.setCurrentItem(selected)
        for current_id in (None, "removed-track-id"):
            with self.subTest(current_id=current_id):
                before_ids = [track.id for track in self.window._tracks()]
                self.window.current_id = current_id
                item = self.window.insert_track_next(self.source)
                self.assertEqual([track.id for track in self.window._tracks()[:-1]], before_ids)
                self.assertIs(self.window.queue.item(self.window.queue.count() - 1), item)
                self.assertIs(self.window.queue.currentItem(), selected)
                self.assertEqual(self.window.current_id, current_id)
        self.assertEqual([track.id for track in self.window._tracks()[:3]], [t.id for t in tracks])

    def test_empty_queue_insert_keeps_no_selection_and_uses_bvid_when_artist_missing(self):
        track = replace(self.source, artist="")
        item = self.window.insert_track_next(track)
        self.assertIs(self.window.queue.item(0), item)
        self.assertIsNone(self.window.queue.currentItem())
        self.assertIsNone(self.window.current_id)
        self.assertEqual(item.text(), f"Album-local title\nP2 · {BV}")
        self.assertEqual(self.window._request, 0)
        self.assertFalse(self.window._want_playing)

    def test_insert_persists_once_and_restores_queue_order_and_ids(self):
        tracks = self.queued_tracks()
        self.window.current_id = tracks[0].id
        before_library = [asdict(entry) for entry in self.window.entries]
        with patch.object(self.window, "_queue_changed", wraps=self.window._queue_changed) as changed, \
                patch.object(self.window.store, "save", wraps=self.window.store.save) as save:
            item = self.window.insert_track_next(self.source)
            changed.assert_called_once_with()
            save.assert_called_once()
        entries, restored, _volume = Store(self.path).load()
        self.assertEqual([asdict(entry) for entry in entries], before_library)
        self.assertEqual(restored, self.window._tracks())
        self.assertEqual([track.id for track in restored],
                         [tracks[0].id, item.data(Qt.ItemDataRole.UserRole).id, tracks[1].id, tracks[2].id])
        self.assertEqual(self.window.queue_title.text(), self.window.mini_queue_title.text())
        self.assertIn("4", self.window.queue_title.text())

    def test_mini_and_full_player_keep_the_same_queue_after_insertion(self):
        tracks = self.queued_tracks()
        queue = self.window.queue
        self.window.current_id = tracks[1].id
        self.window.queue.setCurrentItem(queue.item(1))
        self.window.set_mini_mode(True)
        item = self.window.insert_track_next(self.source)
        self.assertIs(self.window.queue, queue)
        self.assertIs(queue.parentWidget(), self.window.mini_queue_panel)
        self.assertIs(queue.item(2), item)
        self.window.set_mini_mode(False)
        self.assertIs(self.window.queue, queue)
        self.assertIs(queue.item(2), item)
        self.assertEqual(self.window.current_id, tracks[1].id)
        self.assertIs(queue.currentItem(), queue.item(1))
        self.assertEqual(len(self.window._tracks()), 4)

    def test_track_actions_use_same_canonical_song_across_folders_without_duplicates(self):
        second = LibraryEntry("Second folder", kind="album", resolved=True,
                              tracks=[replace(self.source, id="second-folder-copy", song_id="")])
        duplicate = replace(self.song, id="duplicate-single", tracks=[replace(self.source)])
        self.window.entries.extend([second, duplicate])
        self.window._normalize_library()
        self.queued_tracks()
        before = deepcopy(self.window.entries)
        queue_ids = [track.id for track in self.window._tracks()]
        with patch.object(self.window, "open_entry") as details, \
                patch.object(self.window, "edit_single") as editor, \
                patch.object(self.window, "open_album") as folder_editor:
            for folder in (self.album, second):
                with self.subTest(folder=folder.title):
                    self.window.open_track_details(folder.tracks[0], folder)
                    details.assert_called_once_with(self.song)
                    self.assertIs(details.call_args.args[0], self.song)
                    details.reset_mock()
                    self.window.edit_album_track(folder.id, folder.tracks[0].id)
                    editor.assert_called_once_with(self.song)
                    self.assertIs(editor.call_args.args[0], self.song)
                    editor.reset_mock()
            folder_editor.assert_not_called()
        self.assertEqual([entry for entry in self.window.entries if entry.kind == "single"],
                         [self.song])
        self.assertIs(self.album.tracks[0], self.song.tracks[0])
        self.assertIs(second.tracks[0], self.song.tracks[0])
        self.assertEqual(self.window.entries, before)
        self.assertEqual([track.id for track in self.window._tracks()], queue_ids)
        self.assertEqual(self.window._request, 0)
        self.window._submit.assert_not_called()

    def test_track_details_resolve_canonical_song_after_folder_membership_is_removed(self):
        stale = deepcopy(self.source)
        self.album.tracks = []
        self.song.title = self.song.tracks[0].title = "Updated song"
        with patch.object(self.window, "open_entry") as details:
            self.window.open_track_details(stale, self.album)
            details.assert_called_once_with(self.song)
            self.assertIs(details.call_args.args[0], self.song)
            details.reset_mock()
            self.window.entries = []
            self.window.open_track_details(stale, self.album)
            details.assert_not_called()

    def test_open_entry_uses_resolved_canonical_song_and_releases_dialog(self):
        dialog = QDialog(self.window)
        try:
            with patch("bili_player.app.EntryDetailsDialog", return_value=dialog) as constructor, \
                    patch.object(dialog, "exec", return_value=QDialog.DialogCode.Rejected) as execute, \
                    patch.object(dialog, "deleteLater") as cleanup, \
                    patch.object(self.window, "_ensure_entry", wraps=self.window._ensure_entry) as ensure:
                self.window.open_entry(self.song)
                self.assertIs(ensure.call_args.args[0], self.song)
                constructor.assert_called_once_with(self.window, self.song)
                execute.assert_called_once_with()
                cleanup.assert_called_once_with()
            self.window._submit.assert_not_called()
        finally:
            dialog.deleteLater()

    def test_open_entry_native_destruction_skips_cleanup(self):
        dialog = QDialog(self.window)
        with patch("bili_player.app.EntryDetailsDialog", return_value=dialog) as constructor, \
                patch.object(dialog, "exec", side_effect=lambda: sip.delete(dialog)), \
                patch.object(dialog, "deleteLater") as cleanup:
            self.window.open_entry(self.song)
            constructor.assert_called_once_with(self.window, self.song)
            cleanup.assert_not_called()
        self.assertTrue(sip.isdeleted(dialog))
        self.window._submit.assert_not_called()

    def test_open_entry_exception_still_releases_dialog(self):
        dialog = QDialog(self.window)
        try:
            with patch("bili_player.app.EntryDetailsDialog", return_value=dialog) as constructor, \
                    patch.object(dialog, "exec", side_effect=RuntimeError("Dialog failure")), \
                    patch.object(dialog, "deleteLater") as cleanup:
                with self.assertRaisesRegex(RuntimeError, "Dialog failure"):
                    self.window.open_entry(self.song)
                constructor.assert_called_once_with(self.window, self.song)
                cleanup.assert_called_once_with()
            self.window._submit.assert_not_called()
        finally:
            dialog.deleteLater()

    def test_edit_album_track_resolves_current_album_and_ignores_stale_targets(self):
        updated = replace(self.album, title="Updated album", tracks=[self.source])
        self.window.entries = [updated, self.song]
        with patch.object(self.window, "edit_single") as editor, \
                patch.object(self.window, "open_album") as folder_editor:
            self.window.edit_album_track(self.album.id, self.source.id)
            editor.assert_called_once_with(self.song)
            self.assertIs(editor.call_args.args[0], self.song)
            editor.reset_mock()
            self.window.edit_album_track("deleted-album", self.source.id)
            self.window.edit_album_track(self.album.id, "deleted-track")
            updated.tracks = []
            self.window.edit_album_track(self.album.id, self.source.id)
            self.window.entries = [replace(updated, kind="single"), self.song]
            self.window.edit_album_track(self.album.id, self.source.id)
            editor.assert_not_called()
            folder_editor.assert_not_called()


if __name__ == "__main__":
    unittest.main()

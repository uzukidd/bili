"""Offline coverage of LRCLIB preview sequencing and saved lyrics actions."""
from __future__ import annotations

import os
from collections import deque
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from bili_player.details import EntryDetailsDialog
from bili_player.lyrics import LRCLIBResult, LyricsCooldownError, LyricsEntry
from bili_player.lyrics_dialog import LyricsSearchDialog
from bili_player.models import LibraryEntry


def candidate(identity):
    return LRCLIBResult(identity, f"Candidate {identity}", "Artist", "Album", 160., False, True, True)


class WorkerQueue:
    def __init__(self):
        self.jobs = deque()

    def submit(self, work, done, _failed):
        self.jobs.append((work, done))

    def finish(self):
        work, done = self.jobs.popleft()
        done(work())


class SearchClient:
    def __init__(self):
        self.calls = []

    def search(self, query, *, limit):
        self.calls.append(("search", query, limit))
        return [candidate(1), candidate(2)]

    def get(self, identity):
        self.calls.append(("get", identity))
        return LyricsEntry(f"lrclib:{identity}", f"Candidate {identity}", artist="Artist", source="lrclib",
                           synced_lyrics="[00:00.00]\n[00:01.00]First\n[00:02.00]Second\n"
                                         "[00:03.00]Third\n[00:04.00]Fourth")


class LyricsSearchTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.worker = WorkerQueue()
        self.client = SearchClient()
        with patch.object(LyricsSearchDialog, "DEBOUNCE_MS", 0):
            self.dialog = LyricsSearchDialog(None, "Song title", submit=self.worker.submit, client=self.client)
        self.dialog.DEBOUNCE_MS = 0

    def tearDown(self):
        if not sip.isdeleted(self.dialog):
            self.dialog.reject()
            sip.delete(self.dialog)

    def fire_preview_timer(self):
        self.assertTrue(self.dialog._timer.isActive())
        self.dialog._timer.stop()
        self.dialog._dispatch()

    def test_every_candidate_is_previewed_sequentially_and_confirm_keeps_full_record(self):
        self.assertEqual(self.dialog.query_input.text(), "Song title")
        self.assertFalse(self.worker.jobs)
        self.app.processEvents()
        self.worker.finish()
        self.assertEqual(self.client.calls, [("search", "Song title", 20)])
        self.assertEqual(self.dialog.results_list.count(), 2)
        self.assertIn("Candidate 2 · Artist", self.dialog.results_list.item(1).text())
        self.assertFalse(self.dialog.confirm_button.isEnabled())
        self.assertFalse(self.worker.jobs)
        self.fire_preview_timer()
        self.worker.finish()
        self.assertIn("First\nSecond\nThird", self.dialog.results_list.item(0).text())
        self.assertNotIn("Fourth", self.dialog.results_list.item(0).text())
        self.assertNotIn("00:01", self.dialog.results_list.item(0).text())
        self.assertTrue(self.dialog.confirm_button.isEnabled())
        self.assertFalse(self.worker.jobs)
        self.fire_preview_timer()
        self.worker.finish()
        self.assertIn("First\nSecond\nThird", self.dialog.results_list.item(1).text())
        self.assertEqual(self.client.calls[1:], [("get", 1), ("get", 2)])
        self.dialog.results_list.setCurrentRow(1)
        self.dialog.confirm_button.click()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.dialog.selected_lyrics.id, "lrclib:2")
        self.assertIn("Fourth", self.dialog.selected_lyrics.synced_lyrics)

    def test_debounce_and_query_changes_discard_stale_work_and_rows(self):
        self.dialog.query_input.setText("Changed")
        self.dialog.query_input.setText("Kept query")
        self.app.processEvents()
        work, done = self.worker.jobs.popleft()
        stale = work()
        self.dialog.query_input.setText("Next query")
        self.app.processEvents()
        self.assertFalse(self.worker.jobs)
        done(stale)
        self.assertEqual(self.dialog.results_list.count(), 0)
        self.worker.finish()
        self.assertEqual(self.client.calls, [("search", "Kept query", 20), ("search", "Next query", 20)])
        self.fire_preview_timer()
        self.dialog.query_input.clear()
        self.worker.finish()
        self.assertEqual(self.dialog.results_list.count(), 0)
        self.assertFalse(self.dialog.confirm_button.isEnabled())
        self.assertEqual(len(self.client.calls), 2)

    def test_preview_waits_for_server_cooldown_then_retries_once(self):
        self.app.processEvents()
        self.worker.finish()
        with patch("bili_player.lyrics_dialog.monotonic", return_value=100) as clock:
            with patch.object(self.client, "get", side_effect=[LyricsCooldownError(5),
                                                              LyricsCooldownError(3)]) as get:
                self.fire_preview_timer()
                self.worker.finish()
                self.fire_preview_timer()
                self.assertFalse(self.worker.jobs)
                self.assertIn("5", self.dialog.status_label.text())
                clock.return_value = 105
                self.fire_preview_timer()
                self.worker.finish()
                self.assertEqual(get.call_count, 2)
                self.assertIn("加载失败", self.dialog.results_list.item(0).text())
                self.fire_preview_timer()
                self.assertFalse(self.worker.jobs)
            clock.return_value = 108
            self.fire_preview_timer()
            self.worker.finish()
        self.assertEqual(self.client.calls[-1], ("get", 2))
        self.assertIn("First", self.dialog.results_list.item(1).text())

    def test_native_destruction_cancels_pending_worker(self):
        self.app.processEvents()
        sip.delete(self.dialog)
        self.worker.finish()
        self.assertEqual(self.client.calls, [])


class LyricsOwner(QWidget):
    lyricsChanged = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.entries = []
        self.saved = {}
        self.active = None

    def _find(self, identity):
        return next((entry for entry in self.entries if entry.id == identity), None)

    def _submit(self, *_args):
        raise AssertionError("Details tests must not send network requests")

    def song_lyrics(self, _song_id):
        return tuple(self.saved.values()), self.active

    def save_song_lyrics(self, song_id, lyric):
        self.saved[lyric.id] = lyric
        return self.set_active_lyrics(song_id, lyric.id)

    def set_active_lyrics(self, song_id, identity):
        self.active = identity
        self.lyricsChanged.emit(song_id)
        return True


class SavedLyricsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_add_to_single_and_activate_another_saved_entry(self):
        owner = LyricsOwner()
        song = LibraryEntry("Saved song", "BV1GJ411x7h7")
        album = LibraryEntry("Saved album", kind="album")
        owner.entries.extend((song, album))
        album_dialog = EntryDetailsDialog(owner, album)
        details = EntryDetailsDialog(album_dialog, song, owner=owner)
        try:
            details.show()
            self.app.processEvents()
            self.assertIsNone(album_dialog.lyrics_section)
            self.assertFalse(details.no_lyrics_label.isHidden())
            first = LyricsEntry("first", "First lyrics", plain_lyrics="Hello")
            last = LyricsEntry("last", "Last lyrics", plain_lyrics="World")
            owner.save_song_lyrics(song.id, first)
            self.assertEqual(details.lyrics_list.count(), 1)
            picker = QDialog(details)
            picker.selected_lyrics = last
            picker.exec = lambda: QDialog.DialogCode.Accepted
            with patch("bili_player.lyrics_dialog.LyricsSearchDialog", return_value=picker) as create:
                details.add_lyrics_button.click()
                self.assertEqual(create.call_args.args, (details, "Saved song"))
            self.assertEqual(owner.active, last.id)
            self.assertEqual(details.lyrics_list.count(), 2)
            self.assertIn("当前歌词", details.lyrics_list.item(1).text())
            area = details.lyrics_list.visualItemRect(details.lyrics_list.item(0))
            QTest.mouseClick(details.lyrics_list.viewport(), Qt.MouseButton.LeftButton, pos=area.center())
            self.assertEqual(owner.active, first.id)
            self.assertIn("当前歌词", details.lyrics_list.item(0).text())
            self.assertNotIn("当前歌词", details.lyrics_list.item(1).text())
        finally:
            sip.delete(owner)

"""Verify saved lyrics follow the queued song in the mini player."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from bili_player.app import MainWindow
from bili_player.lyrics import LyricsEntry, LyricsStore
from bili_player.models import LibraryEntry, Track


class MiniLyricsTests(unittest.TestCase):
    def test_cover_toggle_and_selected_lyrics_follow_playback_position(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(Path(directory) / "library.json")
            try:
                track = Track("Song", "BV1xx411c7mD", 1, 1)
                entry = LibraryEntry(track.title, track.bvid, tracks=[track], resolved=True)
                other = LibraryEntry("Other", track.bvid, tracks=[Track("Other", track.bvid, 1, 1)], resolved=True)
                window.entries.extend([entry, other])
                window._normalize_library()
                window._submit = lambda *_: None
                window.resolve_entry(entry, False)
                window.resolve_entry(other, False)
                window.play_item(window.queue.item(0))
                self.assertTrue(window.save_song_lyrics(entry.id, LyricsEntry(
                    "lrclib:1", "First", synced_lyrics="[00:01]First line\n[00:05]Next line")))
                window.show()
                window.set_mini_mode(True)
                app.processEvents()
                with patch.object(window.player, "pause") as pause, patch.object(window.player, "play") as play:
                    cover_geometry = window.mini_cover.geometry()
                    QTest.mouseClick(window.mini_cover, Qt.MouseButton.LeftButton)
                    self.assertIs(window.mini_artwork.currentWidget(), window.mini_lyrics)
                    self.assertEqual(window.mini_lyrics.geometry(), cover_geometry)
                    window._position(5300)
                    self.assertEqual(window.mini_lyrics.currentItem().text(), "Next line")
                    window._position(1100)
                    self.assertEqual(window.mini_lyrics.currentItem().text(), "First line")
                    self.assertTrue(window.save_song_lyrics(entry.id, LyricsEntry(
                        "lrclib:2", "Second", plain_lyrics="Untimed lyrics")))
                    self.assertEqual(window.mini_lyrics.item(0).text(), "Untimed lyrics")
                    QTest.mouseClick(window.mini_lyrics.viewport(), Qt.MouseButton.LeftButton)
                    self.assertIs(window.mini_artwork.currentWidget(), window.mini_cover)
                    pause.assert_not_called()
                    play.assert_not_called()
                restored = LyricsStore(window.lyrics_store.path).load(entry.id)
                self.assertEqual([item.id for item in restored.entries], ["lrclib:1", "lrclib:2"])
                self.assertEqual(restored.active_id, "lrclib:2")
                window._toggle_mini_lyrics()
                window.play_item(window.queue.item(1))
                self.assertEqual(window.mini_lyrics.item(0).text(), "暂无已保存歌词")
            finally:
                window._quitting = True
                window.close()
                window.deleteLater()
                app.processEvents()

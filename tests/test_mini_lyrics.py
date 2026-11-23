"""Verify saved lyrics follow the queued song in the mini player."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from bili_player.app import MainWindow
from bili_player.lyrics import LyricsEntry, LyricsStore
from bili_player.models import LibraryEntry, Track


class MiniLyricsTests(unittest.TestCase):
    def test_settings_blocks_player_but_keeps_floating_lyrics_draggable(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(Path(directory) / "library.json")
            observed = []

            class BlockTracker(QObject):
                def eventFilter(self, watched, event):
                    if event.type() == QEvent.Type.WindowBlocked:
                        observed.append(watched)
                    return False

            tracker = BlockTracker()
            try:
                window.show()
                window.set_floating_lyrics_visible(True)
                overlay = window.floating_lyrics
                player_handle, overlay_handle = window.windowHandle(), overlay.windowHandle()
                player_handle.installEventFilter(tracker)
                overlay_handle.installEventFilter(tracker)
                result = []

                def drag_lyrics():
                    dialog = window._settings_dialog
                    try:
                        before = overlay.pos()
                        start = QPoint(24, 20)
                        delta = QPoint(30, -35)
                        QTest.mousePress(overlay_handle, Qt.MouseButton.LeftButton, pos=start)
                        QTest.mouseMove(overlay_handle, start + delta)
                        QTest.mouseRelease(overlay_handle, Qt.MouseButton.LeftButton, pos=start)
                        result.append((dialog.windowModality(), overlay.pos() - before))
                    finally:
                        dialog.reject()

                QTimer.singleShot(0, drag_lyrics)
                window.open_settings()
                self.assertEqual(result, [(Qt.WindowModality.WindowModal, QPoint(30, -35))])
                self.assertEqual(observed, [player_handle])
            finally:
                window._quitting = True
                window.close()
                window.deleteLater()
                app.processEvents()

    def test_floating_lyrics_controls_follow_the_current_song(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(Path(directory) / "library.json")
            overlay = window.floating_lyrics
            controls = (window.floating_lyrics_button, window.mini_floating_lyrics_button, window.floating_lyrics_action)
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
                window.save_song_lyrics(entry.id, LyricsEntry(
                    "first", "First", synced_lyrics="[00:01]First line\n[00:05]Next line"))
                window.show()
                self.assertTrue(overlay.isHidden())
                self.assertTrue(overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                window.floating_lyrics_button.click()
                self.assertTrue(overlay.isVisible())
                self.assertTrue(all(control.isChecked() for control in controls))
                self.assertEqual(overlay.line_label.text(), "")
                window._position(5300)
                self.assertEqual(overlay.line_label.text(), "Next line")
                window._position(1100)
                self.assertEqual(overlay.line_label.text(), "First line")
                window.save_song_lyrics(entry.id, LyricsEntry("plain", "Plain", plain_lyrics="One\nTwo"))
                self.assertIs(overlay.pages.currentWidget(), overlay.plain_lyrics)
                self.assertEqual(overlay.plain_lyrics.toPlainText(), "One\nTwo")
                window.set_mini_mode(True)
                mini_toggle = window.mini_floating_lyrics_button
                self.assertIs(window.mini_surface.layout().itemAt(0).layout().itemAt(0).widget(), mini_toggle)
                self.assertEqual(mini_toggle.text(), "")
                self.assertFalse(mini_toggle.icon().isNull())
                self.assertEqual(mini_toggle.accessibleName(), window.tr("歌词悬浮窗"))
                mini_toggle.click()
                self.assertTrue(overlay.isHidden())
                self.assertFalse(any(control.isChecked() for control in controls))
                window.floating_lyrics_action.trigger()
                self.assertTrue(overlay.isVisible())
                window.play_item(window.queue.item(1))
                self.assertEqual(overlay.line_label.text(), "暂无已保存歌词")
                window.hide()
                self.assertTrue(overlay.isVisible())
                overlay.close_button.click()
                self.assertTrue(overlay.isHidden())
                self.assertFalse(any(control.isChecked() for control in controls))
                window.floating_lyrics_action.trigger()
                self.assertTrue(overlay.isVisible())
                window._quitting = True
                window.close()
                self.assertTrue(overlay.isHidden())
            finally:
                if not window._closing:
                    window._quitting = True
                    window.close()
                window.deleteLater()
                app.processEvents()

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

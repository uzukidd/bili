"""Integration of full-library JSON exchange, absolute seek, and mini overlay."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from bili_player.app import MainWindow
from bili_player.exchange import read_library, write_library
from bili_player.models import LibraryEntry, Track, Store

BV = "BV1GJ411x7h7"


class PlayerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.window = MainWindow(self.root / "active.json")
        self.window._load_cover = Mock()
        self.window._error = Mock()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window._quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def entries(self):
        return [
            LibraryEntry("Album", kind="album", tracks=[Track("Song A", BV, 1, 1, artist="Author", date="2025 Spring")],
                         cover="https://example.test/cover.jpg", resolved=True),
            LibraryEntry("Single", BV, page=2, tracks=[Track("Song B", BV, 2, 2)],
                         cover="C:/external/cover.png", resolved=True, date="Manual date"),
        ]

    def test_export_always_includes_entire_library_not_filtered_view_or_queue(self):
        original = self.entries()
        self.window.entries = original
        self.window._append_tracks([Track("Queue only", BV, 3, 3)])
        self.window.tabs.setCurrentIndex(2)
        self.window.search.setText("Single")
        destination = self.root / "export.json"
        self.assertTrue(self.window.export_json_path(destination))
        imported = read_library(destination)
        self.assertEqual([e.title for e in imported], ["Album", "Single", "Song A"])
        self.assertIs(imported[0].tracks[0], imported[2].tracks[0])
        self.assertEqual(imported[0].tracks[0].song_id, imported[2].id)
        self.assertEqual(imported[0].tracks[0].date, "2025 Spring")
        self.assertEqual(imported[1].date, "Manual date")
        self.assertEqual(imported[0].cover, original[0].cover)
        self.assertEqual(imported[1].cover, "")
        self.assertEqual(original[1].cover, "C:/external/cover.png")
        payload = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 2)
        self.assertEqual([e["id"] for e in payload["entries"]], [e.id for e in original])
        self.assertEqual(payload["entries"][0]["song_ids"], [original[2].id])
        self.assertNotIn("tracks", payload["entries"][0])
        self.assertTrue(set(e.id for e in imported).isdisjoint(e.id for e in original))
        self.assertNotIn("Queue only", destination.read_text(encoding="utf-8"))
        self.assertNotIn("settings", destination.read_text(encoding="utf-8"))

    def test_import_appends_all_entries_without_playback_and_persists_new_ids(self):
        incoming = self.entries()
        destination = self.root / "incoming.json"
        write_library(destination, incoming)
        existing = LibraryEntry("Keep me", kind="album", resolved=True)
        self.window.entries = [existing]
        queued = Track("Playing", BV, 99, 1)
        self.window._append_tracks([queued])
        self.window.current_id = queued.id
        request = self.window._request
        with patch.object(self.window.player, "stop") as stop, patch.object(self.window.player, "setSource") as source:
            self.assertTrue(self.window.import_json_path(destination))
            stop.assert_not_called()
            source.assert_not_called()
        self.assertEqual([e.title for e in self.window.entries], ["Keep me", "Album", "Single", "Song A"])
        self.assertEqual(self.window.entries[0].id, existing.id)
        self.assertNotEqual(self.window.entries[1].id, incoming[0].id)
        self.assertNotEqual(self.window.entries[1].tracks[0].id, incoming[0].tracks[0].id)
        self.assertTrue(set(e.id for e in self.window.entries[1:]).isdisjoint(e.id for e in incoming))
        self.assertIs(self.window.entries[1].tracks[0], self.window.entries[3].tracks[0])
        self.assertEqual(self.window.entries[1].tracks[0].song_id, self.window.entries[3].id)
        self.assertEqual(self.window.current_id, queued.id)
        self.assertEqual(self.window._request, request)
        self.assertEqual([t.id for t in self.window._tracks()], [queued.id])
        restored = Store(self.window.store.path).load()[0]
        self.assertEqual([e.title for e in restored], ["Keep me", "Album", "Single", "Song A"])
        self.assertIs(restored[1].tracks[0], restored[3].tracks[0])

    def test_import_merges_existing_song_authority_and_keeps_new_folder_reference(self):
        existing = LibraryEntry("Keep canonical metadata", BV, tracks=[Track("Keep canonical metadata", BV, 9, 1)],
                                artist="Manual artist", date="Manual date", resolved=True)
        self.window.entries = [existing]
        self.window._normalize_library()
        self.window.resolve_entry(existing, False)
        occurrence = self.window._tracks()[0]
        self.window.current_id = occurrence.id
        request = self.window._request
        destination = self.root / "incoming.json"
        write_library(destination, self.entries())
        with patch.object(self.window.player, "stop") as stop, patch.object(self.window.player, "setSource") as source:
            self.assertTrue(self.window.import_json_path(destination))
            stop.assert_not_called()
            source.assert_not_called()
        self.assertEqual([entry.title for entry in self.window.entries],
                         ["Keep canonical metadata", "Album", "Single"])
        song, folder, other = self.window.entries
        self.assertEqual(song.id, existing.id)
        self.assertEqual((song.title, song.artist, song.date),
                         ("Keep canonical metadata", "Manual artist", "Manual date"))
        self.assertIs(folder.tracks[0], song.tracks[0])
        self.assertEqual((other.bvid, other.page), (BV, 2))
        self.assertEqual(len([e for e in self.window.entries if e.kind == "single" and e.page == 1]), 1)
        self.assertEqual(self.window.current_id, occurrence.id)
        self.assertEqual(self.window._request, request)
        self.assertEqual([(t.id, t.song_id) for t in self.window._tracks()], [(occurrence.id, existing.id)])
        restored = Store(self.window.store.path).load()[0]
        self.assertEqual(restored[0].id, existing.id)
        self.assertIs(restored[1].tracks[0], restored[0].tracks[0])

    def test_invalid_import_and_failed_save_do_not_change_library(self):
        existing = self.entries()
        self.window.entries = existing
        self.window._save()
        original = self.window.store.path.read_bytes()
        path = self.root / "bad.json"
        path.write_text('{"format":"bili-music-library","version":1,"entries":[{}]}', encoding="utf-8")
        self.assertFalse(self.window.import_json_path(path))
        self.assertIs(self.window.entries, existing)
        self.assertEqual(self.window.store.path.read_bytes(), original)
        write_library(path, self.entries())
        with patch.object(self.window.store, "save", side_effect=OSError("disk full")):
            self.assertFalse(self.window.import_json_path(path))
        self.assertIs(self.window.entries, existing)
        self.assertEqual(self.window.store.path.read_bytes(), original)

    def test_export_guard_protects_active_library(self):
        self.window.entries = self.entries()
        self.window._save()
        original = self.window.store.path.read_bytes()
        self.assertFalse(self.window.export_json_path(self.window.store.path))
        self.assertEqual(self.window.store.path.read_bytes(), original)
        self.window._error.assert_called_once()

    def test_file_dialog_cancel_does_not_import_or_export(self):
        with patch("bili_player.app.QFileDialog.getSaveFileName", return_value=("", "")), \
             patch("bili_player.app.write_library") as writer:
            self.window.export_json()
            writer.assert_not_called()
        with patch("bili_player.app.QFileDialog.getOpenFileName", return_value=("", "")), \
             patch("bili_player.app.read_library") as reader:
            self.window.import_json()
            reader.assert_not_called()

    def test_full_and_mini_click_seek_once_and_keep_paused_state(self):
        self.window._duration(200000)
        for mini in (False, True):
            self.window.set_mini_mode(mini)
            self.app.processEvents()
            slider = self.window.mini_seek if mini else self.window.seek
            slider.setEnabled(True)
            with patch.object(self.window.player, "setPosition") as position, \
                 patch.object(self.window.player, "play") as play:
                QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() // 2, slider.height() // 2))
                self.assertEqual(position.call_count, 1)
                self.assertAlmostEqual(position.call_args.args[0], 100000, delta=3000)
                self.assertEqual(self.window.seek.value(), self.window.mini_seek.value())
                play.assert_not_called()
        self.assertEqual(self.window._last_played_position, self.window.mini_seek.value())

    def test_overlay_dismiss_and_resize_preserve_cover_and_shared_queue(self):
        self.window._append_tracks([Track("A", BV, 1, 1)])
        self.window.set_mini_mode(True)
        self.app.processEvents()
        original = self.window.size()
        with patch.object(self.window, "toggle_play") as toggle:
            self.window.toggle_mini_queue()
            self.app.processEvents()
            panel = self.window.mini_queue_panel
            overlap = panel.geometry().intersected(self.window.mini_cover.geometry())
            self.assertFalse(overlap.isEmpty())
            point = overlap.center()
            self.assertTrue(self.window.queue.isAncestorOf(self.window.mini_surface.childAt(point)) or
                            panel.isAncestorOf(self.window.mini_surface.childAt(point)))
            QTest.keyClick(self.window.queue, Qt.Key.Key_Escape)
            self.assertFalse(panel.isVisible())
            self.window.toggle_mini_queue()
            QTest.mouseClick(self.window.mini_cover, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
            self.assertFalse(panel.isVisible())
            toggle.assert_not_called()
        self.assertEqual(self.window.size(), original)
        self.window.toggle_mini_queue()
        self.window.resize(420, 640)
        self.app.processEvents()
        self.app.processEvents()
        panel = self.window.mini_queue_panel
        self.assertTrue(self.window.mini_surface.rect().contains(panel.geometry()))
        self.assertLess(panel.geometry().bottom(), self.window.mini_play.geometry().top())
        self.assertEqual(self.window.queue.count(), 1)


if __name__ == "__main__":
    unittest.main()

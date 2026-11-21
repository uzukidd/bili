"""Integration of library packages, absolute seek, and mini overlay."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from bili_player.app import MainWindow
from bili_player.exchange import read_library_package, write_library_package
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
                         resolved=True, date="Manual date"),
        ]

    def test_export_selected_album_includes_its_songs_and_restores_local_covers(self):
        original = self.entries()
        self.window.entries = original
        self.window._append_tracks([Track("Queue only", BV, 3, 3)])
        album, single, member = original
        picture = self.root / "original-art.png"
        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(0xFF1A2B3C)
        self.assertTrue(image.save(str(picture)))
        album.cover = member.cover = str(picture)
        destination = self.root / "export.zip"
        self.window._render_library()
        self.window.enter_library_selection(album.id)
        with patch("bili_player.app.QFileDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.selectedFiles.return_value = [str(destination)]
            self.window.batch_export_button.click()
        self.window._error.assert_not_called()
        with zipfile.ZipFile(destination) as archive:
            payload = json.loads(archive.read("library.json"))
            names = archive.namelist()
            self.assertEqual(len(names), 2)
            self.assertEqual(archive.read(payload["entries"][0]["cover"]), picture.read_bytes())
        self.assertEqual([entry["id"] for entry in payload["entries"]], [album.id, member.id])
        self.assertEqual(payload["entries"][0]["song_ids"], [member.id])
        self.assertNotIn("settings", payload)
        self.assertNotIn("tracks", payload["entries"][0])
        self.assertEqual(album.cover, str(picture))

        imported = read_library_package(destination, self.window.covers)
        self.assertEqual([entry.title for entry in imported], ["Album", "Song A"])
        self.assertIs(imported[0].tracks[0], imported[1].tracks[0])
        self.assertEqual(imported[0].tracks[0].date, "2025 Spring")
        cover = Path(imported[0].cover)
        self.assertEqual(cover.parent, self.window.covers.directory.resolve())
        self.assertEqual(cover.read_bytes(), picture.read_bytes())
        self.assertEqual(imported[1].cover, imported[0].cover)

    def write_package(self, destination, entries):
        write_library_package(destination, entries, [entry.id for entry in entries])

    def test_import_appends_all_entries_without_playback_and_persists_ids(self):
        incoming = self.entries()
        destination = self.root / "incoming.zip"
        self.write_package(destination, incoming)
        existing = LibraryEntry("Keep me", kind="album", resolved=True)
        self.window.entries = [existing]
        queued = Track("Playing", BV, 99, 1)
        self.window._append_tracks([queued])
        self.window.current_id = queued.id
        request = self.window._request
        with patch.object(self.window.player, "stop") as stop, patch.object(self.window.player, "setSource") as source:
            self.assertTrue(self.window.import_library_path(destination))
            stop.assert_not_called()
            source.assert_not_called()
        self.assertEqual([e.title for e in self.window.entries], ["Keep me", "Album", "Single", "Song A"])
        self.assertEqual(self.window.entries[0].id, existing.id)
        self.assertEqual(self.window.entries[1].id, incoming[0].id)
        self.assertEqual(self.window.entries[1].tracks[0].id, incoming[0].tracks[0].id)
        self.assertEqual([e.id for e in self.window.entries[1:3]], [e.id for e in incoming])
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
        destination = self.root / "incoming.zip"
        incoming = self.entries()
        incoming[0].tracks[0].song_id = existing.id
        self.write_package(destination, incoming)
        with patch.object(self.window.player, "stop") as stop, patch.object(self.window.player, "setSource") as source:
            self.assertTrue(self.window.import_library_path(destination))
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

    def test_import_same_source_with_distinct_ids_keeps_independent_songs(self):
        existing = LibraryEntry("Keep original", BV, tracks=[Track("Keep original", BV, 1, 1)], resolved=True)
        self.window.entries = [existing]
        self.window._normalize_library()
        destination = self.root / "incoming.zip"
        self.write_package(destination, self.entries())
        self.assertTrue(self.window.import_library_path(destination))
        self.assertEqual([entry.title for entry in self.window.entries],
                         ["Keep original", "Album", "Single", "Song A"])
        self.assertIs(self.window.entries[1].tracks[0], self.window.entries[3].tracks[0])
        self.assertIsNot(self.window.entries[1].tracks[0], self.window.entries[0].tracks[0])
        identifiers = [entry.id for entry in self.window.entries]
        self.assertTrue(self.window.import_library_path(destination))
        self.assertEqual([entry.id for entry in self.window.entries], identifiers)

    def test_invalid_import_and_failed_save_do_not_change_library(self):
        existing = self.entries()
        self.window.entries = existing
        self.window._save()
        original = self.window.store.path.read_bytes()
        path = self.root / "bad.json"
        path.write_text('{"format":"bili-music-library","version":1,"entries":[{}]}', encoding="utf-8")
        self.assertFalse(self.window.import_library_path(path))
        self.assertIs(self.window.entries, existing)
        self.assertEqual(self.window.store.path.read_bytes(), original)
        self.write_package(path, self.entries())
        with patch.object(self.window.store, "save", side_effect=OSError("disk full")):
            self.assertFalse(self.window.import_library_path(path))
        self.assertIs(self.window.entries, existing)
        self.assertEqual(self.window.store.path.read_bytes(), original)

    def test_export_guard_protects_active_library(self):
        self.window.entries = self.entries()
        self.window._save()
        original = self.window.store.path.read_bytes()
        self.assertFalse(self.window.export_selection_path(self.window.store.path, [self.window.entries[0].id]))
        self.assertEqual(self.window.store.path.read_bytes(), original)
        self.window._error.assert_called_once()

    def test_file_dialog_cancel_does_not_import_or_export(self):
        self.window.entries = self.entries()
        self.window._render_library()
        with patch("bili_player.app.QFileDialog") as dialog, \
             patch("bili_player.app.write_library_package") as writer:
            self.window.export_selection()
            dialog.assert_not_called()
            self.window.enter_library_selection(self.window.entries[0].id)
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.window.batch_export_button.click()
            dialog.return_value.exec.assert_called_once_with()
            writer.assert_not_called()
        with patch("bili_player.app.QFileDialog.getOpenFileName", return_value=("", "")) as chooser, \
             patch("bili_player.app.read_library_package") as reader:
            self.window.import_library_button.click()
            chooser.assert_called_once()
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

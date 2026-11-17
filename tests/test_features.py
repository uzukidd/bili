"""Application-level album snapshots, settings, and retained cover art."""
from __future__ import annotations

import ast
from copy import deepcopy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QColor
from PyQt6.QtWidgets import QApplication

from bili_player.api import VideoInfo
from bili_player.app import MainWindow
from bili_player.covers import CoverCache
from bili_player.dialogs import AlbumDialog, EntryDialog
from bili_player.i18n import ENGLISH
from bili_player.models import LibraryEntry, Store, Track

BV = "BV1GJ411x7h7"
BV2 = "BV1xx411c7mD"


class FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "library.json"
        self.window = MainWindow(self.path)
        self.window._load_cover = lambda *_: None
        self.window._error = Mock()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def tracks(self):
        return [Track(f"P{i}", BV, 100 + i, i, artist="Artist") for i in (1, 2, 3)]

    def test_english_catalog_covers_owned_ui_strings(self):
        root = Path(__file__).resolve().parents[1] / "bili_player"
        missing = set()
        for name in ("app.py", "dialogs.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                method = node.func.attr
                if method not in {"tr", "_text", "_button", "_bind", "_status"}:
                    continue
                position = 1 if method == "_bind" else 0
                if len(node.args) <= position:
                    continue
                for part in ast.walk(node.args[position]):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        if any("\u4e00" <= char <= "\u9fff" for char in part.value) and part.value not in ENGLISH:
                            missing.add(part.value)
        self.assertEqual(missing, set())

    def test_theme_language_apply_immediately_and_survive_reopen(self):
        track = self.tracks()[0]
        self.window._append_tracks([track])
        self.window.current_id = track.id
        self.window.now_title.setText("Custom now playing")
        generation = self.window._request
        with patch.object(self.window.player, "stop") as stop, patch.object(self.window.player, "setSource") as source:
            self.window.set_theme("dark")
            self.window.set_language("en_US")
            stop.assert_not_called()
            source.assert_not_called()
        self.assertEqual(self.window.current_id, track.id)
        self.assertEqual(self.window._request, generation)
        self.assertEqual(self.window.now_title.text(), "Custom now playing")
        self.assertEqual(self.window.tabs.tabText(1), "Albums")
        self.assertEqual(self.window.mode.itemText(1), "Loop queue")
        self.assertEqual(self.window.queue_title.text(), "Queue · 1")
        self.assertIn("#151923", self.app.styleSheet())
        dialog = EntryDialog(self.window)
        self.assertEqual(dialog.windowTitle(), "Import music")
        self.assertEqual(dialog.save_button.text(), "Save")
        dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.window = MainWindow(self.path)
        self.window._error = Mock()
        self.assertEqual((self.window.theme, self.window.language), ("dark", "en_US"))
        self.assertEqual([t.id for t in self.window._tracks()], [track.id])
        self.window.set_theme("light")
        self.window.set_language("zh_CN")
        self.assertEqual(self.window.tabs.tabText(1), "专辑")
        self.assertIn("#f5f6fa", self.app.styleSheet())

    def test_empty_album_never_calls_video_api_even_after_all_tracks_removed(self):
        album = self.window.new_album(title="My empty album")
        self.assertEqual(album.bvid, "")
        self.assertTrue(album.resolved)
        # An imported album intentionally emptied must also remain empty.
        imported = LibraryEntry("Emptied", BV, "album", tracks=[], resolved=True)
        self.window.entries.append(imported)
        with patch.object(self.window, "_submit") as submit:
            self.window.resolve_entry(album, False)
            self.window.resolve_entry(imported, False)
            submit.assert_not_called()
        self.assertEqual(self.window.queue.count(), 0)
        self.window._save()
        restored = Store(self.path).load()[0]
        self.assertTrue(all(a.resolved and a.tracks == [] for a in restored))

    def test_import_album_uses_video_cover_and_playback_retains_edits(self):
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done, error))
        self.window.import_entry(LibraryEntry(BV, BV, "album"))
        jobs[0][1](VideoInfo("Video album title", "https://i0.hdslb.com/cover.jpg", self.tracks()))
        album = self.window.entries[0]
        self.assertEqual(album.title, "Video album title")
        self.assertEqual(album.cover, "https://i0.hdslb.com/cover.jpg")
        self.assertEqual(len(album.tracks), 3)
        dialog = AlbumDialog(self.window, album, [])
        dialog.title_input.setText("Edited album")
        dialog.tracks_list.item(1).setSelected(True)
        dialog.remove_selected()
        dialog.tracks_list.clearSelection()
        dialog.tracks_list.item(1).setSelected(True)
        dialog.move_selected(-1)
        dialog.tracks_list.item(0).data(Qt.ItemDataRole.UserRole).title = "Local rename"
        # QListWidget stores Python dataclasses by reference.
        edited = dialog.value()
        self.window.save_album(edited)
        before = len(jobs)
        self.window.resolve_entry(edited, False)
        self.assertEqual(len(jobs), before)
        self.assertEqual([t.page for t in self.window._tracks()], [3, 1])
        self.assertEqual(self.window._tracks()[0].title, "Local rename")
        self.assertEqual(len(self.window.entries[0].tracks), 2)
        self.assertEqual(self.window.entries[0].cover, album.cover)
        restored = Store(self.path).load()[0][0]
        self.assertEqual([t.page for t in restored.tracks], [3, 1])
        dialog.reject()
        dialog.deleteLater()

    def test_add_single_to_empty_album_copies_and_queue_ids_are_unique(self):
        source = LibraryEntry("Custom single", BV2, tracks=[Track("Custom single", BV2, 55, 1)], resolved=True)
        self.window.entries.append(source)
        album = self.window.new_album(title="Mixed")
        with patch.object(self.window, "_submit") as submit:
            self.window.add_single_to_album(source, album)
            submit.assert_not_called()
        self.assertEqual(len(album.tracks), 1)
        self.assertNotEqual(source.tracks[0].id, album.tracks[0].id)
        album.tracks[0].title = "Album-only title"
        self.assertEqual(source.tracks[0].title, "Custom single")
        self.window.resolve_entry(album, False)
        self.window.resolve_entry(album, False)
        queue = self.window._tracks()
        self.assertEqual(len({t.id for t in queue}), 2)
        self.assertNotEqual(queue[0].id, album.tracks[0].id)
        queue[0].title = "Queue-only title"
        self.assertEqual(album.tracks[0].title, "Album-only title")

    def test_migrated_album_materializes_once_and_deleted_parts_do_not_return(self):
        album = LibraryEntry("Legacy", BV, "album")
        self.window.entries.append(album)
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done))
        self.window.resolve_entry(album, False)
        jobs[0][1](VideoInfo("Remote", "", self.tracks()))
        self.assertTrue(album.resolved)
        self.window.clear_queue()
        album.tracks = [album.tracks[-1]]
        album.tracks[0].title = "Kept only"
        self.window.resolve_entry(album, False)
        self.assertEqual(len(jobs), 1)
        self.assertEqual([(t.page, t.title) for t in self.window._tracks()], [(3, "Kept only")])

    def test_deleted_pending_album_is_not_restored_or_queued(self):
        album = LibraryEntry("Legacy", BV, "album")
        self.window.entries.append(album)
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done))
        self.window.resolve_entry(album, False)
        self.window.entries.clear()
        jobs[0][1](VideoInfo("Remote", "", self.tracks()))
        self.assertEqual(self.window.entries, [])
        self.assertEqual(self.window.queue.count(), 0)

    def test_async_single_add_survives_concurrent_album_save_and_cancel(self):
        for accept in (True, False):
            with self.subTest(accept=accept):
                source = LibraryEntry("Pending single", BV2)
                album = LibraryEntry("Original album", "", "album", tracks=[self.tracks()[0]], resolved=True)
                self.window.entries = [source, album]
                jobs = []
                self.window._submit = lambda fn, done, error=None: jobs.append((fn, done))
                self.window.add_single_to_album(source, album)
                snapshot = deepcopy(album)
                snapshot.title = "Edited album"
                editor = Mock()
                editor.value.return_value = snapshot

                def modal():
                    jobs[0][1](VideoInfo("Pending single", "", [Track("Remote", BV2, 99, 1)]))
                    self.assertEqual(len(album.tracks), 1)
                    return AlbumDialog.DialogCode.Accepted if accept else AlbumDialog.DialogCode.Rejected

                editor.exec.side_effect = modal
                with patch("bili_player.app.AlbumDialog", return_value=editor):
                    self.window.open_album(album)
                saved = self.window._find(album.id)
                self.assertEqual([track.title for track in saved.tracks], ["P1", "Pending single"])
                self.assertEqual(saved.title, "Edited album" if accept else "Original album")
                self.assertEqual(self.window._deferred_album_additions, {})
                restored = Store(self.path).load()[0][-1]
                self.assertEqual(len(restored.tracks), 2)

    def test_reselecting_modified_local_cover_imports_new_content(self):
        source = Path(self.directory.name) / "mutable.png"
        image = QImage(20, 20, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        image.save(str(source))
        cache = CoverCache(Path(self.directory.name) / "covers")
        original = cache.fetch(str(source))
        image.fill(QColor("blue"))
        image.save(str(source))
        updated = cache.fetch(str(source))
        self.assertNotEqual(original, updated)
        self.assertEqual(QImage(str(original)).pixelColor(1, 1), QColor("red"))
        self.assertEqual(QImage(str(updated)).pixelColor(1, 1), QColor("blue"))

    def test_local_cover_is_retained_and_managed_path_is_stable(self):
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("#fa85ad"))
        external = Path(self.directory.name) / "external.png"
        self.assertTrue(image.save(str(external)))
        album = self.window.new_album(title="Art")
        album.cover = str(external)
        self.window.save_album(album)
        retained = Path(album.cover)
        self.assertNotEqual(retained, external)
        self.assertTrue(retained.is_file())
        external.unlink()
        self.window.save_album(album)
        self.assertEqual(Path(album.cover), retained)
        self.assertFalse(QImage(str(retained)).isNull())
        self.assertEqual(len(list(retained.parent.glob("*.img"))), 1)

    def test_cover_download_cached_and_invalid_data_rejected(self):
        image = QImage(80, 80, QImage.Format.Format_RGB32)
        image.fill(QColor("#7099ed"))
        fixture = Path(self.directory.name) / "image.png"
        image.save(str(fixture))
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [fixture.read_bytes()]
        cache = CoverCache(Path(self.directory.name) / "art")
        with patch("bili_player.covers.requests.get", return_value=response) as get:
            first = cache.fetch("https://cdn.example.test/cover.jpg")
            second = cache.fetch("https://cdn.example.test/cover.jpg")
            self.assertEqual(first, second)
            get.assert_called_once()
        response.iter_content.return_value = [b"not an image"]
        with patch("bili_player.covers.requests.get", return_value=response), self.assertRaises(ValueError):
            cache.fetch("https://cdn.example.test/broken.jpg")


if __name__ == "__main__":
    unittest.main()

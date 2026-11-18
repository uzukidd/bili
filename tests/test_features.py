"""Application-level shared songs, album folders, settings, and retained cover art."""
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
from bili_player.details import EntryDetailsDialog
from bili_player.dialogs import AlbumDialog, EntryDialog
from bili_player.i18n import ENGLISH
from bili_player.lists import ListImportResult
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
        self.window._quitting = True
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
        self.window._quitting = True
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

    def test_list_import_commit_creates_shared_songs_without_changing_queue(self):
        album = LibraryEntry("Imported list", kind="album", tracks=self.tracks(),
                             cover="https://example.test/cover.jpg", resolved=True)
        self.window._append_tracks([self.tracks()[0]])
        queue_ids = [t.id for t in self.window._tracks()]
        result = ListImportResult(album, [], 3)
        self.window.commit_list_import(result)
        saved = self.window.entries[0]
        self.assertIsNot(saved, album)
        self.assertTrue(saved.resolved)
        self.assertEqual(saved.bvid, "")
        self.assertEqual(saved.title, "Imported list")
        self.assertEqual([t.page for t in saved.tracks], [1, 2, 3])
        self.assertEqual(len(self.window.entries), 4)
        songs = [self.window.song_for_track(track) for track in saved.tracks]
        for track, song in zip(saved.tracks, songs):
            self.assertIs(track, song.tracks[0])
            self.assertEqual(track.song_id, song.id)
        saved.tracks.pop(1)
        edited = deepcopy(songs[0])
        edited.title = "Edited imported song"
        self.window._commit_song_edit(songs[0], edited)
        restored_entries, restored_queue, _ = Store(self.path).load()
        restored = restored_entries[0]
        self.assertEqual([t.page for t in restored.tracks], [1, 3])
        self.assertEqual(restored.tracks[0].title, "Edited imported song")
        self.assertEqual(len(restored_entries), 4)
        self.assertIs(restored.tracks[0], next(e for e in restored_entries if e.id == songs[0].id).tracks[0])
        self.assertEqual([t.page for t in album.tracks], [1, 2, 3])
        self.assertEqual([t.id for t in self.window._tracks()], queue_ids)
        self.assertEqual([t.id for t in restored_queue], queue_ids)
        # A pre-existing unlinked queue snapshot with the same source is independent.
        self.assertEqual(self.window._tracks()[0].title, "P1")

    def test_list_song_video_covers_survive_save_and_same_source_stays_independent(self):
        tracks = self.tracks()
        existing = LibraryEntry("Existing", BV, page=1, tracks=[deepcopy(tracks[0])],
                                cover="https://example.test/custom.jpg", resolved=True)
        self.window.entries.append(existing)
        album = LibraryEntry("List", kind="album", tracks=tracks,
                             cover="https://example.test/list.jpg", resolved=True)
        songs = [LibraryEntry(t.title, t.bvid, page=t.page, tracks=[t],
                              cover=f"https://example.test/video-{t.page}.jpg", resolved=True)
                 for t in tracks]
        self.window.commit_list_import(ListImportResult(album, [], 3, songs))
        self.assertEqual(self.window.song_for_track(tracks[0]).cover, songs[0].cover)
        self.assertEqual(existing.cover, "https://example.test/custom.jpg")
        self.assertNotEqual(existing.id, songs[0].id)
        self.assertEqual(self.window.song_for_track(tracks[1]).cover, songs[1].cover)
        restored, _, _ = Store(self.path).load()
        saved_songs = {e.id: e for e in restored if e.kind == "single"}
        self.assertEqual(saved_songs[existing.id].cover, "https://example.test/custom.jpg")
        self.assertEqual(saved_songs[songs[0].id].cover, "https://example.test/video-1.jpg")
        self.assertEqual(saved_songs[songs[1].id].cover, "https://example.test/video-2.jpg")
        self.assertEqual(next(e for e in restored if e.kind == "album").cover, album.cover)

    def test_cancelled_list_dialog_does_not_create_album(self):
        with patch("bili_player.app.ListImportDialog") as dialog:
            dialog.return_value.exec.return_value = AlbumDialog.DialogCode.Rejected
            dialog.return_value.import_result = None
            self.window.import_list()
        self.assertEqual(self.window.entries, [])
        self.assertEqual(self.window.queue.count(), 0)
        self.assertFalse(self.path.exists())

    def test_failed_save_does_not_report_list_import_success(self):
        result = ListImportResult(LibraryEntry("List", kind="album", tracks=self.tracks(), resolved=True), [], 3)
        with patch.object(self.window.store, "save", side_effect=OSError("disk full")):
            self.window.commit_list_import(result)
        self.window._error.assert_called_once()
        self.assertIn("保存失败", self.window.status.text())

    def test_single_manual_metadata_survives_import_edit_and_source_reload(self):
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done, error))
        entry = LibraryEntry(BV, BV, artist="手填作者", date="2025 春")
        self.window.import_entry(entry)
        jobs[0][1](VideoInfo("Video title", "", [Track("Video title", BV, 1, 1)]))
        current = self.window.entries[0]
        self.assertEqual((current.title, current.artist, current.date), ("Video title", "手填作者", "2025 春"))
        self.assertEqual((current.tracks[0].artist, current.tracks[0].date), ("手填作者", "2025 春"))
        self.window.resolve_entry(current, False)
        queued = self.window._tracks()[0]
        edited = deepcopy(current)
        edited.artist, edited.date = "新作者", "某天"
        editor = Mock()
        editor.exec.return_value = AlbumDialog.DialogCode.Accepted
        editor.value.return_value = edited
        with patch("bili_player.app.EntryDialog", return_value=editor):
            self.window.edit_single(current)
        saved = self.window.entries[0]
        self.assertEqual((saved.tracks[0].artist, saved.tracks[0].date), ("新作者", "某天"))
        queued_after_edit = self.window._tracks()[0]
        self.assertEqual((queued_after_edit.artist, queued_after_edit.date), ("新作者", "某天"))
        self.assertEqual(queued_after_edit.id, queued.id)
        self.assertEqual(queued_after_edit.song_id, saved.id)
        self.assertNotEqual(queued_after_edit.id, saved.id)
        saved.resolved, saved.tracks = False, []
        self.window._ensure_entry(saved, lambda result: None)
        jobs[-1][1](VideoInfo("New source", "", [Track("New source", BV, 2, 1)]))
        self.assertEqual((saved.tracks[0].artist, saved.tracks[0].date), ("新作者", "某天"))
        restored = Store(self.path).load()[0][0]
        self.assertEqual((restored.artist, restored.date), ("新作者", "某天"))

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
        self.assertEqual(len(self.window.entries), 4)
        dialog = AlbumDialog(self.window, album, [e for e in self.window.entries if e.kind == "single"])
        dialog.title_input.setText("Edited album")
        dialog.tracks_list.item(1).setSelected(True)
        dialog.remove_selected()
        dialog.tracks_list.clearSelection()
        dialog.tracks_list.item(1).setSelected(True)
        dialog.move_selected(-1)
        song = self.window.song_for_track(album.tracks[2])
        updated = deepcopy(song)
        updated.title = "Local rename"
        editor = Mock()
        editor.exec.return_value = AlbumDialog.DialogCode.Accepted
        editor.value.return_value = updated
        with patch("bili_player.app.EntryDialog", return_value=editor):
            dialog.edit_selected()
        self.assertEqual(song.title, "Local rename")
        self.assertEqual(album.tracks[2].title, "Local rename")
        # Saving a stale editor snapshot must only save folder fields and references.
        dialog.tracks_list.item(0).data(Qt.ItemDataRole.UserRole).title = "Stale snapshot title"
        edited = dialog.value()
        self.window.save_album(edited)
        before = len(jobs)
        self.window.resolve_entry(self.window._find(album.id), False)
        self.assertEqual(len(jobs), before)
        self.assertEqual([t.page for t in self.window._tracks()], [3, 1])
        self.assertEqual(self.window._tracks()[0].title, "Local rename")
        self.assertEqual(len(self.window.entries[0].tracks), 2)
        self.assertEqual(self.window.entries[0].cover, album.cover)
        self.assertEqual(self.window.entries[0].title, "Edited album")
        self.assertIs(self.window.entries[0].tracks[0], song.tracks[0])
        restored_entries = Store(self.path).load()[0]
        restored = restored_entries[0]
        self.assertEqual([t.page for t in restored.tracks], [3, 1])
        self.assertEqual(restored.tracks[0].title, "Local rename")
        self.assertIs(restored.tracks[0], next(e for e in restored_entries if e.id == song.id).tracks[0])
        dialog.reject()
        dialog.deleteLater()

    def test_add_single_to_albums_shares_song_and_queue_ids_are_unique(self):
        source = LibraryEntry("Custom single", BV2, tracks=[Track("Custom single", BV2, 55, 1)], resolved=True)
        self.window.entries.append(source)
        album = self.window.new_album(title="Mixed")
        other = self.window.new_album(title="Other folder")
        with patch.object(self.window, "_submit") as submit:
            self.window.add_single_to_album(source, album)
            self.window.add_single_to_album(source, other)
            self.window.add_single_to_album(source, album)
            submit.assert_not_called()
        self.assertEqual(len(album.tracks), 1)
        self.assertIs(source.tracks[0], album.tracks[0])
        self.assertIs(source.tracks[0], other.tracks[0])
        self.assertEqual(album.tracks[0].song_id, source.id)
        album.tracks[0].title = "Shared title"
        self.assertEqual(source.tracks[0].title, "Shared title")
        self.assertEqual(other.tracks[0].title, "Shared title")
        self.assertEqual(source.title, "Custom single")
        self.window._normalize_library()
        self.assertEqual(source.title, "Shared title")
        self.window.resolve_entry(album, False)
        self.window.resolve_entry(album, False)
        queue = self.window._tracks()
        occurrence_ids = [t.id for t in queue]
        self.assertEqual(len(set(occurrence_ids)), 2)
        self.assertTrue(all(t.id != source.id and t.song_id == source.id for t in queue))
        queue[0].title = "Queue-only title"
        self.assertEqual(album.tracks[0].title, "Shared title")
        self.assertEqual(queue[1].title, "Shared title")
        edited = deepcopy(source)
        edited.title, edited.artist, edited.date = "Global edit", "New artist", "Manual date"
        editor = Mock()
        editor.exec.return_value = AlbumDialog.DialogCode.Accepted
        editor.value.return_value = edited
        with patch("bili_player.app.EntryDialog", return_value=editor):
            self.window.edit_single(source)
        self.assertEqual((source.title, source.artist, source.date), ("Global edit", "New artist", "Manual date"))
        self.assertIs(album.tracks[0], source.tracks[0])
        self.assertIs(other.tracks[0], source.tracks[0])
        self.assertEqual([t.id for t in self.window._tracks()], occurrence_ids)
        self.assertTrue(all((t.title, t.artist, t.date, t.song_id) ==
                            ("Global edit", "New artist", "Manual date", source.id)
                            for t in self.window._tracks()))
        restored, restored_queue, _ = Store(self.path).load()
        restored_song = next(e for e in restored if e.id == source.id)
        for folder in (e for e in restored if e.kind == "album"):
            self.assertIs(folder.tracks[0], restored_song.tracks[0])
        self.assertEqual([t.id for t in restored_queue], occurrence_ids)

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
                restored_entries = Store(self.path).load()[0]
                restored = next(entry for entry in restored_entries if entry.id == album.id)
                self.assertEqual(len(restored.tracks), 2)
                self.assertEqual(len(restored_entries), 3)
                for track in restored.tracks:
                    self.assertIs(track, next(e for e in restored_entries if e.id == track.song_id).tracks[0])

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

    def test_details_cover_ignores_stale_image_after_edit_or_clear(self):
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done, error))
        self.window._load_cover = MainWindow._load_cover.__get__(self.window, MainWindow)
        entry = LibraryEntry("Single", BV, tracks=[self.tracks()[0]], cover="old-cover", resolved=True)
        self.window.entries = [entry]
        detail = EntryDetailsDialog(self.window, entry)
        try:
            old = Path(self.directory.name) / "old.png"
            new = Path(self.directory.name) / "new.png"
            image = QImage(20, 20, QImage.Format.Format_RGB32)
            image.fill(QColor("red"))
            image.save(str(old))
            image.fill(QColor("blue"))
            image.save(str(new))
            entry.cover = "new-cover"
            detail.refresh()
            jobs[1][1](new)
            jobs[0][1](old)
            self.assertEqual(detail.cover_widget._pixmap.toImage().pixelColor(10, 10), QColor("blue"))
            entry.cover = "third-cover"
            detail.refresh()
            entry.cover = ""
            detail.refresh()
            jobs[2][1](old)
            self.assertTrue(detail.cover_widget._pixmap.isNull())
        finally:
            detail.close()
            detail.deleteLater()

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

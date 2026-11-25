"""Integration of library packages, absolute seek, and mini overlay."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib
import json
import time
import wave
import zipfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from bili_player.app import MainWindow
from bili_player.audio_cache import BLOCK_SIZE
from bili_player.details import EntryDetailsDialog
from bili_player.exchange import read_library_package, write_library_package
from bili_player.lyrics import LyricsEntry, LyricsStore, SongLyrics
from bili_player.models import LibraryEntry, Track, Store
from tests.test_cached_stream import RangeFixture

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
        cached_image = QImage(16, 16, QImage.Format.Format_RGB32)
        cached_image.fill(0xFF335577)
        self.window.covers.directory.mkdir()
        key = hashlib.sha256(b"https://example.test/cover.jpg").hexdigest()
        self.assertTrue(cached_image.save(str(self.window.covers.directory / (key + ".img")), "PNG"))
        self.app.processEvents()

    def tearDown(self):
        self.window._quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def wait_for_tasks(self):
        deadline = time.monotonic() + 5
        while self.window._tasks and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window._tasks)

    def wav(self, name="song.wav", sample=b"\x01\x00"):
        path = self.root / name
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(sample * 8000)
        return path

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
        with patch("bili_player.app.ExportOptionsDialog.exec", return_value=QDialog.DialogCode.Accepted), \
                patch("bili_player.app.QFileDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.selectedFiles.return_value = [str(destination)]
            self.window.batch_export_button.click()
            dialog.return_value.setOption.assert_not_called()
            dialog.return_value.setDefaultSuffix.assert_called_once_with("zip")
        self.wait_for_tasks()
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

    def test_import_restores_active_lyrics_and_merges_versions_by_song_id(self):
        incoming = self.entries()
        album, fresh_song = incoming
        track = album.tracks[0]
        song = LibraryEntry(track.title, BV, tracks=[Track(track.title, BV, 1, 1)], resolved=True)
        track.song_id = song.id
        inactive = LibraryEntry("Inactive", BV, page=3, tracks=[Track("Inactive", BV, 3, 3)])
        incoming.append(inactive)
        self.window.entries = [song, inactive]
        current = LyricsEntry("local:1", "Edited", plain_lyrics="My version")
        conflict = LyricsEntry("local:1", "Imported", plain_lyrics="Their version")
        synced = LyricsEntry("lrclib:2", "Synced", synced_lyrics="[00:01]第一行", source="lrclib")
        inactive_current = LyricsEntry("local:3", "Unselected", plain_lyrics="No active version")
        self.window.lyrics_store.save(song.id, current)
        self.window.lyrics_store.save(inactive.id, inactive_current, select=False)
        source = LyricsStore(self.root / "source" / "lyrics.json")
        source.save(song.id, conflict)
        source.save(song.id, synced, select=False)
        source.save(fresh_song.id, conflict)
        source.save(fresh_song.id, synced)
        source.save(inactive.id, synced)
        destination = self.root / "with-lyrics.zip"
        write_library_package(destination, incoming, [entry.id for entry in incoming],
                              covers=self.window.covers, lyrics_store=source, include_lyrics=True)

        self.assertTrue(self.window.import_library_path(destination))
        restored = LyricsStore(self.window.lyrics_store.path)
        self.assertEqual(restored.load(fresh_song.id), SongLyrics((conflict, synced), synced.id))
        self.assertEqual(restored.load(inactive.id), SongLyrics((inactive_current, synced)))
        merged = restored.load(song.id)
        self.assertEqual(merged.active, current)
        self.assertEqual(len(merged.entries), 3)
        self.assertEqual(merged.entries[0], current)
        self.assertEqual(merged.entries[1].plain_lyrics, conflict.plain_lyrics)
        self.assertNotEqual(merged.entries[1].id, conflict.id)
        self.assertEqual(merged.entries[2], synced)
        self.assertTrue(self.window.import_library_path(destination))
        self.assertEqual(restored.load(song.id), merged)
        self.window._error.assert_not_called()

    def test_invalid_lyrics_package_is_rejected_before_persistence(self):
        incoming = self.entries()
        album, song = incoming
        self.window.entries = incoming
        self.assertTrue(self.window._save())
        local = LyricsEntry("local:1", "Original", plain_lyrics="Preserve this")
        self.window.lyrics_store.save(song.id, local)
        library_before = self.window.store.path.read_bytes()
        lyrics_before = self.window.lyrics_store.path.read_bytes()
        original = self.root / "original.zip"
        self.write_package(original, incoming)
        with zipfile.ZipFile(original) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        destination = self.root / "invalid.zip"
        rows = [{"id": "local:1", "title": "Imported"}]
        for malformed in (
            {"version": 2, "songs": {}},
            {"version": 1, "songs": {album.id: {"entries": rows}}},
            {"version": 1, "songs": {"unknown-song": {"entries": rows}}},
            {"version": 1, "songs": {song.id: {"entries": rows, "active_id": "missing"}}},
            {"version": 1, "songs": {song.id: {"entries": rows * 2}}},
        ):
            with self.subTest(malformed=malformed):
                with zipfile.ZipFile(destination, "w") as archive:
                    for name, data in members.items():
                        archive.writestr(name, data)
                    archive.writestr("lyrics.json", json.dumps(malformed))
                self.assertFalse(self.window.import_library_path(destination))
                self.assertEqual(self.window.store.path.read_bytes(), library_before)
                self.assertEqual(self.window.lyrics_store.path.read_bytes(), lyrics_before)

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
        incoming = self.entries()
        source = LyricsStore(self.root / "source" / "lyrics.json")
        source.save(incoming[1].id, LyricsEntry("local:1", "New lyrics", plain_lyrics="New words"))
        write_library_package(path, incoming, [entry.id for entry in incoming], covers=self.window.covers,
                              lyrics_store=source, include_lyrics=True)
        with patch.object(self.window.store, "save", side_effect=OSError("disk full")):
            self.assertFalse(self.window.import_library_path(path))
        self.assertIs(self.window.entries, existing)
        self.assertEqual(self.window.store.path.read_bytes(), original)
        self.assertFalse(self.window.lyrics_store.path.exists())

    def test_export_guard_protects_active_library(self):
        self.window.entries = self.entries()
        self.window._save()
        song = self.window.entries[1]
        self.window.lyrics_store.save(song.id, LyricsEntry("local:1", "Saved lyrics", plain_lyrics="Keep this"))
        for protected in (self.window.store.path, self.window.lyrics_store.path):
            original = protected.read_bytes()
            self.assertFalse(self.window.export_selection_path(protected, [song.id], include_lyrics=True))
            self.assertEqual(protected.read_bytes(), original)
        self.assertEqual(self.window._error.call_count, 2)

    def test_file_dialog_cancel_does_not_import_or_export(self):
        self.window.entries = self.entries()
        self.window._render_library()
        with patch("bili_player.app.QFileDialog") as dialog, \
             patch("bili_player.app.write_library_package") as writer, \
             patch("bili_player.app.ExportOptionsDialog.exec", return_value=QDialog.DialogCode.Rejected) as options:
            self.window.export_selection()
            options.assert_not_called()
            self.window.enter_library_selection(self.window.entries[0].id)
            self.window.batch_export_button.click()
            options.assert_called_once_with()
            dialog.assert_not_called()
            writer.assert_not_called()
            options.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.window.batch_export_button.click()
            dialog.return_value.exec.assert_called_once_with()
            writer.assert_not_called()
        with patch("bili_player.app.pick_files", return_value=[]) as chooser, \
             patch("bili_player.app.read_library_package") as reader:
            self.window.import_library_button.click()
            chooser.assert_called_once()
            reader.assert_not_called()

    def test_local_import_keeps_copy_and_plays_directly(self):
        original = self.wav()
        with patch("bili_player.app.pick_files", return_value=[str(original)]):
            self.window.import_audio_button.click()
        self.wait_for_tasks()
        original.unlink()
        song = self.window.entries[0]
        detail = EntryDetailsDialog(self.window, song)
        self.addCleanup(detail.deleteLater)
        deadline = time.monotonic() + 5
        while song.tracks[0].duration == 0 and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertEqual(song.tracks[0].duration, 1)
        self.assertEqual(detail.meta_label.text(), "单曲 · 0:01")
        self.assertTrue(self.window.player.source().isEmpty())
        self.assertTrue(self.window._save())
        songs, _, _ = Store(self.window.store.path).load()
        self.assertEqual([(song.title, song.tracks[0].duration) for song in songs], [("song", 1)])
        self.assertEqual(detail.local_audio_label.text(), "已在本地 · 可离线播放")
        self.assertTrue(detail.download_button.isHidden())
        path = self.window.local_audio_path(song.tracks[0])
        with patch.object(self.window, "_source_client") as client, \
                patch.object(self.window.proxy, "get_cached_stream") as cache:
            self.window.resolve_entry(song, True)
            self.assertEqual(Path(self.window.player.source().toLocalFile()), path)
            deadline = time.monotonic() + 3
            while self.window.player.duration() == 0 and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertEqual(self.window.player.duration(), 1000)
            client.assert_not_called()
            cache.assert_not_called()
        self.window._error.assert_not_called()

    def test_existing_local_song_duration_is_backfilled_on_startup(self):
        source = "1" * 32
        track = Track("Legacy song", source, 0, 1, provider="local",
                      audio_file=self.window.audio_files.import_file(self.wav()))
        song = LibraryEntry(track.title, source, provider="local", tracks=[track], resolved=True)
        album = LibraryEntry("Album", kind="album", tracks=[track], resolved=True)
        queued = Track(track.title, source, 0, 1, provider="local", song_id=song.id, audio_file=track.audio_file)
        store = Store(self.root / "legacy.json")
        store.save([album, song], [queued], 70)
        restored = MainWindow(store.path)
        try:
            album, song = restored.entries
            detail = EntryDetailsDialog(restored, album)
            self.assertTrue(detail.meta_label.text().endswith("—"))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if store.load()[0][1].tracks[0].duration:
                    break
                QTest.qWait(10)
            entries, queue, _ = store.load()
            self.assertEqual(entries[1].tracks[0].duration, 1)
            self.assertEqual(queue[0].duration, 1)
            self.assertEqual(album.tracks[0].duration, 1)
            self.assertTrue(detail.meta_label.text().endswith("0:01"))
            self.assertTrue(restored.player.source().isEmpty())
        finally:
            restored._quitting = True
            restored.close()
            restored.deleteLater()

    def test_download_updates_details_and_existing_queue_without_resolving_again(self):
        track = Track("Remote", BV, 10, 1)
        song = LibraryEntry("Remote", BV, tracks=[track], resolved=True)
        self.window.entries = [song]
        self.window.resolve_entry(song, False)
        audio_data = self.wav().read_bytes()
        client = Mock()
        client.get_stream.return_value = "https://example.test/audio.wav"
        response = Mock(status_code=200, headers={"Content-Type": "audio/wav", "Content-Length": str(len(audio_data))})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = iter([audio_data[:2048], audio_data[2048:]])
        detail = EntryDetailsDialog(self.window, song)
        self.addCleanup(detail.deleteLater)
        self.assertEqual(detail.local_audio_label.text(), "未下载到本地")
        with patch.object(self.window, "_source_client", return_value=client) as resolver, \
                patch("bili_player.stream.requests.request", return_value=response) as request:
            detail.download_button.click()
            self.assertEqual(detail.local_audio_label.text(), "正在下载音频…")
            self.wait_for_tasks()
            resolver.assert_called_once()
            client.get_stream.assert_called_once()
            request.assert_called_once()
            self.assertEqual(detail.local_audio_label.text(), "已在本地 · 可离线播放")
            self.window.proxy.clear_cache()
            self.window.play_item(self.window.queue.item(0))
            self.assertTrue(self.window.player.source().isLocalFile())
            resolver.assert_called_once()
            request.assert_called_once()
        restored = Store(self.window.store.path).load()[0][0]
        self.assertEqual(self.window.audio_files.path(restored.tracks[0].audio_file).read_bytes(), audio_data)
        self.window._error.assert_not_called()

    def test_detail_progress_counts_cached_bytes_and_restores_on_reopen(self):
        fixture = RangeFixture()
        self.addCleanup(fixture.close)
        video_id = "Dvl7IEqtcz4"
        song = LibraryEntry("Song", video_id, provider="youtube", resolved=True, tracks=[
            Track("Song", video_id, 0, 1, duration=180, provider="youtube"),
        ])
        other = LibraryEntry("Other", BV, resolved=True, tracks=[Track("Other", BV, 10, 1)])
        self.window.entries = [song, other]
        self.window._normalize_library()
        key, source = self.window._audio_cache_identity(song.tracks[0])
        relay = self.window.proxy.set_stream(fixture.url, cache_key=key, source_key=source)
        with requests.get(relay, headers={"Range": "bytes=0-10"}, timeout=4) as response:
            self.assertEqual(response.content, fixture.data[:11])
        fixture.seen.clear()
        fixture.gate = threading.Event()
        detail = EntryDetailsDialog(self.window, song)
        unrelated = EntryDetailsDialog(self.window, other)
        self.addCleanup(detail.deleteLater)
        self.addCleanup(unrelated.deleteLater)
        self.assertTrue(detail.download_progress.isHidden())
        with patch.object(self.window, "_source_client", side_effect=AssertionError("Reuse active audio URL")):
            try:
                detail.download_button.click()
                self.assertFalse(detail.download_progress.isHidden())
                self.assertEqual(detail.download_progress.maximum(), 0)
                deadline = time.monotonic() + 2
                expected = BLOCK_SIZE * 100 // len(fixture.data)
                while detail.download_progress.value() != expected and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertEqual(detail.download_progress.maximum(), 100)
                self.assertEqual(detail.download_progress.value(), expected)
                self.assertTrue(unrelated.download_progress.isHidden())
                reopened = EntryDetailsDialog(self.window, song)
                self.addCleanup(reopened.deleteLater)
                self.assertEqual(reopened.download_progress.value(), expected)
            finally:
                fixture.gate.set()
                self.wait_for_tasks()
        self.assertTrue(detail.download_progress.isHidden())
        self.assertEqual(detail.local_audio_label.text(), "已在本地 · 可离线播放")
        self.assertEqual(self.window.local_audio_path(song.tracks[0]).read_bytes(), fixture.data)
        self.assertEqual(fixture.seen[0], ("GET", f"bytes={BLOCK_SIZE}-{2*BLOCK_SIZE-1}"))
        self.window._error.assert_not_called()

    def test_detail_progress_is_hidden_after_download_failure(self):
        song = LibraryEntry("Remote", BV, resolved=True, tracks=[Track("Remote", BV, 10, 1)])
        self.window.entries = [song]
        detail = EntryDetailsDialog(self.window, song)
        self.addCleanup(detail.deleteLater)
        with patch.object(self.window.proxy, "download_audio", side_effect=ValueError("download failed")):
            detail.download_button.click()
            self.assertFalse(detail.download_progress.isHidden())
            self.wait_for_tasks()
        self.assertTrue(detail.download_progress.isHidden())
        self.assertTrue(detail.download_button.isEnabled())
        self.assertEqual(detail.local_audio_label.text(), "未下载到本地")
        self.window._error.assert_called_once()

    def test_zip_checkboxes_control_audio_and_lyrics_and_embed_both_kinds_of_cover(self):
        files = self.window.audio_files
        original = self.wav("local.wav")
        local_file = files.import_file(original)
        remote_file = files.import_file(self.wav("download.wav", b"\x04\x00"))
        source = "1" * 32
        local = LibraryEntry("Local", source, provider="local", resolved=True,
                             tracks=[Track("Local", source, 0, 1, provider="local", audio_file=local_file)])
        remote = LibraryEntry("Remote", BV, resolved=True, cover="https://example.test/uncached.png",
                              tracks=[Track("Remote", BV, 1, 1, audio_file=remote_file)])
        picture = QImage(16, 16, QImage.Format.Format_RGB32)
        picture.fill(0xFF996622)
        path = self.root / "local-cover.png"
        self.assertTrue(picture.save(str(path)))
        local.cover = str(path)
        cached = self.window.covers.directory / (hashlib.sha256(b"https://example.test/cover.jpg").hexdigest() + ".img")
        data = cached.read_bytes()
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.side_effect = lambda _: iter([data])
        album = LibraryEntry("Album", kind="album", tracks=[local.tracks[0], remote.tracks[0]], resolved=True)
        local.tracks[0].song_id, remote.tracks[0].song_id = local.id, remote.id
        unselected = LibraryEntry("Unselected", BV, page=2, tracks=[Track("Unselected", BV, 2, 2)])
        self.window.entries = [album, local, remote, unselected]
        local_lyrics = LyricsEntry("local:1", "版本一", plain_lyrics="第一行\n第二行")
        remote_lyrics = LyricsEntry("lrclib:1", "Instrumental", source="lrclib", instrumental=True)
        self.window.lyrics_store.save(local.id, local_lyrics)
        self.window.lyrics_store.save(remote.id, remote_lyrics)
        self.window.lyrics_store.save(unselected.id, local_lyrics)
        self.window._render_library()
        self.window.enter_library_selection(album.id)
        for checked in (False, True):
            destination = self.root / f"package-{checked}.zip"

            def choose_options(options):
                self.assertFalse(options.include_lyrics.isChecked())
                options.include_downloads.setChecked(checked)
                options.include_lyrics.setChecked(not checked)
                return QDialog.DialogCode.Accepted

            with patch("bili_player.app.QFileDialog") as dialog, \
                    patch("bili_player.app.ExportOptionsDialog.exec", choose_options), \
                    patch("bili_player.covers.requests.get", return_value=response) as request:
                dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
                dialog.return_value.selectedFiles.return_value = [str(destination)]
                self.window.export_selection()
                self.wait_for_tasks()
                self.assertEqual(request.call_count, int(not checked))
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(len([name for name in archive.namelist() if name.startswith("covers/")]), 2)
                self.assertEqual({name for name in archive.namelist() if name.startswith("audio/")},
                                 {f"audio/{local_file}", f"audio/{remote_file}"} if checked else {f"audio/{local_file}"})
                self.assertEqual("lyrics.json" in archive.namelist(), not checked)
            from bili_player.audio_files import AudioFiles
            from bili_player.covers import CoverCache
            restored_files = AudioFiles(self.root / f"restored-{checked}" / "audio-files")
            lyrics = {}
            restored = read_library_package(destination, CoverCache(restored_files.directory.parent / "covers"),
                                            restored_files, lyrics=lyrics)
            self.assertEqual(lyrics, {} if checked else {
                local.id: SongLyrics((local_lyrics,), local_lyrics.id),
                remote.id: SongLyrics((remote_lyrics,), remote_lyrics.id),
            })
            restored_album, restored_local, restored_remote = restored
            self.assertIs(restored_album.tracks[0], restored_local.tracks[0])
            self.assertIs(restored_album.tracks[1], restored_remote.tracks[0])
            self.assertEqual(Path(restored_local.cover).read_bytes(), path.read_bytes())
            self.assertEqual(Path(restored_remote.cover).read_bytes(), data)
            self.assertEqual(restored_files.path(local_file).read_bytes(), original.read_bytes())
            self.assertEqual(restored_remote.tracks[0].audio_file, remote_file if checked else "")
            self.assertEqual(restored_files.path(remote_file) is not None, checked)
        self.window._error.assert_not_called()

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

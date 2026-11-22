"""Playback cache integration and manual clearing / 播放缓存与手动清理集成。"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PyQt6.QtCore import QUrl
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from bili_player.api import BilibiliClient
from bili_player.app import MainWindow, copy_tracks
from bili_player.models import LibraryEntry, Track
from bili_player.settings_dialog import SettingsDialog
from tests.test_player import AudioFixture

BV = "BV1xx411c7mD"


class AudioCacheUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = MainWindow(Path(self.directory.name) / "library.json")
        self.window._error = Mock()
        self.window._load_cover = Mock()

    def tearDown(self):
        self.window._quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def song(self):
        song = LibraryEntry("Song", BV, tracks=[Track("Song", BV, 123, 1)], resolved=True)
        self.window.entries.append(song)
        self.window._normalize_library()
        return song

    def test_queue_occurrences_share_entity_cache_key_but_copies_do_not(self):
        song = self.song()
        first, second = copy_tracks([song.tracks[0], song.tracks[0]])
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(self.window._audio_cache_identity(first), self.window._audio_cache_identity(second))
        duplicate = next(e for e in self.window.copy_library_entries([song.id]) if e.kind == "single")
        self.assertNotEqual(self.window._audio_cache_identity(first)[0], self.window._audio_cache_identity(duplicate.tracks[0])[0])
        changed = Track("Other part", BV, 456, 2, song_id=song.id)
        self.assertNotEqual(self.window._audio_cache_identity(first)[1], self.window._audio_cache_identity(changed)[1])

    def test_complete_cache_skips_bilibili_resolution_and_survives_proxy_restart(self):
        fixture = AudioFixture()
        try:
            song = self.song()
            key, source = self.window._audio_cache_identity(song.tracks[0])
            url = self.window.proxy.set_stream(fixture.url, cache_key=key, source_key=source)
            self.assertEqual(requests.get(url, timeout=3).content, fixture.data)
            self.window.proxy.close()
            from bili_player.stream import StreamProxy
            self.window.proxy = StreamProxy(self.window.store.path.parent / "audio-cache")
            self.window._append_tracks(copy_tracks(song.tracks))
            with patch.object(BilibiliClient, "get_stream", side_effect=AssertionError("Cache hit must be offline")), \
                 patch.object(self.window.player, "setSource") as set_source, \
                 patch.object(self.window.player, "play"):
                self.window.play_item(self.window.queue.item(0))
                self.assertIn("127.0.0.1", set_source.call_args.args[0].toString())
                self.assertFalse(self.window._loading_stream)
                self.assertEqual(requests.get(set_source.call_args.args[0].toString(), timeout=3).content, fixture.data)
        finally:
            self.window.player.setSource(QUrl())
            fixture.close()

    def test_qt_decodes_and_seeks_complete_cache_with_upstream_offline(self):
        fixture = AudioFixture()
        try:
            song = self.song()
            key, source = self.window._audio_cache_identity(song.tracks[0])
            url = self.window.proxy.set_stream(fixture.url, cache_key=key, source_key=source)
            self.assertEqual(requests.get(url, timeout=3).content, fixture.data)
        finally:
            fixture.close()
        self.window._append_tracks(copy_tracks(song.tracks))
        self.window._submit = lambda fn, done, error=None: done(fn())
        with patch.object(BilibiliClient, "get_stream", side_effect=AssertionError("Must play offline")), \
             patch.object(self.window.player, "play"):
            self.window.play_item(self.window.queue.item(0))
            deadline = time.monotonic() + 6
            while not self.window.player.isSeekable() and time.monotonic() < deadline:
                QTest.qWait(30)
            self.assertTrue(self.window.player.isSeekable())
            self.assertGreaterEqual(self.window.player.duration(), 1900)
            self.window.player.setPosition(700)
            self.assertEqual(self.window.player.position(), 700)

    def test_youtube_download_saves_complete_cache_without_replacing_the_playback_stream(self):
        identifier = "Dvl7IEqtcz4"
        track = Track("Song", identifier, 0, 1, provider="youtube")
        song = LibraryEntry(track.title, identifier, tracks=[track], resolved=True, provider="youtube")
        self.window.entries.append(song)
        self.window._normalize_library()
        key, source = self.window._audio_cache_identity(track)
        fixture = AudioFixture()
        try:
            url = self.window.proxy.set_stream(fixture.url, cache_key=key, source_key=source)
            self.assertEqual(requests.get(url, timeout=3).content, fixture.data)
        finally:
            fixture.close()
        self.window._submit = lambda fn, done, error=None: done(fn())
        with patch.object(self.window, "_source_client") as client:
            self.window.download_audio(song)
        client.assert_not_called()
        self.assertEqual(self.window.local_audio_path(track).read_bytes(), fixture.data)
        self.assertEqual(requests.get(url, timeout=3).content, fixture.data)

    def test_youtube_download_reports_cdn_403_without_saving_audio(self):
        identifier = "Dvl7IEqtcz4"
        track = Track("Song", identifier, 0, 1, provider="youtube")
        song = LibraryEntry(track.title, identifier, tracks=[track], resolved=True, provider="youtube")
        self.window.entries.append(song)
        self.window._normalize_library()
        pending = []
        self.window._submit = lambda fn, done, error=None: pending.append((fn, done, error))
        client = Mock()
        client.get_stream.return_value = "https://cdn.example.test/audio.m4a"
        response = requests.Response()
        response.status_code = 403
        response._content = b""
        response._content_consumed = True
        with patch.object(self.window, "_source_client", return_value=client), \
                patch("bili_player.audio_files.network.get", return_value=response):
            self.window.download_audio(song)
            load, _, failed = pending[0]
            with self.assertRaisesRegex(ValueError, "HTTP 403") as raised:
                load()
            failed(str(raised.exception))
        self.assertIn("HTTP 403", self.window._error.call_args.args[0])
        self.assertFalse(track.audio_file)
        self.assertFalse(self.window.audio_download_pending(song))

    def test_stream_registration_uses_entity_id_and_stale_callback_is_ignored(self):
        song = self.song()
        self.window._append_tracks(copy_tracks(song.tracks))
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append(done)
        self.window.play_item(self.window.queue.item(0))
        with patch.object(self.window.proxy, "set_stream", return_value="http://127.0.0.1/audio/test") as register, \
             patch.object(self.window.player, "setSource"), patch.object(self.window.player, "play"):
            jobs[0]("https://example.test/audio")
            register.assert_called_once_with("https://example.test/audio", cache_key=song.id,
                                            source_key=self.window._audio_cache_identity(song.tracks[0])[1])
        self.window.clear_queue()
        with patch.object(self.window.proxy, "set_stream") as register:
            jobs[0]("https://example.test/stale")
            register.assert_not_called()

    def test_manual_clear_cancellation_and_success_do_not_change_library(self):
        song = self.song()
        self.window._append_tracks(copy_tracks(song.tracks))
        ids = [entry.id for entry in self.window.entries]
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel), \
             patch.object(self.window.proxy, "clear_cache") as clear:
            self.window.clear_audio_cache()
            clear.assert_not_called()
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done, error))
        self.window._settings_dialog = SettingsDialog(self.window)
        self.window._refresh_settings()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window._settings_dialog.clear_cache_button.click()
        self.assertFalse(self.window._settings_dialog.clear_cache_button.isEnabled())
        jobs[0][1]({"removed_bytes": 1024 ** 2, "pending_bytes": 2 * 1024 ** 2})
        self.assertTrue(self.window._settings_dialog.clear_cache_button.isEnabled())
        self.assertIn("1.0", self.window.status.text())
        self.assertIn("2.0", self.window.status.text())
        self.assertEqual([entry.id for entry in self.window.entries], ids)
        self.assertEqual(self.window.queue.count(), 1)

    def test_clear_failure_restores_button_and_translation(self):
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done, error))
        self.window.set_language("en_US")
        self.window._settings_dialog = SettingsDialog(self.window)
        self.window._refresh_settings()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window._settings_dialog.clear_cache_button.click()
        jobs[0][2]("Disk unavailable")
        self.assertTrue(self.window._settings_dialog.clear_cache_button.isEnabled())
        self.assertEqual(self.window._settings_dialog.clear_cache_button.text(), "Clear audio cache…")
        self.assertIn("Could not clear audio cache", self.window._error.call_args.args[0])

    def test_clear_queue_releases_active_cache(self):
        self.window._append_tracks([Track("Song", BV, 123, 1)])
        with patch.object(self.window.proxy, "release_stream") as release:
            self.window.clear_queue()
            release.assert_called_once()


if __name__ == "__main__":
    unittest.main()

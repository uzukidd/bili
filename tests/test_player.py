"""Qt behavior and loopback media integration, without Bilibili access."""
from __future__ import annotations

import io
import os
import tempfile
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import requests
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from bili_player.api import VideoInfo
from bili_player.app import EntryDialog, MainWindow, STYLE
from bili_player.models import LibraryEntry, Track
from bili_player.stream import StreamProxy

BV = "BV1xx411c7mD"


class AudioFixture:
    def __init__(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 16000)
        self.data = output.getvalue()
        self.seen = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_HEAD(self):
                self.serve(head=True)

            def do_GET(self):
                self.serve(head=False)

            def serve(self, head):
                fixture.seen.append(dict(self.headers))
                start, end = 0, len(fixture.data) - 1
                ranged = self.headers.get("Range")
                if ranged:
                    value = ranged.removeprefix("bytes=").split("-")
                    start = int(value[0] or 0)
                    end = min(int(value[1]) if value[1] else end, end)
                content = fixture.data[start:end + 1]
                self.send_response(206 if ranged else 200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Accept-Ranges", "bytes")
                if ranged:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(fixture.data)}")
                self.end_headers()
                if not head:
                    try:
                        self.wfile.write(content)
                    except OSError:
                        pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/audio.wav"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AudioFixture()
        self.proxy = StreamProxy()
        self.url = self.proxy.set_stream(self.fixture.url)

    def tearDown(self):
        self.proxy.close()
        self.fixture.close()

    def test_stream_bytes_headers_head_and_range(self):
        response = requests.get(self.url, headers={"Range": "bytes=8-99"}, timeout=3)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, self.fixture.data[8:100])
        self.assertEqual(response.headers["Content-Range"], f"bytes 8-99/{len(self.fixture.data)}")
        self.assertEqual(self.fixture.seen[-1]["Referer"], "https://www.bilibili.com/")
        self.assertIn("Mozilla", self.fixture.seen[-1]["User-Agent"])
        response = requests.head(self.url, timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(response.headers["Content-Length"]), len(self.fixture.data))
        self.assertEqual(response.content, b"")

    def test_old_token_and_unknown_path_are_rejected(self):
        new_url = self.proxy.set_stream(self.fixture.url)
        self.assertNotEqual(self.url, new_url)
        self.assertEqual(requests.get(self.url, timeout=3).status_code, 404)
        self.assertEqual(requests.get(new_url, timeout=3).content, self.fixture.data)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "library.json"
        self.window = MainWindow(self.path)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window._quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def tracks(self):
        return [Track(title=f"Song {i}", bvid=BV, cid=i, page=i) for i in (1, 2, 3)]

    def begin_stream(self):
        self.window._append_tracks(self.tracks())
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((done, error))
        self.window.play_item(self.window.queue.item(0))
        with patch.object(self.window.player, "setSource"), patch.object(self.window.player, "play"):
            jobs[0][0]("https://example.com/first.m4a")
        self.window._position(42000)
        return jobs

    def test_network_error_refreshes_stream_and_seeks_to_interrupted_position(self):
        jobs = self.begin_stream()
        track_id = self.window.current_id
        self.window._media_error(QMediaPlayer.Error.NetworkError, "Connection interrupted")
        self.assertEqual(len(jobs), 2)
        self.assertEqual(self.window._resume_position, 42000)
        self.assertEqual(self.window.current_id, track_id)
        with patch.object(self.window.player, "setSource") as source, \
             patch.object(self.window.player, "play") as play, \
             patch.object(self.window.player, "isSeekable", return_value=False):
            jobs[1][0]("https://example.com/refreshed.m4a")
            self.assertIn("127.0.0.1", source.call_args.args[0].toString())
            play.assert_called_once()
            self.assertEqual(self.window._resume_position, 42000)
        with patch.object(self.window.player, "isSeekable", return_value=True), \
             patch.object(self.window.player, "setPosition") as seek:
            self.window._media_status(QMediaPlayer.MediaStatus.LoadedMedia)
            self.app.processEvents()
            seek.assert_called_once_with(42000)
            self.assertIsNone(self.window._resume_position)

    def test_watchdog_recovers_when_progress_stops_but_not_during_pause(self):
        jobs = self.begin_stream()
        self.window.toggle_play()
        self.window._last_progress_at = time.monotonic() - 30
        self.window._check_playback_progress()
        self.assertEqual(len(jobs), 1)
        self.window._want_playing = True
        self.window._check_playback_progress()
        self.assertEqual(len(jobs), 2)
        self.window._check_playback_progress()
        self.assertEqual(len(jobs), 2)

    def test_reconnect_stops_after_three_failures_and_can_be_retried(self):
        jobs = self.begin_stream()
        self.window._recover_stream()
        for index in (1, 2, 3):
            jobs[index][1]("Network unavailable")
        self.assertEqual(len(jobs), 4)
        self.assertFalse(self.window._want_playing)
        self.assertEqual(self.window._resume_position, 42000)
        self.assertIn("音频重连失败", self.window.status.text())
        self.window._check_playback_progress()
        self.assertEqual(len(jobs), 4)
        self.window.toggle_play()
        self.assertEqual(len(jobs), 5)
        self.assertTrue(self.window._want_playing)

    def test_pause_during_reconnect_never_autoplays_on_completion(self):
        jobs = self.begin_stream()
        self.window._recover_stream()
        self.window.toggle_play()
        with patch.object(self.window.player, "setSource"), patch.object(self.window.player, "play") as play:
            jobs[1][0]("https://example.com/refreshed.m4a")
            play.assert_not_called()
        self.assertFalse(self.window._want_playing)

    def test_stale_reconnect_cannot_replace_new_song_or_cleared_queue(self):
        jobs = self.begin_stream()
        self.window._recover_stream()
        self.window.play_item(self.window.queue.item(1))
        with patch.object(self.window.player, "setSource") as source:
            jobs[1][0]("https://example.com/old-retry.m4a")
            source.assert_not_called()
        self.window._loading_stream = False
        self.window._recover_stream()
        old_retry = jobs[-1]
        self.window.clear_queue()
        with patch.object(self.window.player, "setSource") as source:
            old_retry[0]("https://example.com/cleared-retry.m4a")
            source.assert_not_called()
        self.assertFalse(self.window._want_playing)

    def test_premature_end_recovers_but_natural_end_advances_queue(self):
        jobs = self.begin_stream()
        with patch.object(self.window.player, "duration", return_value=180000), \
             patch.object(self.window, "next") as next_track:
            self.window._media_status(QMediaPlayer.MediaStatus.EndOfMedia)
            next_track.assert_not_called()
            self.assertEqual(len(jobs), 2)
            self.window._loading_stream = False
            self.window._last_played_position = 179000
            self.window._media_status(QMediaPlayer.MediaStatus.EndOfMedia)
            next_track.assert_called_once_with(automatic=True)

    def test_mini_mode_preserves_playback_and_restores_geometry(self):
        original = self.window.geometry()
        tracks = self.tracks()
        self.window._append_tracks(tracks)
        self.window.current_id = tracks[0].id
        generation = self.window._request
        with patch.object(self.window.player, "stop") as stop, patch.object(self.window.player, "setSource") as source:
            QTest.mouseClick(self.window.collapse_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertTrue(self.window._mini_mode)
            self.assertEqual(self.window.pages.currentIndex(), 1)
            self.assertLess(self.window.width(), 500)
            self.assertLess(self.window.height(), 700)
            self.window._duration(180000)
            self.window._position(42000)
            self.assertEqual(self.window.mini_seek.value(), 42000)
            self.assertEqual(self.window.mini_elapsed.text(), "0:42")
            self.assertEqual(self.window.mini_total.text(), "3:00")
            with patch.object(self.window.player, "setPosition") as position:
                self.window.mini_seek.setValue(60000)
                self.window.mini_seek.seekRequested.emit(60000)
                position.assert_called_once_with(60000)
            QTest.mouseClick(self.window.expand_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(self.window.geometry(), original)
            self.assertEqual(self.window._request, generation)
            stop.assert_not_called()
            source.assert_not_called()

    def test_mini_queue_overlays_cover_above_controls_without_resizing(self):
        self.window.set_mini_mode(True)
        self.app.processEvents()
        compact_size = self.window.size()
        cover_geometry = self.window.mini_cover.geometry()
        self.assertFalse(self.window.mini_queue_panel.isVisible())
        with patch.object(self.window.player, "stop") as stop:
            QTest.mouseClick(self.window.mini_queue_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertTrue(self.window.mini_queue_panel.isVisible())
            self.assertTrue(self.window.mini_queue_button.isChecked())
            self.assertEqual(self.window.size(), compact_size)
            self.assertEqual(self.window.mini_cover.geometry(), cover_geometry)
            panel_bottom = self.window.mini_queue_panel.mapTo(self.window, self.window.mini_queue_panel.rect().bottomLeft()).y()
            button_top = self.window.mini_queue_button.mapTo(self.window, self.window.mini_queue_button.rect().topLeft()).y()
            self.assertLess(panel_bottom, button_top)
            self.assertTrue(self.window.mini_queue_panel.geometry().intersects(self.window.mini_cover.geometry()))
            self.assertTrue(self.window.queue.isVisible())
            self.assertTrue(self.window.mini_queue_empty.isVisible())
            QTest.mouseClick(self.window.mini_queue_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertFalse(self.window.mini_queue_panel.isVisible())
            self.assertFalse(self.window.mini_queue_button.isChecked())
            self.assertEqual(self.window.size(), compact_size)
            stop.assert_not_called()

    def test_mini_queue_shares_playback_order_and_selection_with_full_view(self):
        tracks = self.tracks()
        self.window._append_tracks(tracks)
        queue = self.window.queue
        self.window.set_mini_mode(True)
        self.window.toggle_mini_queue()
        self.window._submit = lambda *_: None
        queue.itemDoubleClicked.emit(queue.item(1))
        self.assertEqual(self.window.current_id, tracks[1].id)
        self.assertEqual(self.window.mini_title.text(), "Song 2")
        first = queue.takeItem(0)
        queue.addItem(first)
        self.window._queue_changed()
        self.assertEqual(self.window._current_row(), 0)
        self.window.set_mini_mode(False)
        self.app.processEvents()
        self.assertIs(self.window.queue, queue)
        self.assertIs(queue.parentWidget(), self.window.queue_layout.parentWidget())
        self.assertTrue(queue.isVisible())
        self.assertEqual([t.id for t in self.window._tracks()], [tracks[1].id, tracks[2].id, tracks[0].id])
        self.assertEqual(queue.currentItem().data(Qt.ItemDataRole.UserRole).id, tracks[1].id)
        self.window.set_mini_mode(True)
        self.assertFalse(self.window.mini_queue_panel.isVisible())
        self.window.toggle_mini_queue()
        self.assertTrue(self.window.mini_queue_panel.isVisible())
        self.assertTrue(self.window.queue.isVisible())
        self.window.clear_queue()
        self.assertEqual(queue.count(), 0)
        self.assertEqual(self.window.mini_queue_title.text(), "播放队列 · 0")
        self.assertTrue(self.window.mini_queue_empty.isVisible())
        self.window._append_tracks([tracks[0]])
        self.assertFalse(self.window.mini_queue_empty.isVisible())
        self.window.set_language("en_US")
        self.assertEqual(self.window.mini_queue_title.text(), "Queue · 1")
        self.assertEqual(self.window.mini_queue_button.toolTip(), "Show / Hide play queue")

    def test_close_to_tray_keeps_player_and_pending_requests_alive(self):
        generation = self.window._request
        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True), \
             patch.object(self.window.player, "stop") as stop, \
             patch.object(self.window.proxy, "close") as close_proxy:
            self.assertFalse(self.window.close())
            self.assertFalse(self.window.isVisible())
            self.assertFalse(self.window._closing)
            self.assertEqual(self.window._request, generation)
            stop.assert_not_called()
            close_proxy.assert_not_called()
            self.window._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
            self.assertTrue(self.window.isVisible())
            self.window.show_mini_player()
            self.window.close()
            self.assertFalse(self.window.isVisible())
            self.window.restore_window()
            self.assertTrue(self.window._mini_mode)
            self.assertTrue(self.window.isVisible())

    def test_explicit_quit_releases_player_proxy_and_tray(self):
        with patch.object(self.app, "quit") as quit_app, \
             patch.object(self.window.proxy, "close", wraps=self.window.proxy.close) as close_proxy:
            self.window.quit_application()
            self.assertTrue(self.window._closing)
            self.assertFalse(self.window.tray.isVisible())
            self.assertTrue(self.window.player.source().isEmpty())
            close_proxy.assert_called_once()
            quit_app.assert_called_once()

    def test_close_without_system_tray_exits_normally(self):
        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
            self.assertTrue(self.window.close())
        self.assertTrue(self.window._closing)

    def test_mini_cover_tracks_current_song_and_ignores_late_covers(self):
        tracks = self.tracks()
        self.window.entries = [LibraryEntry("Album", BV, "album", tracks=tracks, cover="first")]
        self.window._append_tracks(tracks)
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append(done)
        self.window.play_item(self.window.queue.item(0))
        self.assertEqual(self.window.mini_title.text(), "Song 1")
        self.assertEqual(self.window.mini_meta.text(), f"{BV} · P1")
        self.window.song_for_track(tracks[1]).cover = "second"
        self.assertEqual(self.window.entries[0].cover, "first")
        self.window.play_item(self.window.queue.item(1))
        self.assertEqual(self.window.mini_title.text(), "Song 2")
        old = QPixmap(8, 8)
        old.fill(QColor("red"))
        old_path = Path(self.directory.name) / "old.png"
        old.save(str(old_path))
        jobs[0](old_path)
        self.assertTrue(self.window.mini_cover.pixmap.isNull())
        jobs[2](old_path)
        self.assertFalse(self.window.mini_cover.pixmap.isNull())
        self.window.clear_queue()
        self.assertTrue(self.window.mini_cover.pixmap.isNull())
        self.assertEqual(self.window.mini_title.text(), "还没有正在播放的音乐")
        self.window.set_language("en_US")
        self.assertEqual(self.window.mini_title.text(), "Nothing playing")
        self.assertEqual(self.window.expand_button.text(), "Expand")
        self.window.set_theme("dark")
        self.assertEqual(self.window.mini_cover.theme, "dark")

    def test_queue_survives_reorder_and_reopen(self):
        tracks = self.tracks()
        self.window._append_tracks(tracks)
        self.window.current_id = tracks[1].id
        first = self.window.queue.takeItem(0)
        self.window.queue.addItem(first)
        self.window._queue_changed()
        self.assertEqual(self.window._current_row(), 0)
        self.window.volume.setValue(35)
        self.window._save()
        entries, saved, volume = self.window.store.load()
        self.assertEqual([t.id for t in saved], [tracks[1].id, tracks[2].id, tracks[0].id])
        self.assertEqual(volume, 35)
        self.assertEqual(entries, [])

    def test_late_append_cannot_undo_clear_queue(self):
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done))
        self.window.resolve_entry(LibraryEntry("Album", BV, "album"), False)
        self.window.clear_queue()
        jobs[0][1](VideoInfo("Album", "", self.tracks()))
        self.assertEqual(self.window.queue.count(), 0)

    def test_concurrent_append_requests_both_remain_valid(self):
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done))
        entry = LibraryEntry("Album", BV, "album")
        self.window.resolve_entry(entry, False)
        self.window.resolve_entry(entry, False)
        jobs[1][1](VideoInfo("Album", "", [self.tracks()[1]]))
        jobs[0][1](VideoInfo("Album", "", [self.tracks()[0]]))
        self.assertEqual(self.window.queue.count(), 2)

    def test_stale_stream_response_cannot_override_latest_song(self):
        jobs = []
        self.window._submit = lambda fn, done, error=None: jobs.append((fn, done))
        tracks = self.tracks()
        self.window._append_tracks(tracks)
        self.window.play_item(self.window.queue.item(0))
        self.window.play_item(self.window.queue.item(1))
        with patch.object(self.window.proxy, "set_stream") as relay:
            jobs[0][1]("https://example.com/old.m4a")
            relay.assert_not_called()
        self.assertEqual(self.window.current_id, tracks[1].id)
        self.assertTrue(self.window.player.source().isEmpty())

    def test_modes_follow_queue_order(self):
        tracks = self.tracks()
        self.window._append_tracks(tracks)
        played = []
        self.window.play_item = lambda item: played.append(item.data(256).id) if item else None
        self.window.current_id = tracks[-1].id
        self.window.next(automatic=True)
        self.assertEqual(played, [])
        self.window.mode.setCurrentIndex(1)
        self.window.next(automatic=True)
        self.assertEqual(played[-1], tracks[0].id)
        self.window.mode.setCurrentIndex(2)
        self.window.next(automatic=True)
        self.assertEqual(played[-1], tracks[-1].id)

    def test_album_dialog_and_search(self):
        dialog = EntryDialog(self.window, LibraryEntry("Album", BV, "album"))
        self.assertFalse(dialog.page_input.isEnabled())
        self.assertEqual(dialog.value().kind, "album")
        self.window.entries = [LibraryEntry("Moonlight", BV), LibraryEntry("Daylight", BV, "album")]
        self.window.search.setText("Moon")
        self.assertEqual(self.window.grid.count(), 1)
        self.window.tabs.setCurrentIndex(1)
        self.assertIn("当前显示 0", self.window.library_count.text())
        dialog.deleteLater()

    def test_reconnect_reloads_real_qt_media_and_restores_position(self):
        fixture = AudioFixture()
        try:
            self.window._append_tracks(self.tracks())
            self.window._submit = lambda fn, done, error=None: done(fn())
            with patch("bili_player.app.BilibiliClient.get_stream", return_value=fixture.url), \
                 patch.object(self.window.player, "play"):
                self.window.play_item(self.window.queue.item(0))
                deadline = time.monotonic() + 6
                while not self.window.player.isSeekable() and time.monotonic() < deadline:
                    QTest.qWait(30)
                self.assertTrue(self.window.player.isSeekable())
                self.window.player.setPosition(700)
                self.assertEqual(self.window._last_played_position, 700)
                old_source = self.window.player.source()
                self.window._media_error(QMediaPlayer.Error.NetworkError, "Simulated interruption")
                self.assertNotEqual(self.window.player.source(), old_source)
                deadline = time.monotonic() + 6
                while self.window._resume_position is not None and time.monotonic() < deadline:
                    QTest.qWait(30)
                self.assertIsNone(self.window._resume_position)
                self.assertEqual(self.window.player.position(), 700)
                self.assertEqual(self.window._reconnect_attempts, 1)
        finally:
            self.window._want_playing = False
            self.window.player.setSource(QUrl())
            fixture.close()

    def test_qt_decodes_audio_through_proxy(self):
        fixture = AudioFixture()
        errors = []
        self.window.player.errorOccurred.connect(lambda _code, message: errors.append(message))
        try:
            self.window.player.setSource(QUrl(self.window.proxy.set_stream(fixture.url)))
            deadline = time.monotonic() + 6
            while self.window.player.duration() == 0 and not errors and time.monotonic() < deadline:
                QTest.qWait(40)
            self.assertFalse(errors, str(errors))
            self.assertGreater(self.window.player.duration(), 0)
            self.assertGreaterEqual(self.window.player.duration(), 1900)
            self.assertTrue(fixture.seen)
            self.assertTrue(self.window.seek.isEnabled())
            slider = self.window.seek
            QTest.mouseClick(slider, Qt.MouseButton.LeftButton,
                             pos=slider.rect().center())
            self.assertAlmostEqual(self.window.player.position(), 1000, delta=100)
            self.assertNotEqual(self.window.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        finally:
            self.window.player.setSource(QUrl())
            fixture.close()


if __name__ == "__main__":
    unittest.main()

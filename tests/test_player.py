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
from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

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
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def tracks(self):
        return [Track(title=f"Song {i}", bvid=BV, cid=i, page=i) for i in (1, 2, 3)]

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
        finally:
            self.window.player.setSource(QUrl())
            fixture.close()


if __name__ == "__main__":
    unittest.main()

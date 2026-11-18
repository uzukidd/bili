"""Real Qt AAC/M4A decoding and cache seek regressions, entirely on loopback."""
from __future__ import annotations

import os
import random
import struct
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import requests
from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import (
    QAudioBuffer, QAudioBufferInput, QAudioBufferOutput, QAudioFormat,
    QAudioOutput, QMediaCaptureSession, QMediaFormat, QMediaPlayer, QMediaRecorder,
)
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from bili_player.audio_cache import BLOCK_SIZE
from bili_player.stream import StreamProxy


def _wait(app, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())


def _record_m4a(app, path):
    """Encode two minutes of deterministic noise without devices or executables."""
    audio_format = QAudioFormat()
    audio_format.setSampleRate(48000)
    audio_format.setChannelCount(1)
    audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    audio_input = QAudioBufferInput(audio_format)
    session = QMediaCaptureSession()
    recorder = QMediaRecorder()
    session.setAudioBufferInput(audio_input)
    session.setRecorder(recorder)
    media_format = QMediaFormat(QMediaFormat.FileFormat.Mpeg4Audio)
    media_format.setAudioCodec(QMediaFormat.AudioCodec.AAC)
    recorder.setMediaFormat(media_format)
    recorder.setAudioSampleRate(48000)
    recorder.setAudioChannelCount(1)
    recorder.setAudioBitRate(192000)
    recorder.setAutoStop(True)
    recorder.setOutputLocation(QUrl.fromLocalFile(str(path)))
    # Noise avoids AAC silence compression producing a fixture smaller than one block.
    pcm = random.Random(71023).randbytes(120 * 48000 * 2)
    offset = 0
    ended = False
    errors = []
    recorder.errorChanged.connect(
        lambda: errors.append((recorder.error().name, recorder.errorString()))
        if recorder.error() != QMediaRecorder.Error.NoError else None)

    def feed():
        nonlocal offset, ended
        while offset < len(pcm):
            end = min(offset + 4096 * 2, len(pcm))
            buffer = QAudioBuffer(pcm[offset:end], audio_format, offset // 2 * 1000000 // 48000)
            if not audio_input.sendAudioBuffer(buffer):
                return
            offset = end
        if not ended:
            ended = audio_input.sendAudioBuffer(QAudioBuffer())

    audio_input.readyToSendAudioBuffer.connect(feed)
    recorder.record()
    try:
        if not _wait(app, lambda: bool(errors) or (
                ended and recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState), 45):
            raise AssertionError(f"Qt AAC encoding timed out after {offset // 2} frames")
        if errors:
            raise AssertionError(f"Qt AAC encoding failed: {errors}")
        if not path.is_file():
            raise AssertionError("Qt recorder did not create the M4A fixture")
    finally:
        recorder.stop()
        session.setAudioBufferInput(None)
        session.setRecorder(None)
        app.processEvents()


def _atoms(data):
    """Read top-level MP4 atom bounds rather than matching arbitrary AAC bytes."""
    atoms = []
    offset = 0
    while offset < len(data):
        if offset + 8 > len(data):
            raise AssertionError("Truncated MP4 atom header")
        size, kind = struct.unpack_from(">I4s", data, offset)
        if size == 1:
            size = struct.unpack_from(">Q", data, offset + 8)[0]
        elif size == 0:
            size = len(data) - offset
        if size < 8 or offset + size > len(data):
            raise AssertionError("Invalid MP4 atom length")
        atoms.append((kind, offset, size))
        offset += size
    return atoms


class M4ARangeFixture:
    """A CDN-like origin: HEAD lacks ETag, GET has it, bodies arrive in tiny writes."""
    def __init__(self, data):
        self.data = data
        self.seen = []
        self.etag = '"qt-m4a-stable"'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_HEAD(self):
                self.serve(True)

            def do_GET(self):
                self.serve(False)

            def serve(self, head):
                ranged = self.headers.get("Range")
                owner.seen.append((self.command, ranged))
                size = len(owner.data)
                start, end = 0, size - 1
                if ranged:
                    first, last = ranged.removeprefix("bytes=").split("-", 1)
                    if first:
                        start = int(first)
                        end = min(int(last), end) if last else end
                    else:
                        start = max(0, size - int(last))
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(206 if ranged else 200)
                self.send_header("Content-Type", "audio/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if not head:
                    self.send_header("ETag", owner.etag)
                if ranged:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if head:
                    return
                try:
                    for offset in range(start, end + 1, 997):
                        self.wfile.write(owner.data[offset:min(offset + 997, end + 1)])
                        self.wfile.flush()
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/audio.m4a"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)


class M4ACacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.media_temp = tempfile.TemporaryDirectory(prefix="qt-m4a-")
        cls.addClassCleanup(cls.media_temp.cleanup)
        cls.media_path = Path(cls.media_temp.name) / "noise.m4a"
        _record_m4a(cls.app, cls.media_path)
        cls.media_data = cls.media_path.read_bytes()
        if len(cls.media_data) <= BLOCK_SIZE:
            raise AssertionError("The real AAC fixture must exceed the 256 KiB cache block")
        atoms = _atoms(cls.media_data)
        kinds = [kind for kind, _, _ in atoms]
        if b"mdat" not in kinds or not kinds or kinds[-1] != b"moov":
            raise AssertionError(f"Expected a non-faststart MP4 with moov at EOF: {kinds}")
        if kinds.index(b"mdat") >= kinds.index(b"moov"):
            raise AssertionError("M4A metadata must follow encoded audio")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qt-m4a-cache-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = M4ARangeFixture(self.media_data)
        self.addCleanup(self.fixture.close)
        self.proxy = StreamProxy(Path(self.temp.name))
        self.addCleanup(self.proxy.close)
        self.url = self.proxy.set_stream(self.fixture.url, cache_key="real-m4a", source_key="loopback")

    def _decode_and_seek(self, url):
        player = QMediaPlayer()
        audio = QAudioOutput()
        audio.setMuted(True)
        player.setAudioOutput(audio)
        decoded = QAudioBufferOutput()
        player.setAudioBufferOutput(decoded)
        errors, statuses, buffers = [], [], []
        player.errorOccurred.connect(lambda error, message: errors.append((error.name, message)))
        player.mediaStatusChanged.connect(statuses.append)
        decoded.audioBufferReceived.connect(
            lambda buffer: buffers.append(buffer.startTime() // 1000) if buffer.isValid() else None)
        try:
            player.setSource(url)
            player.play()
            self.assertTrue(_wait(self.app, lambda: bool(errors) or (
                player.duration() > 119000 and buffers and player.position() > 100)),
                f"No initial decode: duration={player.duration()}, errors={errors}, statuses={statuses}")
            self.assertFalse(errors, f"Qt media errors: {errors}")
            self.assertGreater(player.duration(), 119000)
            self.assertTrue(player.isSeekable())
            for target in (87765, 12345, 108250):
                with self.subTest(target_ms=target):
                    buffers.clear()
                    player.setPosition(target)
                    # Position alone is insufficient: setPosition can update it before decoding.
                    self.assertTrue(_wait(self.app, lambda: bool(errors) or (
                        target + 80 <= player.position() <= target + 2000
                        and any(target - 100 <= stamp <= target + 2000 for stamp in buffers))),
                        f"Seek did not decode: target={target}, position={player.position()}, "
                        f"decoded={buffers[-5:]}, errors={errors}")
                    self.assertFalse(errors, f"Qt media errors after seek: {errors}")
                    self.assertNotIn(QMediaPlayer.MediaStatus.InvalidMedia, statuses)
                    self.assertLess(abs(player.position() - target), 2000)
        finally:
            player.stop()
            player.setSource(QUrl())
            player.setAudioBufferOutput(None)
            player.setAudioOutput(None)
            player.deleteLater()
            decoded.deleteLater()
            audio.deleteLater()
            self.app.processEvents()

    def test_local_m4a_has_real_duration_decodes_and_seeks(self):
        self._decode_and_seek(QUrl.fromLocalFile(str(self.media_path)))

    def test_cold_cache_decodes_and_seeks_with_head_missing_etag(self):
        self.assertEqual(self.proxy.cache_info()["bytes"], 0)
        head = requests.head(self.url, timeout=5)
        self.assertEqual(head.status_code, 200)
        self.assertNotIn("ETag", head.headers)
        self.assertEqual(self.proxy.cache_info()["bytes"], 0)
        self._decode_and_seek(QUrl(self.url))
        self.assertIn(("HEAD", None), self.fixture.seen)
        self.assertTrue(any(method == "GET" and ranged for method, ranged in self.fixture.seen))
        self.assertGreater(self.proxy.cache_info()["bytes"], BLOCK_SIZE)

    def test_head_then_get_adopts_stable_etag_without_invalidating_audio(self):
        head = requests.head(self.url, timeout=5)
        self.assertEqual(head.status_code, 200)
        self.assertNotIn("ETag", head.headers)
        for start in (0, BLOCK_SIZE * 2 + 17, 41, len(self.media_data) - 4096):
            with self.subTest(start=start):
                end = min(start + 4095, len(self.media_data) - 1)
                result = requests.get(self.url, headers={"Range": f"bytes={start}-{end}"}, timeout=5)
                self.assertEqual(result.status_code, 206)
                self.assertEqual(result.content, self.media_data[start:end + 1])
                self.assertEqual(result.headers.get("ETag"), self.fixture.etag)
                self.assertEqual(result.headers["Content-Range"], f"bytes {start}-{end}/{len(self.media_data)}")


if __name__ == "__main__":
    unittest.main()

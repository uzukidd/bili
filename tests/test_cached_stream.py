"""Local HTTP byte-range regression / 本地 HTTP 字节区间回归。"""
from __future__ import annotations

import concurrent.futures
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests

from bili_player.audio_cache import BLOCK_SIZE
from bili_player.stream import StreamProxy


class RangeFixture:
    def __init__(self):
        self.data = bytes(range(256)) * (BLOCK_SIZE * 3 // 256) + b'final-block'
        self.seen = []
        self.etag = '"version1"'
        self.head_etag = True
        self.get_etag = True
        self.ignore_range = False
        self.truncate = False
        self.bad_range = False
        self.unknown_length = False
        self.failures = 0
        self.delay = 0
        self.gate = None
        self.entered = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_HEAD(self): self.serve(True)
            def do_GET(self): self.serve(False)
            def serve(self, head):
                owner.seen.append((self.command, self.headers.get('Range')))
                if owner.failures:
                    owner.failures -= 1
                    self.send_error(503)
                    return
                size = len(owner.data)
                start, end = 0, size - 1
                ranged = self.headers.get('Range')
                if ranged and not owner.ignore_range:
                    values = ranged.removeprefix('bytes=').split('-')
                    start = int(values[0] or 0)
                    end = min(int(values[1]) if values[1] else end, end)
                payload = owner.data[start:end + 1]
                self.send_response(206 if ranged and not owner.ignore_range else 200)
                self.send_header('Content-Type', 'audio/mp4')
                if (head and owner.head_etag) or (not head and owner.get_etag):
                    self.send_header('ETag', owner.etag)
                self.send_header('Accept-Ranges', 'bytes')
                if not owner.unknown_length:
                    self.send_header('Content-Length', str(len(payload)))
                if ranged and not owner.ignore_range:
                    self.send_header('Content-Range', f'bytes {start + int(owner.bad_range)}-{end}/{size}')
                self.end_headers()
                if head: return
                owner.entered.set()
                if owner.gate: owner.gate.wait(3)
                if owner.delay: time.sleep(owner.delay)
                if owner.truncate: payload = payload[:len(payload) // 2]
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                except OSError: pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/audio'
    def close(self):
        if self.gate: self.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)


class CachedStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RangeFixture()
        self.proxy = StreamProxy(Path(self.temp.name))
        self.url = self.proxy.set_stream(self.fixture.url, cache_key='song1', source_key='bili:1')
    def tearDown(self):
        self.proxy.close()
        self.fixture.close()
        self.temp.cleanup()
    def get(self, value=None, url=None):
        return requests.get(url or self.url, headers={'Range': value} if value else {}, timeout=5)

    def test_partial_overlap_missing_interval_persistence_and_cached_offline_read(self):
        result = self.get('bytes=10-99')
        self.assertEqual(result.status_code, 206)
        self.assertEqual(result.content, self.fixture.data[10:100])
        self.assertEqual(result.headers['Content-Range'], f'bytes 10-99/{len(self.fixture.data)}')
        count = len(self.fixture.seen)
        self.assertEqual(self.get('bytes=50-199').content, self.fixture.data[50:200])
        self.assertEqual(len(self.fixture.seen), count)
        start = BLOCK_SIZE * 2 + 5
        self.assertEqual(self.get(f'bytes={start}-{start+100}').content, self.fixture.data[start:start+101])
        self.assertEqual(self.fixture.seen[-1][1], f'bytes={BLOCK_SIZE*2}-{BLOCK_SIZE*3-1}')
        self.assertIsNone(self.proxy.get_cached_stream('song1', 'bili:1'))
        self.proxy.close()
        self.proxy = StreamProxy(Path(self.temp.name))
        self.url = self.proxy.set_stream(self.fixture.url, cache_key='song1', source_key='bili:1')
        with patch('bili_player.stream.requests.request', side_effect=AssertionError('Cached range must not hit network')):
            self.assertEqual(self.get('bytes=50-199').content, self.fixture.data[50:200])

    def test_complete_cache_range_head_suffix_and_416_without_upstream(self):
        self.assertEqual(self.get().content, self.fixture.data)
        count = len(self.fixture.seen)
        self.url = self.proxy.get_cached_stream('song1', 'bili:1')
        self.assertIsNotNone(self.url)
        with patch('bili_player.stream.requests.request', side_effect=AssertionError('Offline cache')):
            head = requests.head(self.url, timeout=3)
            self.assertEqual(int(head.headers['Content-Length']), len(self.fixture.data))
            for value, expected in [('bytes=-11', self.fixture.data[-11:]),
                                    (f'bytes={BLOCK_SIZE}-', self.fixture.data[BLOCK_SIZE:])]:
                self.assertEqual(self.get(value).content, expected)
            invalid = self.get(f'bytes={len(self.fixture.data)}-')
            self.assertEqual(invalid.status_code, 416)
            self.assertEqual(invalid.headers['Content-Range'], f'bytes */{len(self.fixture.data)}')
        self.assertEqual(len(self.fixture.seen), count)

    def test_same_url_different_entities_and_source_changes_do_not_share(self):
        self.get('bytes=0-10')
        count = len(self.fixture.seen)
        self.url = self.proxy.set_stream(self.fixture.url, cache_key='song2', source_key='bili:1')
        self.get('bytes=0-10')
        self.assertGreater(len(self.fixture.seen), count)
        self.assertEqual(self.proxy.cache_info()['entries'], 2)
        self.fixture.data = b'x' * len(self.fixture.data)
        self.url = self.proxy.set_stream(self.fixture.url, cache_key='song1', source_key='other-source')
        self.assertEqual(self.get('bytes=0-10').content, b'x' * 11)

    def test_concurrent_same_block_downloaded_once(self):
        self.fixture.delay = .05
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.get('bytes=100-300'), range(4)))
        self.assertTrue(all(response.content == self.fixture.data[100:301] for response in responses))
        self.assertEqual(len([request for request in self.fixture.seen if request[0] == 'GET']), 1)

    def test_range_ignored_200_is_sliced_from_zero_without_duplicate_bytes(self):
        self.get('bytes=0-99')
        self.fixture.ignore_range = True
        result = self.get()
        self.assertEqual(result.content, self.fixture.data)
        self.assertIsNotNone(self.proxy.get_cached_stream('song1', 'bili:1'))
        self.proxy.clear_cache()
        self.proxy.release_stream()
        self.url = self.proxy.set_stream(self.fixture.url, cache_key='song1', source_key='bili:1')
        start = BLOCK_SIZE + 12
        self.assertEqual(self.get(f'bytes={start}-{start+30}').content, self.fixture.data[start:start+31])

    def test_truncation_and_bad_range_are_not_committed(self):
        for attribute in ('truncate', 'bad_range'):
            with self.subTest(attribute=attribute):
                setattr(self.fixture, attribute, True)
                response = self.get('bytes=0-99')
                self.assertEqual(response.status_code, 502)
                self.assertEqual(self.proxy.cache_info()['bytes'], 0)
                setattr(self.fixture, attribute, False)
        self.assertEqual(self.get('bytes=0-99').content, self.fixture.data[:100])

    def test_head_without_etag_then_get_with_etag_does_not_invalidate_first_read(self):
        self.fixture.head_etag = False
        head = requests.head(self.url, timeout=3)
        self.assertEqual(head.status_code, 200)
        response = self.get('bytes=0-99')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, self.fixture.data[:100])

    def test_missing_etag_on_later_ranges_keeps_valid_blocks(self):
        self.get('bytes=0-99')
        self.fixture.get_etag = False
        response = self.get(f'bytes={BLOCK_SIZE}-{BLOCK_SIZE+99}')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, self.fixture.data[BLOCK_SIZE:BLOCK_SIZE+100])
        self.assertEqual(self.proxy.cache_info()['bytes'], BLOCK_SIZE * 2)

    def test_slow_full_response_reader_does_not_block_new_seek(self):
        self.fixture.ignore_range = True
        self.fixture.data = self.fixture.data * 32
        stalled = requests.get(self.url, stream=True, timeout=5)
        try:
            self.assertEqual(stalled.status_code, 200)
            start = len(self.fixture.data) - 50
            before = time.monotonic()
            result = self.get(f'bytes={start}-')
            self.assertLess(time.monotonic() - before, 2.5)
            self.assertEqual(result.status_code, 206)
            self.assertEqual(result.content, self.fixture.data[start:])
        finally:
            stalled.close()

    def test_cached_seek_does_not_wait_for_an_unrelated_network_miss(self):
        self.get('bytes=0-99')
        self.fixture.entered.clear()
        self.fixture.gate = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.get, f'bytes={BLOCK_SIZE}-{BLOCK_SIZE+99}')
            try:
                self.assertTrue(self.fixture.entered.wait(2))
                before = time.monotonic()
                self.assertEqual(self.get('bytes=0-99').content, self.fixture.data[:100])
                self.assertLess(time.monotonic() - before, 1)
            finally:
                self.fixture.gate.set()
            self.assertEqual(future.result(timeout=3).status_code, 206)

    def test_paused_reader_over_three_seconds_does_not_truncate_response(self):
        self.fixture.ignore_range = True
        self.fixture.data = self.fixture.data * 32
        response = requests.get(self.url, stream=True, timeout=5)
        try:
            time.sleep(3.4)
            self.assertEqual(response.content, self.fixture.data)
        finally:
            response.close()

    def test_releasing_stream_interrupts_old_read_before_reusing_same_entity(self):
        self.get('bytes=0-99')
        self.fixture.entered.clear()
        self.fixture.gate = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.get, f'bytes={BLOCK_SIZE}-{BLOCK_SIZE+99}')
            try:
                self.assertTrue(self.fixture.entered.wait(2))
                self.proxy.release_stream()
                self.url = self.proxy.set_stream(self.fixture.url, cache_key='song1', source_key='bili:1')
                before = time.monotonic()
                self.assertEqual(self.get('bytes=0-99').content, self.fixture.data[:100])
                self.assertLess(time.monotonic() - before, 1)
            finally:
                self.fixture.gate.set()
            self.assertEqual(future.result(timeout=3).status_code, 502)

    def test_range_ignored_body_accepts_arbitrary_network_fragment_sizes(self):
        self.fixture.ignore_range = True
        original = requests.Response.iter_content
        def fragments(response, *args, **kwargs):
            if response.url == self.fixture.url:
                yield from original(response, chunk_size=997)
            else:
                yield from original(response, *args, **kwargs)
        with patch.object(requests.Response, 'iter_content', fragments):
            self.assertEqual(self.get().content, self.fixture.data)

    def test_etag_change_never_concatenates_two_representations(self):
        self.get('bytes=0-99')
        self.fixture.data = b'x' * len(self.fixture.data)
        self.fixture.etag = '"version2"'
        result = self.get(f'bytes={BLOCK_SIZE}-{BLOCK_SIZE+99}')
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.get('bytes=0-99').content, b'x' * 100)

    def test_disk_write_failure_does_not_prevent_playback(self):
        with patch('bili_player.audio_cache.os.replace', side_effect=OSError('disk full')):
            self.assertEqual(self.get().content, self.fixture.data)
        self.assertEqual(self.proxy.cache_info()['bytes'], 0)

    def test_clear_deferred_until_release_keeps_current_range_readable(self):
        self.get('bytes=0-99')
        result = self.proxy.clear_cache()
        self.assertEqual(result['pending_bytes'], BLOCK_SIZE)
        self.assertEqual(self.get('bytes=0-99').content, self.fixture.data[:100])
        self.assertEqual(self.get(f'bytes={BLOCK_SIZE}-{BLOCK_SIZE+99}').content,
                         self.fixture.data[BLOCK_SIZE:BLOCK_SIZE+100])
        self.assertEqual(self.proxy.cache_info()['bytes'], BLOCK_SIZE)
        old = self.url
        self.proxy.release_stream()
        self.assertEqual(self.proxy.cache_info()['bytes'], 0)
        self.assertEqual(self.get(url=old).status_code, 404)

    def test_transient_server_error_retries(self):
        self.fixture.failures = 1
        self.assertEqual(self.get('bytes=0-99').content, self.fixture.data[:100])
        self.assertEqual(len(self.fixture.seen), 2)

    def test_unknown_length_200_falls_back_to_streaming_without_cache(self):
        self.fixture.ignore_range = True
        self.fixture.unknown_length = True
        self.assertEqual(self.get().content, self.fixture.data)
        self.assertEqual(self.proxy.cache_info()['bytes'], 0)

    def test_close_interrupts_inflight_request_without_hanging(self):
        self.fixture.gate = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.get)
            self.assertTrue(self.fixture.entered.wait(2))
            before = time.monotonic()
            self.proxy.close()
            self.assertLess(time.monotonic() - before, 2)
            self.fixture.gate.set()
            try:
                future.result(timeout=3)
            except requests.RequestException:
                pass


if __name__ == '__main__':
    unittest.main()

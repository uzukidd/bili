"""Qt-compatible HTTP relay with an optional persistent, entity-scoped block cache."""
from __future__ import annotations

import re
import secrets
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from .api import HEADERS
from .audio_cache import AudioCache, BLOCK_SIZE, CacheEntry


class UpstreamError(OSError):
    pass


@dataclass(eq=False)
class _Stream:
    url: str
    entry: CacheEntry | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)


def _range(value: str | None, size: int) -> tuple[int, int, bool]:
    if not value:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or not any(match.groups()):
        raise ValueError("Invalid byte range")
    first, last = match.groups()
    if not first:
        length = int(last)
        if length <= 0:
            raise ValueError("Empty suffix")
        return max(0, size - length), size - 1, True
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError("Unsatisfiable byte range")
    return start, end, True


def _content_type(response) -> str:
    value = response.headers.get("Content-Type", "application/octet-stream")
    mime = value.split(";", 1)[0].strip().lower()
    if not (mime.startswith(("audio/", "video/")) or mime in (
        "application/octet-stream", "binary/octet-stream", "application/mp4", "application/ogg",
    )):
        raise UpstreamError("Upstream did not return audio")
    if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
        raise UpstreamError("Encoded audio representation")
    return value


def _audio_bytes(data: bytes):
    prefix = data[:128].lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html", b"<?xml", b'{"', b"[{") ):
        raise UpstreamError("Upstream returned an error document")


class StreamProxy:
    def __init__(self, cache_directory: Path | None = None,
                 max_cache_bytes=1024**3, target_cache_bytes=800 * 1024**2) -> None:
        self._lock = threading.RLock()
        self._streams: dict[str, _Stream] = {}
        self._closed = threading.Event()
        self._connections: set[socket.socket] = set()
        self._responses: set[requests.Response] = set()
        self._cache = (AudioCache(cache_directory, max_cache_bytes, target_cache_bytes)
                       if cache_directory is not None else None)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(3)
                with owner._lock:
                    owner._connections.add(self.connection)

            def finish(self):
                try:
                    super().finish()
                finally:
                    with owner._lock:
                        owner._connections.discard(self.connection)

            def log_message(self, *_args):
                pass

            def do_HEAD(self):
                self._serve(True)

            def do_GET(self):
                self._serve(False)

            def _headers(self, status, size, content_type, start=0, end=None, etag=""):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                if size is not None:
                    end = size - 1 if end is None else end
                    self.send_header("Content-Length", str(end - start + 1))
                    if status == 206:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                if etag:
                    self.send_header("ETag", etag)
                self.send_header("Connection", "close")
                self.end_headers()

            def _serve(self, head):
                started = False
                stream = None
                body = None
                with owner._lock:
                    stream = owner._streams.get(self.path)
                    if stream and stream.entry:
                        owner._cache.acquire(stream.entry)
                try:
                    if not stream or stream.cancelled.is_set():
                        self.send_error(404)
                        return
                    if stream.entry is None:
                        self._relay(stream, head)
                        return
                    entry = stream.entry
                    # Hold the entry lock across the initial fetch only. Subsequent range
                    # blocks each acquire it, allowing cached readers between misses.
                    with entry.download_lock:
                        initial = None
                        full = None
                        if entry.size is None:
                            if head:
                                owner._head(stream)
                            if entry.size is None:
                                initial, full = owner._fetch(stream, 0)
                        size = entry.size
                        if size is None:
                            if full is None:
                                raise UpstreamError("Missing upstream audio length")
                            try:
                                self._headers(200, None, _content_type(full))
                                started = True
                                if not head:
                                    for data in full.iter_content(64 * 1024):
                                        owner._check(stream)
                                        self.wfile.write(data)
                            finally:
                                owner._close_response(full)
                            return
                        try:
                            start, end, partial = _range(self.headers.get("Range"), size)
                        except ValueError:
                            if full is not None:
                                owner._close_response(full)
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.send_header("Content-Length", "0")
                            self.send_header("Connection", "close")
                            self.end_headers()
                            return
                        revision = entry.revision
                        if head:
                            if full is not None:
                                owner._close_response(full)
                            self._headers(206 if partial else 200, size, entry.content_type,
                                          start, end, entry.etag)
                            return
                        body = owner._body(stream, start, end, revision, initial, full)
                        # Buffer at most one block before committing HTTP headers. A
                        # failed/truncated first download can still become a proper 502.
                        first = next(body)
                    self._headers(206 if partial else 200, size, entry.content_type,
                                  start, end, entry.etag)
                    started = True
                    self.wfile.write(first)
                    for data in body:
                        owner._check(stream)
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                except (requests.RequestException, OSError, ValueError, StopIteration):
                    if not started:
                        try:
                            self.send_error(502, "Audio upstream unavailable")
                        except OSError:
                            pass
                    # After headers, terminate the connection. Content-Length lets Qt
                    # detect the short transfer rather than accept truncated success.
                finally:
                    if body is not None:
                        body.close()
                    if stream and stream.entry:
                        owner._cache.release(stream.entry)
                    self.close_connection = True

            def _relay(self, stream, head):
                headers = {**HEADERS, "Accept-Encoding": "identity"}
                if self.headers.get("Range"):
                    headers["Range"] = self.headers["Range"]
                started = False
                response = None
                try:
                    response = owner._request(stream, "HEAD" if head else "GET", headers)
                    self.send_response(response.status_code)
                    for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "ETag"):
                        if name in response.headers:
                            self.send_header(name, response.headers[name])
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                    if not head:
                        for chunk in response.iter_content(64 * 1024):
                            owner._check(stream)
                            if chunk:
                                self.wfile.write(chunk)
                except (requests.RequestException, OSError):
                    if not started:
                        self.send_error(502, "Audio upstream unavailable")
                finally:
                    if response is not None:
                        owner._close_response(response)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._server.block_on_close = False
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True,
                                        name="audio-proxy")
        self._thread.start()

    def _check(self, stream):
        if self._closed.is_set() or stream.cancelled.is_set():
            raise UpstreamError("Stream released")

    def _request(self, stream, method, headers, attempts=3):
        self._check(stream)
        last = None
        for attempt in range(attempts):
            self._check(stream)
            try:
                response = requests.request(method, stream.url, headers=headers,
                                            stream=True, timeout=(5, 10), allow_redirects=True)
                if response.status_code in (408, 429, 500, 502, 503, 504) and attempt < attempts - 1:
                    response.close()
                else:
                    with self._lock:
                        self._responses.add(response)
                    return response
            except requests.RequestException as exc:
                last = exc
            if stream.cancelled.wait(0.05 * (attempt + 1)):
                self._check(stream)
        raise UpstreamError("Audio upstream unavailable") from last

    def _close_response(self, response):
        try:
            response.close()
        finally:
            with self._lock:
                self._responses.discard(response)

    def _head(self, stream):
        response = self._request(stream, "HEAD", {**HEADERS, "Accept-Encoding": "identity"})
        try:
            if response.status_code != 200:
                return
            content_type = _content_type(response)
            size = int(response.headers.get("Content-Length", "0"))
            if size > 0:
                self._cache.representation(stream.entry, size, content_type,
                                           response.headers.get("ETag", ""))
        finally:
            self._close_response(response)

    def _fetch(self, stream, index):
        """Called with entry.download_lock. Return a verified block or full-200 response."""
        entry = stream.entry
        self._check(stream)
        cached = self._cache.read_block(entry, index)
        if cached is not None:
            return cached, None
        if not stream.url:
            raise UpstreamError("Cached block is unavailable")
        start = index * BLOCK_SIZE
        end = start + BLOCK_SIZE - 1
        if entry.size is not None:
            end = min(end, entry.size - 1)
        headers = {**HEADERS, "Accept-Encoding": "identity", "Range": f"bytes={start}-{end}"}
        if entry.etag and not entry.etag.startswith("W/"):
            headers["If-Range"] = entry.etag
        error = None
        for attempt in range(3):
            response = None
            try:
                response = self._request(stream, "GET", headers, attempts=1)
                content_type = _content_type(response)
                etag = response.headers.get("ETag", "")
                if response.status_code == 200:
                    size = int(response.headers.get("Content-Length", "0"))
                    if size <= 0:
                        if "Content-Length" in response.headers:
                            raise UpstreamError("Empty full audio response")
                        return None, response
                    self._cache.representation(entry, size, content_type, etag)
                    return None, response
                if response.status_code != 206:
                    raise UpstreamError(f"Audio upstream status {response.status_code}")
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                     response.headers.get("Content-Range", ""))
                if not match:
                    raise UpstreamError("Invalid Content-Range")
                first, last, size = map(int, match.groups())
                length = int(response.headers.get("Content-Length", str(last - first + 1)))
                if (first != start or size <= 0 or last != min(end, size - 1)
                        or last < first or length != last - first + 1 or length > BLOCK_SIZE):
                    raise UpstreamError("Mismatched audio range")
                data = bytearray()
                for part in response.iter_content(BLOCK_SIZE):
                    self._check(stream)
                    if len(data) + len(part) > length:
                        raise UpstreamError("Oversized audio range")
                    data.extend(part)
                if len(data) != length:
                    raise UpstreamError("Truncated audio range")
                if first == 0:
                    _audio_bytes(data)
                self._cache.representation(entry, size, content_type, etag)
                result = bytes(data)
                self._cache.write_block(entry, index, result)
                return result, None
            except (requests.RequestException, OSError, ValueError) as exc:
                error = exc
            finally:
                if response is not None and not (response.status_code == 200 and error is None):
                    self._close_response(response)
            if stream.cancelled.wait(0.05 * (attempt + 1)):
                self._check(stream)
            # Never keep a failed attempt's error as state for a successful full-200 retry.
            error, previous = None, error
        raise UpstreamError("Cannot download verified audio block") from previous

    def _body(self, stream, start, end, revision, initial=None, full=None):
        entry = stream.entry
        try:
            index = start // BLOCK_SIZE
            while index * BLOCK_SIZE <= end:
                self._check(stream)
                with entry.download_lock:
                    if full is not None:
                        response, full = full, None
                        yield from self._full_body(stream, response, max(start, index * BLOCK_SIZE), end, revision)
                        return
                    if index == 0 and initial is not None:
                        data, initial = initial, None
                    else:
                        data, full = self._fetch(stream, index)
                    if entry.revision != revision:
                        raise UpstreamError("Audio representation changed during playback")
                    if full is not None:
                        response, full = full, None
                        yield from self._full_body(stream, response, max(start, index * BLOCK_SIZE), end, revision)
                        return
                left = max(start - index * BLOCK_SIZE, 0)
                right = min(end - index * BLOCK_SIZE + 1, len(data))
                if right <= left:
                    raise UpstreamError("Audio range no longer exists")
                yield data[left:right]
                index += 1
        finally:
            if full is not None:
                self._close_response(full)

    def _full_body(self, stream, response, start, end, revision):
        """A Range-ignored 200 always starts at zero; keep at most one block in RAM."""
        entry = stream.entry
        position = 0
        try:
            for data in response.iter_content(BLOCK_SIZE):
                self._check(stream)
                if entry.revision != revision:
                    raise UpstreamError("Audio representation changed during playback")
                expected = min(BLOCK_SIZE, entry.size - position)
                if not data or len(data) != expected or position >= entry.size:
                    raise UpstreamError("Truncated or oversized full audio response")
                if position == 0:
                    _audio_bytes(data)
                self._cache.write_block(entry, position // BLOCK_SIZE, data)
                left, right = max(start - position, 0), min(end - position + 1, len(data))
                if right > left:
                    yield data[left:right]
                position += len(data)
                if position > end:
                    return
            if position <= end:
                raise UpstreamError("Truncated full audio response")
        finally:
            self._close_response(response)

    def _activate(self, stream):
        token = "/audio/" + secrets.token_urlsafe(24)
        self._streams = {token: stream}
        return f"http://127.0.0.1:{self._server.server_port}{token}"

    def set_stream(self, url: str, *, cache_key: str = "", source_key: str = "") -> str:
        with self._lock:
            self.release_stream()
            if self._closed.is_set():
                raise RuntimeError("StreamProxy is closed")
            entry = self._cache.activate(cache_key, source_key, url) if self._cache and cache_key else None
            return self._activate(_Stream(url, entry))

    def get_cached_stream(self, cache_key: str, source_key: str) -> str | None:
        with self._lock:
            if not self._cache or not cache_key or self._closed.is_set():
                return None
            entry = self._cache.activate(cache_key, source_key, complete_only=True)
            if entry is None:
                return None
            self._cache.acquire(entry)
            try:
                self.release_stream()
                # The lease protects a same-entry hit from eviction while switching.
                entry.active = True
                return self._activate(_Stream("", entry))
            finally:
                self._cache.release(entry)

    def release_stream(self) -> None:
        with self._lock:
            streams, self._streams = self._streams, {}
            for stream in streams.values():
                stream.cancelled.set()
                if stream.entry:
                    self._cache.deactivate(stream.entry)

    def cache_info(self) -> dict:
        return self._cache.info() if self._cache else {"bytes": 0, "entries": 0, "pending_bytes": 0}

    def clear_cache(self) -> dict:
        return self._cache.clear() if self._cache else {"removed_bytes": 0, "pending_bytes": 0}

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self.release_stream()
        with self._lock:
            connections = list(self._connections)
            responses = list(self._responses)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # Interrupt in-flight upstream reads without waiting for a response's buffered
        # reader lock. Handlers own response.close() and release their cache leases.
        for response in responses:
            try:
                upstream = response.raw._fp.fp.raw._sock
                upstream.shutdown(socket.SHUT_RDWR)
            except (AttributeError, OSError):
                pass
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)

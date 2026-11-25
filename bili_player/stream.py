"""Qt-compatible HTTP relay with an optional persistent, entity-scoped block cache."""
from __future__ import annotations

import logging
import re
import secrets
import socket
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from requests import RequestException, Response
from requests.structures import CaseInsensitiveDict

from . import network as requests
from .api import HEADERS
from .audio_cache import AudioCache, BLOCK_SIZE, CacheEntry

logger = logging.getLogger(__name__)


class UpstreamError(OSError):
    pass


class _UpstreamStatus(UpstreamError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Audio upstream status {status}")


class _RestartDownload(UpstreamError):
    pass


@dataclass(eq=False)
class _Stream:
    url: str
    entry: CacheEntry | None = None
    http_headers: dict[str, str] | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    progress_at: float = 0.0


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
        self._responses: dict[Response, _Stream] = {}
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

            def _write(self, stream, data):
                # A paused decoder may stop draining its socket. Retry short writes
                # without truncating the declared HTTP body, and remain cancellable.
                remaining = memoryview(data)
                while remaining:
                    owner._check(stream)
                    try:
                        sent = self.connection.send(remaining)
                    except socket.timeout:
                        continue
                    if not sent:
                        raise ConnectionResetError("Audio reader disconnected")
                    remaining = remaining[sent:]

            def _headers(self, status, size, content_type, start=0, end=None, etag=""):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes" if size is not None else "none")
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
                    with entry.download_lock if entry.size is None else nullcontext():
                        initial = None
                        full = None
                        if entry.size is None:
                            if head:
                                owner._head(stream)
                            if entry.size is None:
                                initial, full = owner._fetch(stream, 0)
                        size = entry.size
                        if size is not None:
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
                    if size is None:
                        if full is None:
                            raise UpstreamError("Missing upstream audio length")
                        try:
                            self._headers(200, None, _content_type(full))
                            started = True
                            if not head:
                                for data in full.iter_content(64 * 1024):
                                    owner._check(stream)
                                    if data:
                                        stream.progress_at = time.monotonic()
                                    self._write(stream, data)
                        finally:
                            owner._close_response(full)
                        return
                    if head:
                        if full is not None:
                            owner._close_response(full)
                        self._headers(206 if partial else 200, size, entry.content_type,
                                      start, end, entry.etag)
                        return
                    body = owner._body(stream, start, end, revision, initial, full)
                    # Only a verified first block is needed before committing headers.
                    # No entry lock may cross a generator yield or socket write.
                    first = next(body)
                    self._headers(206 if partial else 200, size, entry.content_type,
                                  start, end, entry.etag)
                    started = True
                    self._write(stream, first)
                    for data in body:
                        owner._check(stream)
                        self._write(stream, data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                except (RequestException, OSError, ValueError, StopIteration) as exc:
                    if stream and not stream.cancelled.is_set() and not owner._closed.is_set():
                        # Do not log signed upstream URLs or response credentials.
                        detail = str(exc) if isinstance(exc, UpstreamError) else type(exc).__name__
                        logger.warning("Audio proxy %s failed (%s): %s", self.command,
                                       "body" if started else "headers", detail)
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
                headers = {"Accept-Encoding": "identity"}
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
                                stream.progress_at = time.monotonic()
                                self._write(stream, chunk)
                except (RequestException, OSError):
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
        upstream_headers = CaseInsensitiveDict(HEADERS if stream.http_headers is None else stream.http_headers)
        upstream_headers.update(headers)
        last = None
        for attempt in range(attempts):
            self._check(stream)
            try:
                response = requests.request(method, stream.url, headers=upstream_headers,
                                            stream=True, timeout=(5, 10), allow_redirects=True)
                if response.status_code in (408, 429, 500, 502, 503, 504) and attempt < attempts - 1:
                    response.close()
                else:
                    with self._lock:
                        self._responses[response] = stream
                    if stream.cancelled.is_set() or self._closed.is_set():
                        self._close_response(response)
                        self._check(stream)
                    if response.status_code in (200, 206):
                        stream.progress_at = time.monotonic()
                    return response
            except RequestException as exc:
                last = exc
            if stream.cancelled.wait(0.05 * (attempt + 1)):
                self._check(stream)
        raise UpstreamError("Audio upstream unavailable") from last

    def _close_response(self, response):
        try:
            response.close()
        finally:
            with self._lock:
                self._responses.pop(response, None)

    def _head(self, stream):
        response = self._request(stream, "HEAD", {"Accept-Encoding": "identity"})
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

    def _fetch(self, stream, index, *, use_cache=True):
        """Called with entry.download_lock. Return a verified block or full-200 response."""
        entry = stream.entry
        self._check(stream)
        cached = self._cache.read_block(entry, index) if use_cache else None
        if cached is not None:
            return cached, None
        if not stream.url:
            raise UpstreamError("Cached block is unavailable")
        start = index * BLOCK_SIZE
        end = start + BLOCK_SIZE - 1
        if entry.size is not None and use_cache:
            end = min(end, entry.size - 1)
        headers = {"Accept-Encoding": "identity", "Range": f"bytes={start}-{end}"}
        if entry.etag and not entry.etag.startswith("W/"):
            headers["If-Range"] = entry.etag
        error = None
        for attempt in range(3):
            response = None
            try:
                response = self._request(stream, "GET", headers, attempts=1)
                if response.status_code not in (200, 206):
                    raise _UpstreamStatus(response.status_code)
                content_type = _content_type(response)
                etag = response.headers.get("ETag", "")
                if response.status_code == 200:
                    if response.headers.get("Content-Range"):
                        raise UpstreamError("Unexpected Content-Range on full response")
                    size = int(response.headers.get("Content-Length", "0"))
                    if size <= 0:
                        if "Content-Length" in response.headers:
                            raise UpstreamError("Empty full audio response")
                        return None, response
                    self._cache.representation(entry, size, content_type, etag)
                    return None, response
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
                for part in response.iter_content(64 * 1024):
                    self._check(stream)
                    if part:
                        stream.progress_at = time.monotonic()
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
            except (RequestException, OSError, ValueError) as exc:
                error = exc
            finally:
                if response is not None and not (response.status_code == 200 and error is None):
                    self._close_response(response)
            if stream.cancelled.wait(0.05 * (attempt + 1)):
                self._check(stream)
            # Never keep a failed attempt's error as state for a successful full-200 retry.
            error, previous = None, error
        if isinstance(previous, _UpstreamStatus):
            raise previous
        raise UpstreamError("Cannot download verified audio block") from previous

    def _body(self, stream, start, end, revision, initial=None, full=None, *, downloading=False):
        entry = stream.entry
        try:
            index = start // BLOCK_SIZE
            while index * BLOCK_SIZE <= end:
                self._check(stream)
                data = None
                if full is None:
                    data = self._cache.read_block(entry, index)
                    if data is None:
                        with entry.download_lock:
                            if index == 0 and initial is not None:
                                data, initial = initial, None
                            else:
                                try:
                                    data, full = self._fetch(stream, index)
                                except _UpstreamStatus as exc:
                                    if downloading and index > 0 and exc.status == 416:
                                        raise _RestartDownload() from exc
                                    raise
                if entry.revision != revision or (downloading and full is not None and index > 0):
                    if downloading:
                        raise _RestartDownload()
                    raise UpstreamError("Audio representation changed during playback")
                if full is not None:
                    # Never keep the entry lock while yielding to a socket: FFmpeg
                    # opens a seek connection before closing/draining the old one.
                    response = full
                    # Let the handler send validated headers before scanning a
                    # Range-ignored response's prefix for a distant seek.
                    yield b""
                    full = None
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
            buffered = bytearray()
            for part in response.iter_content(64 * 1024):
                self._check(stream)
                if part:
                    stream.progress_at = time.monotonic()
                buffered.extend(part)
                if entry.revision != revision:
                    raise UpstreamError("Audio representation changed during playback")
                if position + len(buffered) > entry.size:
                    raise UpstreamError("Oversized full audio response")
                while buffered and len(buffered) >= min(BLOCK_SIZE, entry.size - position):
                    expected = min(BLOCK_SIZE, entry.size - position)
                    if expected <= 0:
                        raise UpstreamError("Oversized full audio response")
                    data = bytes(buffered[:expected])
                    del buffered[:expected]
                    if position == 0:
                        _audio_bytes(data)
                    self._cache.write_block(entry, position // BLOCK_SIZE, data, revision=revision)
                    if entry.revision != revision:
                        raise UpstreamError("Audio representation changed during playback")
                    left, right = max(start - position, 0), min(end - position + 1, len(data))
                    position += len(data)
                    if right > left:
                        yield data[left:right]
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
            headers = getattr(url, "http_headers", None)
            entry = self._cache.activate(cache_key, source_key, url) if self._cache and cache_key else None
            return self._activate(_Stream(url, entry, dict(headers) if headers is not None else None))

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

    def save_cached_audio(self, cache_key: str, source_key: str, save) -> str | None:
        if not self._cache or self._closed.is_set():
            return None
        entry = self._cache.acquire_complete(cache_key, source_key)
        if entry is None:
            return None
        try:
            with entry.download_lock:
                size, revision = entry.size, entry.revision

                def chunks():
                    for index in range((size + BLOCK_SIZE - 1) // BLOCK_SIZE):
                        data = self._cache.read_block(entry, index)
                        if data is None or entry.revision != revision:
                            raise UpstreamError("Incomplete cached audio")
                        yield data

                try:
                    return save(entry.content_type, chunks(), size=size)
                except UpstreamError:
                    return None
        finally:
            self._cache.release(entry)

    def download_audio(self, cache_key, source_key, resolve, save, *, allow_cached=False):
        """Save the complete representation using the same verified range reader as playback."""
        cached_source = None
        if allow_cached:
            try:
                saved = self.save_cached_audio(cache_key, source_key, save)
            except ValueError:
                saved = None  # Unknown MIME can still be resolved from a fresh URL's suffix.
            if saved is not None:
                return saved
            with self._lock:
                cached_source = next((stream for stream in self._streams.values() if stream.url and stream.entry
                                      and (stream.entry.key, stream.entry.source) == (cache_key, source_key)), None)

        try:
            if cached_source is not None:
                try:
                    return self._download_ranges(cached_source.url, cached_source.http_headers, cache_key, source_key, save)
                except _UpstreamStatus as exc:
                    if exc.status not in (401, 403, 410):
                        raise
            url = resolve()
            return self._download_ranges(url, getattr(url, "http_headers", None), cache_key, source_key, save)
        except _UpstreamStatus as exc:
            if exc.status == 403:
                raise ValueError("音频服务器拒绝下载请求（HTTP 403）") from None
            raise ValueError(f"音频请求失败（HTTP {exc.status}）") from None
        except (UpstreamError, RequestException):
            raise ValueError("下载音频时网络请求失败") from None

    def _download_ranges(self, url, headers, key, source, save):
        if self._cache is None:
            raise UpstreamError("Audio cache unavailable")
        stream = _Stream(url, http_headers=headers)
        self._check(stream)
        confirmed = self._cache.cached_validator(key, source, url)
        if confirmed:
            # Compare strong validators before reusing any bytes from an unrelated URL.
            _, size, etag = confirmed
            try:
                response = self._request(stream, "HEAD", {"Accept-Encoding": "identity"}, attempts=1)
            except UpstreamError:
                confirmed = None
            else:
                try:
                    if (response.status_code != 200 or response.headers.get("ETag") != etag
                            or response.headers.get("Content-Length") != str(size)):
                        confirmed = None
                finally:
                    self._close_response(response)
        stream.entry = self._cache.activate(key, source, url, download=True, confirmed=confirmed)
        try:
            try:
                return self._save_download(stream, save)
            except _RestartDownload:
                # A late 200 or changed validator invalidated the in-progress output.
                return self._save_download(stream, save, from_zero=True)
        finally:
            self._cache.release(stream.entry)

    def _save_download(self, stream, save, *, from_zero=False):
        entry = stream.entry
        with entry.download_lock:
            first, full = self._fetch(stream, 0, use_cache=not from_zero)
            size, revision = entry.size, entry.revision
        if full is not None:
            try:
                def chunks():
                    for data in full.iter_content(BLOCK_SIZE):
                        self._check(stream)
                        yield data

                if "Content-Length" not in full.headers:
                    return save(_content_type(full), chunks(), size=None, url=stream.url)
                body = self._full_body(stream, full, 0, size - 1, revision)
                try:
                    return save(entry.content_type, body, size=size, url=stream.url)
                finally:
                    body.close()
            finally:
                if full in self._responses:
                    self._close_response(full)
        # Close the generator on disk failure or a rejected file size/format.
        body = self._body(stream, 0, size - 1, revision, first, downloading=True)
        try:
            return save(entry.content_type, body, size=size, url=stream.url)
        finally:
            body.close()

    def release_stream(self) -> None:
        with self._lock:
            streams, self._streams = self._streams, {}
            for stream in streams.values():
                stream.cancelled.set()
                for response, owner in list(self._responses.items()):
                    if owner is stream:
                        self._interrupt_response(response)
                if stream.entry:
                    self._cache.deactivate(stream.entry)

    def last_progress_time(self) -> float:
        with self._lock:
            return max((stream.progress_at for stream in self._streams.values()), default=0.0)

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
            self._interrupt_response(response)
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)

    @staticmethod
    def _interrupt_response(response):
        try:
            upstream = response.raw._fp.fp.raw._sock
            upstream.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass

"""Relay an audio stream with Bilibili headers for Qt's media backend."""

from __future__ import annotations

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from .api import HEADERS


class StreamProxy:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: dict[str, str] = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_HEAD(self):
                self._relay(head=True)

            def do_GET(self):
                self._relay(head=False)

            def _relay(self, head: bool):
                with owner._lock:
                    url = owner._streams.get(self.path)
                if not url:
                    self.send_error(404)
                    return
                headers = {**HEADERS, "Accept-Encoding": "identity"}
                if self.headers.get("Range"):
                    headers["Range"] = self.headers["Range"]
                started = False
                try:
                    with requests.request(
                        "HEAD" if head else "GET", url, headers=headers,
                        stream=True, timeout=(5, 15), allow_redirects=True,
                    ) as response:
                        self.send_response(response.status_code)
                        for name in (
                            "Content-Type", "Content-Length", "Content-Range", "Accept-Ranges",
                        ):
                            if name in response.headers:
                                self.send_header(name, response.headers[name])
                        self.send_header("Connection", "close")
                        self.end_headers()
                        started = True
                        if not head:
                            for chunk in response.iter_content(64 * 1024):
                                if chunk:
                                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                except (requests.RequestException, OSError):
                    if not started:
                        try:
                            self.send_error(502, "Audio upstream unavailable")
                        except OSError:
                            pass
                finally:
                    self.close_connection = True

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True,
        )
        self._thread.start()

    def set_stream(self, url: str) -> str:
        token = "/audio/" + secrets.token_urlsafe(24)
        with self._lock:
            self._streams = {token: url}
        return f"http://127.0.0.1:{self._server.server_port}{token}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)

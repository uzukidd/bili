from __future__ import annotations

import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

import requests

from bili_player import network
from bili_player.api import BilibiliClient
from bili_player.lists import BilibiliListClient
from bili_player.models import LibraryEntry, Track
from bili_player.stream import StreamProxy
from bili_player.youtube import YouTubeClient, YouTubeError, yt_dlp


VIDEO_ID = "BaW_jenozKc"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


class ProxyTests(unittest.TestCase):
    def setUp(self):
        network.set_proxy("system", "")
        self.addCleanup(network.set_proxy, "system", "")

    def test_manual_proxy_validation(self):
        self.assertEqual(network.validate_proxy_url(" HTTPS://Proxy.Example:443/ "),
                         "https://proxy.example:443")
        self.assertEqual(network.validate_proxy_url("http://[::1]:8080"), "http://[::1]:8080")
        for invalid in (
            "localhost:80", "http://localhost", "https://host:0", "https://host:65536",
            "ftp://host:20", "socks5://host:1080", "http://user:secret@host:80",
            "http://host:80/path", "http://host:80?", "http://host:80#", "http://ho st:80",
            "http://host\\other:80", "http://[::1]extra:80", "http://host:8\t0",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "代理地址"):
                network.validate_proxy_url(invalid)

    def test_system_proxy_manual_override_direct_and_loopback(self):
        external = "https://example.net/media"
        loopback = ["localhost", "LOCALHOST.", "test.localhost", "127.0.0.1", "127.8.1.2",
                    "127.1", "[::1]", "[::ffff:127.0.0.3]"]
        proxy = "http://system.example:8080"
        variables = {"HTTPS_PROXY": proxy, "NO_PROXY": "bypass.example.net"}
        with patch.dict(os.environ, variables, clear=True):
            self.assertEqual(network.proxy_for_url(external), proxy)
            self.assertIsNone(network.proxy_for_url("https://bypass.example.net/media"))
            self.assertTrue(all(network.proxy_for_url(f"http://{host}:7890") is None for host in loopback))
            network.set_proxy("manual", "http://localhost:7890")
            self.assertEqual(network.proxy_for_url(external), "http://localhost:7890")
            self.assertEqual(network.proxy_for_url("https://bypass.example.net/media"), "http://localhost:7890")
            self.assertTrue(all(network.proxy_for_url(f"http://{host}:7890") is None for host in loopback))
            with self.assertRaises(ValueError):
                network.set_proxy("manual", "bad")
            self.assertEqual(network.proxy_for_url(external), "http://localhost:7890")
            network.set_proxy("direct", "")
            self.assertIsNone(network.proxy_for_url(external))
            self.assertEqual(dict(os.environ), variables)

    def _serve(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_clients_use_manual_proxy_and_redirect_back_to_loopback(self):
        seen = []
        for_client = b'{"code":0,"data":{"value":1}}'

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(request):
                seen.append(request.path)
                if request.path.startswith("http://public.example.invalid/"):
                    request.send_response(302)
                    request.send_header("Location", proxy_url + "/local")
                    request.end_headers()
                else:
                    request.send_response(200)
                    request.send_header("Content-Length", str(len(for_client)))
                    request.end_headers()
                    request.wfile.write(for_client)

        proxy_url = self._serve(Handler)
        network.set_proxy("manual", proxy_url)
        for client_class in (BilibiliClient, BilibiliListClient):
            with self.subTest(client=client_class):
                client = client_class()
                self.addCleanup(client.session.close)
                self.assertEqual(client._get("http://public.example.invalid/data", {}), {"value": 1})
        self.assertEqual(seen, ["http://public.example.invalid/data", "/local"] * 2)

    def test_supplied_session_remains_in_charge_of_requests(self):
        session = Mock(spec=requests.Session)
        session.headers = {}
        session.get.return_value.json.return_value = {"code": 0, "data": {}}
        network.set_proxy("direct", "")
        BilibiliClient(session=session)._get("https://example.net/", {"test": True})
        session.get.assert_called_once_with("https://example.net/", params={"test": True}, timeout=15)

    def test_relay_preserves_origin_headers_and_owns_range(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(request):
                seen.append((request.path, request.headers.get("Referer"), request.headers.get("User-Agent"),
                             request.headers.get("Range"), request.headers.get("Accept-Encoding")))
                request.send_response(206)
                request.send_header("Content-Type", "audio/mp4")
                request.send_header("Content-Length", "2")
                request.send_header("Content-Range", "bytes 0-1/2")
                request.end_headers()
                request.wfile.write(b"hi")

        class URL(str):
            http_headers = {"referer": WATCH_URL, "user-agent": "ExtractorAgent",
                            "range": "extractor-range", "accept-encoding": "gzip"}

        upstream = self._serve(Handler)
        network.set_proxy("manual", "http://127.0.0.1:1")
        for with_cache in (False, True):
            with self.subTest(with_cache=with_cache), TemporaryDirectory() as directory:
                relay = StreamProxy(directory if with_cache else None)
                try:
                    local = relay.set_stream(URL(upstream + "/audio"), cache_key="track", source_key="youtube:id")
                    with network.get(local, headers={"Range": "bytes=0-1"}, timeout=3) as response:
                        self.assertEqual(response.status_code, 206)
                        self.assertEqual(response.content, b"hi")
                finally:
                    relay.close()
        self.assertEqual(seen, [("/audio", WATCH_URL, "ExtractorAgent", "bytes=0-1", "identity")] * 1 +
                         [("/audio", WATCH_URL, "ExtractorAgent", "bytes=0-262143", "identity")])


@unittest.skipIf(yt_dlp is None, "yt-dlp unavailable")
class YouTubeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(network.set_proxy, "system", "")
        network.set_proxy("system", "")
        self.extractor = MagicMock()
        self.real_factory = yt_dlp.YoutubeDL
        self.factory = MagicMock()
        self.factory.return_value.__enter__.return_value = self.extractor
        patcher = patch.object(yt_dlp, "YoutubeDL", self.factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = YouTubeClient(timeout=9, use_chrome=False)
        self.entry = LibraryEntry("", VIDEO_ID, provider="youtube")
        self.track = Track("Video", VIDEO_ID, 0, 1, provider="youtube")
        self.info = {"id": VIDEO_ID, "title": "Video", "duration": 123.9,
                     "thumbnail": "https://i.ytimg.com/vi/example/default.jpg"}
        self.extractor.extract_info.return_value = self.info

    def test_uses_canonical_watch_url_and_no_download(self):
        for mode, value, expected in (
            ("system", "", None), ("direct", "", ""), ("manual", "https://127.0.0.1:9000", "https://127.0.0.1:9000"),
        ):
            with self.subTest(mode=mode):
                network.set_proxy(mode, value)
                info = self.client.get_video(self.entry)
                self.assertEqual((info.title, info.cover), (self.info["title"], self.info["thumbnail"]))
                track, = info.tracks
                self.assertEqual((track.provider, track.bvid, track.cid, track.page, track.duration),
                                 ("youtube", VIDEO_ID, 0, 1, 123))
                options = self.factory.call_args.args[0]
                self.assertEqual(options["proxy"], expected)
                self.assertEqual(options["socket_timeout"], 9)
                self.assertIn("node", options["js_runtimes"])
                self.assertTrue(options["noplaylist"] and options["skip_download"])
                for option in ("cookiefile", "cookiesfrombrowser", "username", "password", "external_downloader"):
                    self.assertNotIn(option, options)
                self.extractor.extract_info.assert_called_with(WATCH_URL, download=False, ie_key="Youtube")
        self.assertEqual(self.client.get_tracks(self.entry)[0].title, "Video")

    def test_chrome_default_uses_only_youtube_cookies(self):
        jar = requests.cookies.cookiejar_from_dict({})
        jar.set("session", "mock", domain=".youtube.com", path="/")
        jar.set("other", "mock", domain="example.org", path="/")
        self.extractor.cookiejar = jar
        self.assertEqual(YouTubeClient().get_video(self.entry).title, "Video")
        self.assertEqual(self.factory.call_args.args[0]["cookiesfrombrowser"], ("chrome",))
        self.assertEqual([cookie.domain for cookie in jar], [".youtube.com"])
        self.extractor.extract_info.assert_called_once_with(WATCH_URL, download=False, ie_key="Youtube")

    def test_locked_chrome_profile_retries_anonymously(self):
        from unittest.mock import PropertyMock
        from yt_dlp.cookies import CookieLoadError

        with patch.object(type(self.extractor), "cookiejar", new_callable=PropertyMock, create=True,
                          side_effect=CookieLoadError("mock read error")):
            self.assertEqual(YouTubeClient().get_video(self.entry).title, "Video")
        original, retry = [call.args[0] for call in self.factory.call_args_list]
        self.assertEqual(original["cookiesfrombrowser"], ("chrome",))
        self.assertNotIn("cookiesfrombrowser", retry)
        self.extractor.extract_info.assert_called_once()

    def test_manual_cookie_file_takes_precedence_and_only_passes_youtube_cookies(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "YouTube cookies.txt"
            text = ("# Netscape HTTP Cookie File\n"
                    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tsession\tlocal-test\n"
                    ".example.org\tTRUE\t/\tTRUE\t0\tother\tprivate-test\n")
            path.write_text(text, encoding="utf-8")
            original = path.read_bytes()
            result = YouTubeClient(use_chrome=True, cookie_file=str(path)).get_video(self.entry)
            self.assertEqual(result.title, "Video")
            self.factory.assert_called_once()
            options = self.factory.call_args.args[0]
            self.assertNotIn("cookiesfrombrowser", options)
            cookies = options["cookiefile"].getvalue()
            self.assertIn("#HttpOnly_.youtube.com", cookies)
            self.assertNotIn("private-test", cookies)
            options["cookiefile"].write("mock updated cookie")
            self.assertEqual(path.read_bytes(), original)

    def test_real_ytdlp_loads_manual_cookie_stream_without_rewriting_file(self):
        seen = []

        def fake_extract(extractor, *_args, **_kwargs):
            seen.extend((cookie.domain, cookie.name) for cookie in extractor.cookiejar)
            return self.info

        with TemporaryDirectory() as directory:
            path = Path(directory) / "cookies.txt"
            original = (b"# Netscape HTTP Cookie File\r\n"
                        b"#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tsession\tmock\r\n")
            path.write_bytes(original)
            with patch.object(yt_dlp, "YoutubeDL", self.real_factory), \
                 patch.object(self.real_factory, "extract_info", fake_extract):
                YouTubeClient(cookie_file=str(path)).get_video(self.entry)
            self.assertEqual(seen, [(".youtube.com", "session")])
            self.assertEqual(path.read_bytes(), original)

    def test_missing_and_invalid_manual_cookie_files_do_not_try_chrome_or_network(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "cookies.txt"
            with self.assertRaisesRegex(YouTubeError, "请在设置中选择"):
                YouTubeClient(cookie_file="").get_video(self.entry)
            with self.assertRaisesRegex(YouTubeError, "无法读取 YouTube Cookie 文件"):
                YouTubeClient(cookie_file=str(file)).get_video(self.entry)
            file.write_text('{"not": "Netscape"}', encoding="utf-8")
            with self.assertRaisesRegex(YouTubeError, "Netscape"):
                YouTubeClient(cookie_file=str(file)).get_video(self.entry)
            file.write_text("# Netscape HTTP Cookie File\n.youtube.com.example.org\tTRUE\t/\tTRUE\t0\tsession\tmock\n",
                            encoding="utf-8")
            with self.assertRaisesRegex(YouTubeError, "没有 YouTube 条目"):
                YouTubeClient(cookie_file=str(file)).get_video(self.entry)
            self.factory.assert_not_called()

    def test_rejects_mutated_source_and_mismatched_or_playlist_results(self):
        self.entry.bvid = "https://youtu.be/" + VIDEO_ID
        with self.assertRaisesRegex(YouTubeError, "视频 ID 无效"):
            self.client.get_video(self.entry)
        self.factory.assert_not_called()
        self.entry.bvid = VIDEO_ID
        for invalid in (None, {"id": "aDifferent_"}, {"id": VIDEO_ID, "_type": "playlist"}):
            with self.subTest(invalid=invalid):
                self.extractor.extract_info.return_value = invalid
                with self.assertRaisesRegex(YouTubeError, "无法读取 YouTube 视频"):
                    self.client.get_video(self.entry)

    def test_prefers_best_m4a_and_preserves_safe_format_headers(self):
        audio = {"vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "protocol": "https"}
        origin = "https://example.googlevideo.com/"
        self.info.update({"http_headers": {"User-Agent": "base", "Referer": WATCH_URL, "Cookie": "secret"},
                          "formats": [
            {**audio, "url": origin + "webm", "ext": "webm", "acodec": "opus", "abr": 200},
            {**audio, "url": origin + "lowest", "abr": 64},
            {**audio, "url": origin + "best?signed=1", "abr": 140,
             "http_headers": {"user-agent": "preferred", "Host": "invalid", "Range": "bytes=0-1"}},
            {**audio, "url": origin + "manifest.m3u8", "abr": 300},
            {**audio, "url": origin + "hls", "protocol": "m3u8_native", "abr": 300},
            {**audio, "url": origin + "fragmented", "fragments": [{"path": "path"}], "abr": 300},
            {**audio, "url": origin + "protected", "has_drm": True, "abr": 300},
            {**audio, "url": origin + "video", "vcodec": "av1", "abr": 300},
            {**audio, "url": "http://example.googlevideo.com/insecure", "abr": 300},
        ]})
        first = self.client.get_stream(self.track)
        self.assertIsInstance(first, str)
        self.assertEqual(first, origin + "best?signed=1")
        self.assertEqual(first.http_headers, {"user-agent": "preferred", "referer": WATCH_URL})
        self.info["formats"][2]["url"] = origin + "best?signed=2"
        second = self.client.get_stream(self.track)
        self.assertEqual(second, origin + "best?signed=2")
        self.assertEqual(self.extractor.extract_info.call_count, 2)
        self.info["formats"] = self.info["formats"][:1]
        self.assertEqual(self.client.get_stream(self.track), origin + "webm")
        self.info["formats"].clear()
        with self.assertRaisesRegex(YouTubeError, "没有找到可直接播放"):
            self.client.get_stream(self.track)


if __name__ == "__main__":
    unittest.main()

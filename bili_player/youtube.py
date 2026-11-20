"""Read public YouTube metadata and direct audio URLs through yt-dlp."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from . import network
from .api import VideoInfo
from .models import LibraryEntry, Track

try:
    import yt_dlp
    from yt_dlp.cookies import CookieLoadError
    from yt_dlp.utils import DownloadError
except ImportError:
    yt_dlp = None

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_WATCH_URL = "https://www.youtube.com/watch?v="
_INVALID_VIDEO = "YouTube 视频 ID 无效"
_UNAVAILABLE_VIDEO = "无法读取 YouTube 视频，请检查网络或视频是否公开"
_UNAVAILABLE_AUDIO = "没有找到可直接播放的 YouTube 音频"
_MEDIA_HEADERS = {"accept", "accept-language", "origin", "referer", "sec-fetch-mode", "user-agent"}


class YouTubeError(Exception):
    pass


class _StreamURL(str):
    def __new__(cls, url: str, http_headers: dict[str, str]):
        value = super().__new__(cls, url)
        value.http_headers = http_headers
        return value


def _https_url(value: object) -> str:
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and "@" not in parsed.netloc:
            return value
    except ValueError:
        pass
    return ""


def _nonnegative_number(value: object) -> float:
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value)
    return 0.0


def _media_headers(*sources: object) -> dict[str, str]:
    headers = {}
    for source in sources:
        if isinstance(source, Mapping):
            for key, value in source.items():
                if (isinstance(key, str) and key.lower() in _MEDIA_HEADERS
                        and isinstance(value, str) and "\r" not in value and "\n" not in value):
                    headers[key.lower()] = value
    return headers


class YouTubeClient:
    def __init__(self, timeout: float = 15, *, use_chrome: bool = True):
        self.timeout = timeout
        self.use_chrome = use_chrome

    def _extract(self, video_id: str) -> dict:
        if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
            raise YouTubeError(_INVALID_VIDEO)
        if yt_dlp is None:
            raise YouTubeError("缺少 YouTube 播放依赖 yt-dlp")
        options = {
            "allowed_extractors": ["youtube"],
            "cachedir": False,
            "extractor_args": {"youtube": {"skip": ["hls", "dash"]}},
            "js_runtimes": {"deno": {}, "node": {}},
            "noplaylist": True,
            "no_warnings": True,
            "proxy": network.extractor_proxy(),
            "quiet": True,
            "skip_download": True,
            "socket_timeout": self.timeout,
        }
        chrome_unavailable = False
        for use_chrome in ((True, False) if self.use_chrome else (False,)):
            try:
                settings = {**options, "cookiesfrombrowser": ("chrome",)} if use_chrome else options
                with yt_dlp.YoutubeDL(settings) as extractor:
                    if use_chrome:
                        try:
                            cookies = extractor.cookiejar
                            for cookie in list(cookies):
                                domain = cookie.domain.lstrip(".").lower()
                                if domain != "youtube.com" and not domain.endswith(".youtube.com"):
                                    cookies.clear(cookie.domain, cookie.path, cookie.name)
                            if not cookies:
                                chrome_unavailable = True
                                continue
                        except (CookieLoadError, DownloadError, OSError):
                            chrome_unavailable = True
                            continue
                    info = extractor.extract_info(_WATCH_URL + video_id, download=False, ie_key="Youtube")
            except (DownloadError, OSError) as exc:
                message = "无法使用 Chrome 的 YouTube 会话，匿名访问也失败了" if chrome_unavailable else _UNAVAILABLE_VIDEO
                raise YouTubeError(message) from exc
            if (not isinstance(info, dict) or info.get("id") != video_id
                    or info.get("_type", "video") != "video"):
                raise YouTubeError(_UNAVAILABLE_VIDEO)
            return info

    def get_video(self, entry: LibraryEntry) -> VideoInfo:
        info = self._extract(entry.bvid)
        title = info.get("title")
        if not isinstance(title, str) or not title.strip():
            title = entry.title or entry.bvid
        cover = _https_url(info.get("thumbnail"))
        track = Track(title=title, bvid=entry.bvid, cid=0, page=1, provider="youtube",
                      duration=int(_nonnegative_number(info.get("duration"))))
        return VideoInfo(title, cover, [track])

    def get_tracks(self, entry: LibraryEntry) -> list[Track]:
        return self.get_video(entry).tracks

    def get_stream(self, track: Track) -> str:
        # Signed URLs are refreshed for each playback rather than serialized.
        info = self._extract(track.bvid)
        formats = info.get("formats")
        candidates = []
        for item in formats if isinstance(formats, list) else []:
            if (not isinstance(item, dict) or item.get("protocol") != "https"
                    or item.get("vcodec") != "none" or not isinstance(item.get("acodec"), str)
                    or item["acodec"] in ("", "none") or item.get("has_drm")
                    or item.get("manifest_url") or item.get("fragments")):
                continue
            url = _https_url(item.get("url"))
            if not url or urlsplit(url).path.lower().endswith((".m3u8", ".mpd")):
                continue
            priority = (item.get("ext") == "m4a", _nonnegative_number(item.get("abr") or item.get("tbr")))
            candidates.append((priority, url, item))
        if not candidates:
            raise YouTubeError(_UNAVAILABLE_AUDIO)
        _, url, selected = max(candidates, key=lambda candidate: candidate[0])
        return _StreamURL(url, _media_headers(info.get("http_headers"), selected.get("http_headers")))

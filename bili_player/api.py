from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import requests

from .models import LibraryEntry, Track


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}
_VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
_PLAY_URL = "https://api.bilibili.com/x/player/playurl"


class BilibiliError(Exception):
    pass


@dataclass
class VideoInfo:
    title: str
    cover: str
    tracks: list[Track]


class BilibiliClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 15):
        self.session = session if session is not None else requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
        except requests.Timeout as exc:
            raise BilibiliError("请求哔哩哔哩超时，请稍后重试") from exc
        except requests.RequestException as exc:
            raise BilibiliError(f"连接哔哩哔哩失败：{exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise BilibiliError("哔哩哔哩返回了无效的 JSON 数据") from exc
        if not isinstance(payload, dict) or "code" not in payload:
            raise BilibiliError("哔哩哔哩响应格式不正确")
        code = payload["code"]
        if type(code) is not int:
            raise BilibiliError("哔哩哔哩响应状态码格式不正确")
        if code != 0:
            explanations = {
                -400: "请求参数错误", -403: "访问被拒绝", -404: "视频不存在或已删除",
                -412: "请求被暂时限制，请稍后重试", -101: "该内容需要登录",
                62002: "视频暂不可见", 62012: "视频仅作者可见",
            }
            detail = explanations.get(code, "接口请求失败")
            message = payload.get("message") or payload.get("msg")
            if message and str(message) != "0":
                detail += f"：{message}"
            raise BilibiliError(f"哔哩哔哩错误 {code}：{detail}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BilibiliError("哔哩哔哩返回的数据为空或格式不正确")
        return data

    def get_tracks(self, entry: LibraryEntry) -> list[Track]:
        return self.get_video(entry).tracks

    def get_video(self, entry: LibraryEntry) -> VideoInfo:
        data = self._get(_VIEW_URL, {"bvid": entry.bvid})
        pages = data.get("pages")
        if not isinstance(pages, list) or not pages:
            raise BilibiliError("视频没有可播放的分 P")
        selected = pages
        if entry.kind == "single":
            selected = [page for page in pages if isinstance(page, dict) and page.get("page") == entry.page]
            if not selected:
                raise BilibiliError(f"视频不存在第 {entry.page} P")
        owner = data.get("owner") or {}
        artist = owner.get("name", "") if isinstance(owner, dict) else ""
        title = data.get("title") or entry.title or entry.bvid
        cover = data.get("pic") or ""
        if not isinstance(title, str) or not isinstance(cover, str):
            raise BilibiliError("视频标题或封面格式不正确")
        if cover.startswith("//"):
            cover = "https:" + cover
        tracks = []
        try:
            for page in selected:
                if not isinstance(page, dict):
                    raise ValueError("分 P 信息不是对象")
                tracks.append(Track(
                    title=page.get("part") or title,
                    bvid=entry.bvid,
                    cid=page["cid"],
                    page=page["page"],
                    artist=artist,
                    duration=page.get("duration", 0),
                ))
        except (KeyError, TypeError, ValueError) as exc:
            raise BilibiliError(f"视频分 P 数据不完整：{exc}") from exc
        return VideoInfo(title=title, cover=cover, tracks=tracks)

    def get_stream(self, track: Track) -> str:
        data = self._get(_PLAY_URL, {
            "bvid": track.bvid, "cid": track.cid, "fnval": 16,
            "fnver": 0, "fourk": 0, "qn": 64,
        })
        dash = data.get("dash")
        audio = dash.get("audio") if isinstance(dash, dict) else None
        if isinstance(audio, list):
            candidates = [item for item in audio if isinstance(item, dict)]
            # Prefer the highest available audio bandwidth, independent of API ordering.
            candidates.sort(key=self._bandwidth, reverse=True)
            for item in candidates:
                url = self._stream_url(item)
                if url:
                    return url
        durl = data.get("durl")
        if isinstance(durl, list):
            for item in durl:
                if isinstance(item, dict):
                    url = self._stream_url(item)
                    if url:
                        return url
        raise BilibiliError("没有找到可播放的音频地址，视频可能受地区或权限限制")

    @staticmethod
    def _bandwidth(item: dict[str, Any]) -> float:
        try:
            value = float(item.get("bandwidth", 0))
            return value if math.isfinite(value) else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _stream_url(item: dict[str, Any]) -> str | None:
        values = [item.get("baseUrl"), item.get("base_url"), item.get("url")]
        for key in ("backupUrl", "backup_url"):
            backup = item.get(key)
            if isinstance(backup, list):
                values.extend(backup)
        for value in values:
            if isinstance(value, str):
                if value.startswith("//"):
                    return "https:" + value
                if value.startswith(("https://", "http://")):
                    return value
        return None

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse
from uuid import uuid4


DEFAULT_VOLUME = 70
MINI_COVER_STYLES = ("classic", "record")
DEFAULT_MINI_COVER_STYLE = MINI_COVER_STYLES[0]
_BVID = re.compile(r"BV[0-9A-Za-z]{10}", re.IGNORECASE)
_DEFAULT_SETTINGS = {"theme": "light", "language": "zh_CN"}


def normalize_bvid(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请输入 BV 号或哔哩哔哩视频链接")
    value = text.strip()
    if _BVID.fullmatch(value):
        return "BV" + value[2:]
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not (
        host == "bilibili.com" or host.endswith(".bilibili.com")
    ):
        raise ValueError("链接必须是哔哩哔哩视频链接，或直接输入 BV 号")
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", unquote(parsed.path), re.IGNORECASE)
    if not match:
        raise ValueError("视频链接中没有有效的 BV 号")
    return "BV" + match.group(1)[2:]


def _integer(value: object, minimum: int, label: str) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label}必须是大于等于 {minimum} 的整数")


def _text(value: object, label: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{label}必须是{'非空' if nonempty else ''}字符串")


@dataclass
class Track:
    title: str
    bvid: str
    cid: int
    page: int
    artist: str = ""
    duration: int = 0
    id: str = field(default_factory=lambda: str(uuid4()))
    date: str = ""
    song_id: str = ""

    def __post_init__(self) -> None:
        _text(self.title, "标题")
        _text(self.id, "曲目 ID", nonempty=True)
        _text(self.song_id, "歌曲 ID")
        _text(self.artist, "作者")
        _text(self.date, "日期")
        self.bvid = normalize_bvid(self.bvid)
        _integer(self.cid, 1, "CID")
        _integer(self.page, 1, "分 P 序号")
        _integer(self.duration, 0, "时长")


@dataclass
class LibraryEntry:
    title: str
    bvid: str = ""
    kind: Literal["single", "album"] = "single"
    page: int = 1
    id: str = field(default_factory=lambda: str(uuid4()))
    tracks: list[Track] = field(default_factory=list)
    cover: str = ""
    resolved: bool = False
    artist: str = ""
    date: str = ""

    def __post_init__(self) -> None:
        _text(self.title, "标题")
        _text(self.id, "条目 ID", nonempty=True)
        _text(self.artist, "作者")
        _text(self.date, "日期")
        if self.kind not in ("single", "album"):
            raise ValueError("条目类型必须是 single 或 album")
        if self.kind != "album" or self.bvid != "":
            self.bvid = normalize_bvid(self.bvid)
        _integer(self.page, 1, "分 P 序号")
        _text(self.cover, "封面")
        if type(self.resolved) is not bool:
            raise ValueError("专辑解析状态必须是布尔值")
        if not isinstance(self.tracks, list) or any(not isinstance(track, Track) for track in self.tracks):
            raise ValueError("专辑曲目必须是 Track 列表")
        for track in self.tracks:
            Track(**asdict(track))


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.settings = dict(_DEFAULT_SETTINGS)

    @staticmethod
    def _validate_settings(settings: object) -> dict:
        if not isinstance(settings, dict):
            raise ValueError("设置必须是对象")
        result = dict(_DEFAULT_SETTINGS)
        result.update(settings)
        if result["theme"] not in ("light", "dark"):
            raise ValueError("主题必须是 light 或 dark")
        if result["language"] not in ("zh_CN", "en_US"):
            raise ValueError("语言必须是 zh_CN 或 en_US")
        if "card_scale" in result:
            value = result["card_scale"]
            if type(value) not in (int, float) or not 0.7 <= value <= 1.6:
                raise ValueError("卡片缩放必须在 70% 到 160% 之间")
        if "mini_cover_style" in result and result["mini_cover_style"] not in MINI_COVER_STYLES:
            raise ValueError("不支持的迷你播放器样式")
        if "audio_output_id" in result:
            _text(result["audio_output_id"], "音频输出设备 ID")
        if "play_mode" in result:
            value = result["play_mode"]
            if type(value) is not int or not 0 <= value <= 3:
                raise ValueError("播放模式必须是 0 到 3 之间的整数")
        return result

    @staticmethod
    def _entry_from_dict(item: object) -> LibraryEntry:
        if not isinstance(item, dict):
            raise ValueError("音乐库条目必须是对象")
        fields = dict(item)
        nested = fields.get("tracks", [])
        if not isinstance(nested, list) or any(not isinstance(track, dict) for track in nested):
            raise ValueError("专辑曲目必须是对象列表")
        fields["tracks"] = [Track(**track) for track in nested]
        return LibraryEntry(**fields)

    def _read(self) -> tuple[list[LibraryEntry], list[Track], int, dict, int, bytes]:
        from .catalog import decode_library

        try:
            original = self.path.read_bytes()
        except FileNotFoundError:
            return [], [], DEFAULT_VOLUME, dict(_DEFAULT_SETTINGS), 4, b""
        try:
            data = json.loads(original.decode("utf-8"))
            if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] not in (1, 2, 3, 4):
                raise ValueError("不支持的音乐库格式")
            if not isinstance(data.get("entries"), list) or not isinstance(data.get("tracks"), list):
                raise ValueError("音乐库列表格式不正确")
            volume = data["volume"]
            self._validate_volume(volume)
            entries = decode_library(data)
            if any(not isinstance(item, dict) for item in data["tracks"]):
                raise ValueError("播放队列必须是对象列表")
            tracks = [Track(**item) for item in data["tracks"]]
            if data["version"] < 3:
                self._link_queue(entries, tracks)
            settings = self._validate_settings(data.get("settings", {}) if data["version"] >= 2 else {})
            return entries, tracks, volume, settings, data["version"], original
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ValueError(f"音乐库文件损坏，原文件已保留：{exc}") from exc

    @staticmethod
    def _link_queue(entries: list[LibraryEntry], tracks: list[Track]) -> None:
        """Fill missing legacy queue references without changing explicit IDs."""
        from .catalog import find_song

        songs = {(entry.bvid, entry.page): entry.id for entry in entries if entry.kind == "single"}
        for track in tracks:
            if track.song_id:
                continue
            song = find_song(entries, track.id)
            identifier = song.id if song is not None else songs.get((track.bvid, track.page))
            if identifier is not None:
                track.song_id = identifier

    def load(self) -> tuple[list[LibraryEntry], list[Track], int]:
        entries, tracks, volume, settings, _, _ = self._read()
        self.settings = settings
        return entries, tracks, volume

    def save(
        self, entries: list[LibraryEntry], tracks: list[Track], volume: int,
        settings: dict | None = None,
    ) -> None:
        from .catalog import encode_library

        self._validate_volume(volume)
        selected_settings = self._validate_settings(self.settings if settings is None else settings)
        # Validate the original before replacing it, even when load was never called.
        _, _, _, _, version, original = self._read()
        if not isinstance(entries, list) or any(not isinstance(entry, LibraryEntry) for entry in entries):
            raise ValueError("音乐库条目必须是 LibraryEntry 列表")
        if not isinstance(tracks, list) or any(not isinstance(track, Track) for track in tracks):
            raise ValueError("播放队列必须是 Track 列表")
        # Validate mutable dataclasses before serialization, including nested tracks.
        for entry in entries:
            entry.__post_init__()
        for track in tracks:
            Track(**asdict(track))
        data = encode_library(entries)
        data.update({
            "tracks": [asdict(track) for track in tracks],
            "volume": volume,
            "settings": selected_settings,
        })
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=self.path.name + ".", suffix=".tmp", delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if version == 1:
                self._backup_v1(original)
            elif version in (2, 3):
                self._backup_legacy(original, version)
            os.replace(temporary, self.path)
            temporary = None
            self.settings = selected_settings
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def _backup_v1(self, original: bytes) -> None:
        self._backup_legacy(original, 1)

    def _backup_legacy(self, original: bytes, version: int) -> None:
        backup = self.path.with_name(self.path.name + f".v{version}.bak")
        try:
            handle = backup.open("xb")
        except FileExistsError:
            return
        try:
            with handle:
                handle.write(original)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            backup.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_volume(volume: int) -> None:
        _integer(volume, 0, "音量")
        if volume > 100:
            raise ValueError("音量必须在 0 到 100 之间")

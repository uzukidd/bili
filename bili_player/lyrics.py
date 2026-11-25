"""Offline lyrics storage, line-synced display, and LRCLIB lookups."""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import tempfile
import threading
import time
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from email.utils import parsedate_to_datetime
from pathlib import Path

from requests import RequestException

from . import network


_LRCLIB_URL = "https://lrclib.net"
_USER_AGENT = "BILI v0.1.0 (https://github.com/uzukidd/bili)"
_MAX_RESULTS = 20
_MAX_SEARCH_BYTES = 256 * 1024
_MAX_LYRICS_BYTES = 1024 * 1024
_REQUEST_INTERVAL = 0.25
_STORE_LOCK = threading.Lock()

_TIMESTAMP = r"([0-9]{1,6}):([0-5]?[0-9])(?:[.:]([0-9]{1,3}))?"
_LINE_TIME = re.compile(rf"\[{_TIMESTAMP}\]\s*")
_WORD_TIME = re.compile(rf"<{_TIMESTAMP}>")
_METADATA = re.compile(r"\[([a-z]+|#):([^\]]*)\]", re.IGNORECASE)
_OFFSET = re.compile(r"[+-]?[0-9]{1,9}")


@dataclass(frozen=True)
class LyricsLine:
    text: str
    time_ms: int | None = None


@dataclass(frozen=True)
class LyricsDisplay:
    lines: tuple[LyricsLine, ...]
    synced: bool = False

    def line_index(self, position_ms: int) -> int:
        """Return the last cue at or before the position, or -1 if none."""
        if not self.synced:
            return -1
        return bisect_right(self.lines, position_ms, key=lambda line: line.time_ms) - 1


def _lines(text: str) -> LyricsDisplay:
    return LyricsDisplay(tuple(LyricsLine(line) for line in text.lstrip("\ufeff").strip().splitlines()))


def parse_lrc(text: str) -> LyricsDisplay:
    """Parse LRC cues. The final valid offset shifts positive values earlier."""
    offset_ms = 0
    cues: list[tuple[int, str]] = []
    untimed: list[str] = []
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        tag = _METADATA.fullmatch(line)
        if tag:
            if tag[1].lower() == "offset" and _OFFSET.fullmatch(tag[2].strip()):
                offset_ms = int(tag[2])
            continue
        timestamps: list[int] = []
        start = 0
        while match := _LINE_TIME.match(line, start):
            minutes, seconds, fraction = match.groups()
            timestamps.append(int(minutes) * 60_000 + int(seconds) * 1000
                              + int((fraction or "").ljust(3, "0")))
            start = match.end()
        if timestamps:
            lyric = _WORD_TIME.sub("", line[start:]).strip()
            cues.extend((timestamp, lyric) for timestamp in timestamps)
        else:
            untimed.append(raw)
    if any(lyric for _, lyric in cues):
        cues.sort(key=lambda cue: cue[0])
        return LyricsDisplay(tuple(LyricsLine(lyric, timestamp - offset_ms)
                                   for timestamp, lyric in cues), synced=True)
    return _lines("\n".join(untimed))


@dataclass(frozen=True)
class LyricsEntry:
    id: str
    title: str
    plain_lyrics: str = ""
    synced_lyrics: str = ""
    source: str = "local"
    artist: str = ""
    instrumental: bool = False

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) for value in (
            self.id, self.title, self.plain_lyrics, self.synced_lyrics, self.source, self.artist,
        )) or not self.id.strip() or not self.source.strip() or type(self.instrumental) is not bool:
            raise ValueError("Invalid lyrics entry")

    def display(self) -> LyricsDisplay:
        parsed = parse_lrc(self.synced_lyrics)
        if parsed.synced or not self.plain_lyrics.strip():
            return parsed
        return _lines(self.plain_lyrics)

    def preview(self, *, max_chars: int = 2000, max_lines: int = 12) -> str:
        """Return a plain-text excerpt bounded by both lines and characters."""
        if max_chars <= 0 or max_lines <= 0:
            return ""
        lines = self.display().lines
        preview = "\n".join(line.text for line in lines[:max_lines])
        if len(preview) > max_chars or len(lines) > max_lines:
            return preview[:max_chars - 1].rstrip() + "…"
        return preview


@dataclass(frozen=True)
class SongLyrics:
    entries: tuple[LyricsEntry, ...] = ()
    active_id: str | None = None

    @property
    def active(self) -> LyricsEntry | None:
        return next((entry for entry in self.entries if entry.id == self.active_id), None)

    @classmethod
    def from_dict(cls, value: object) -> SongLyrics:
        if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
            raise ValueError("Invalid lyrics for song")
        try:
            entries = tuple(LyricsEntry(**item) for item in value["entries"])
        except TypeError as exc:
            raise ValueError("Invalid lyrics entry") from exc
        active_id = value.get("active_id")
        ids = {entry.id for entry in entries}
        if len(ids) != len(entries) or (active_id is not None and (
            not isinstance(active_id, str) or active_id not in ids
        )):
            raise ValueError("Invalid lyrics entry IDs")
        return cls(entries, active_id)

    def merge(self, incoming: SongLyrics) -> SongLyrics:
        """Keep local choices and add imported versions without overwriting edits."""
        if not self.entries:
            return incoming
        entries = {entry.id: entry for entry in self.entries}
        reserved = {entry.id: entry for entry in incoming.entries}
        for entry in incoming.entries:
            current = entries.get(entry.id)
            if current is None:
                entries[entry.id] = entry
            elif current != entry:
                # The same archive always resolves an ID collision the same way.
                data = json.dumps(asdict(entry), sort_keys=True).encode("ascii")
                base = f"imported:{hashlib.sha256(data).hexdigest()}"
                candidate = base
                suffix = 0
                while True:
                    alternate = replace(entry, id=candidate)
                    if (entries.get(candidate, alternate) == alternate
                            and reserved.get(candidate, alternate) == alternate):
                        entries[candidate] = alternate
                        break
                    suffix += 1
                    candidate = f"{base}:{suffix}"
        return SongLyrics(tuple(entries.values()), self.active_id)


def _lyrics_songs(data: object) -> dict:
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] != 1 or not isinstance(data.get("songs"), dict)):
        raise ValueError("Unsupported lyrics file format")
    return data["songs"]


def _lyrics_payload(songs: dict) -> dict:
    return {"version": 1, "songs": songs}


def dumps_lyrics(songs: dict[str, SongLyrics]) -> str:
    return json.dumps(_lyrics_payload({key: asdict(song) for key, song in songs.items()}),
                      ensure_ascii=False, indent=2) + "\n"


def loads_lyrics(text: str, song_ids: set[str]) -> dict[str, SongLyrics]:
    def unique_fields(pairs):
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError("Duplicate lyrics field")
        return fields

    songs = _lyrics_songs(json.loads(text, object_pairs_hook=unique_fields))
    if songs.keys() - song_ids:
        raise ValueError("Unknown song ID in lyrics")
    decoded = {key: SongLyrics.from_dict(song) for key, song in songs.items()}
    for key, song in decoded.items():
        # JSON escapes can represent invalid Unicode even in a UTF-8 file.
        key.encode("utf-8")
        for entry in song.entries:
            for value in (entry.id, entry.title, entry.source, entry.artist,
                          entry.plain_lyrics, entry.synced_lyrics):
                value.encode("utf-8")
    return decoded


class LyricsStore:
    """A versioned sidecar file keyed by persistent song entity ID."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @staticmethod
    def _validate_key(song_key: str) -> None:
        if not isinstance(song_key, str) or not song_key.strip():
            raise ValueError("Lyrics require a song entity ID")

    def _read(self) -> dict:
        try:
            with self.path.open(encoding="utf-8") as source:
                data = json.load(source)
        except FileNotFoundError:
            return {}
        return _lyrics_songs(data)

    @staticmethod
    def _song(songs: dict, song_key: str) -> SongLyrics:
        if song_key not in songs:
            return SongLyrics()
        return SongLyrics.from_dict(songs[song_key])

    def _write(self, songs: dict, song_key: str, updated: SongLyrics) -> SongLyrics:
        if updated.entries:
            songs[song_key] = asdict(updated)
        else:
            songs.pop(song_key, None)
        self._write_songs(songs)
        return updated

    def _write_songs(self, songs: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=self.path.name + ".", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(_lyrics_payload(songs), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load_many(self, song_keys: Iterable[str]) -> dict[str, SongLyrics]:
        """Read one snapshot containing only the requested songs with lyrics."""
        with _STORE_LOCK:
            songs = self._read()
            snapshot = {}
            for key in song_keys:
                self._validate_key(key)
                song = self._song(songs, key)
                if song.entries:
                    snapshot[key] = song
            return snapshot

    def merge(self, incoming: dict[str, SongLyrics]) -> None:
        """Merge imported songs in a single atomic sidecar update."""
        if not incoming:
            return
        with _STORE_LOCK:
            songs = self._read()
            changed = False
            for key, imported in incoming.items():
                self._validate_key(key)
                song = self._song(songs, key)
                updated = song.merge(imported)
                if updated != song:
                    songs[key] = asdict(updated)
                    changed = True
            if changed:
                self._write_songs(songs)

    def load(self, song_key: str) -> SongLyrics:
        self._validate_key(song_key)
        with _STORE_LOCK:
            return self._song(self._read(), song_key)

    def save(self, song_key: str, entry: LyricsEntry, *, select: bool = True) -> SongLyrics:
        """Add or replace an entry, selecting it by default."""
        self._validate_key(song_key)
        if not isinstance(entry, LyricsEntry):
            raise TypeError("Expected a LyricsEntry")
        with _STORE_LOCK:
            songs = self._read()
            song = self._song(songs, song_key)
            entries = {item.id: item for item in song.entries}
            entries[entry.id] = entry
            updated = SongLyrics(tuple(entries.values()), entry.id if select else song.active_id)
            return self._write(songs, song_key, updated)

    def set_active(self, song_key: str, entry_id: str | None) -> SongLyrics:
        self._validate_key(song_key)
        with _STORE_LOCK:
            songs = self._read()
            song = self._song(songs, song_key)
            if entry_id is not None and all(item.id != entry_id for item in song.entries):
                raise ValueError("Unknown lyrics ID")
            if song.active_id == entry_id:
                return song
            return self._write(songs, song_key, SongLyrics(song.entries, entry_id))

    def remove(self, song_key: str, entry_id: str) -> SongLyrics:
        self._validate_key(song_key)
        with _STORE_LOCK:
            songs = self._read()
            song = self._song(songs, song_key)
            entries = tuple(item for item in song.entries if item.id != entry_id)
            if entries == song.entries:
                return song
            active_id = song.active_id
            if active_id == entry_id:
                active_id = entries[0].id if entries else None
            return self._write(songs, song_key, SongLyrics(entries, active_id))


class LyricsError(Exception):
    """LRCLIB could not return usable results."""


class LyricsCooldownError(LyricsError):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"LRCLIB is temporarily unavailable. Try again in {math.ceil(retry_after)} s.")


@dataclass(frozen=True)
class LRCLIBResult:
    id: int
    title: str
    artist: str
    album: str
    duration: float | None
    instrumental: bool | None
    has_synced_lyrics: bool | None
    has_plain_lyrics: bool | None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _duration(value: object) -> float | None:
    if type(value) not in (float, int):
        return None
    try:
        duration = float(value)
    except OverflowError:
        return None
    return duration if math.isfinite(duration) and duration >= 0 else None


def _retry_delay(status: int, header: str | None) -> float:
    if header:
        if re.fullmatch(r"[0-9]{1,16}", header.strip()):
            return float(header)
        try:
            return max(0.0, parsedate_to_datetime(header).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    return 60.0 if status == 429 else 1.0


class LRCLIBClient:
    """Synchronous client. Reuse one instance across off-UI-thread requests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self._next_request = 0.0

    def _request(self, path: str, *, params: dict | None = None, max_bytes: int) -> object:
        with self._lock:
            now = time.monotonic()
            if self._cooldown_until > now:
                raise LyricsCooldownError(self._cooldown_until - now)
            if self._next_request > now:
                time.sleep(self._next_request - now)
            try:
                with network.get(
                    _LRCLIB_URL + path, params=params, headers={"User-Agent": _USER_AGENT},
                    timeout=(4, 8), stream=True, allow_redirects=False,
                ) as response:
                    status = response.status_code
                    if status in (429, 503):
                        delay = _retry_delay(status, response.headers.get("Retry-After"))
                        delay += random.uniform(0.0, min(delay * 0.3, 3.0))
                        delay = max(delay, _REQUEST_INTERVAL)
                        self._cooldown_until = time.monotonic() + delay
                        raise LyricsCooldownError(delay)
                    if status == 404 and path.startswith("/api/get/"):
                        raise LyricsError("The selected LRCLIB lyrics could not be found.")
                    if status != 200:
                        raise LyricsError(f"LRCLIB returned HTTP {status}.")
                    data = bytearray()
                    for chunk in response.iter_content(chunk_size=16 * 1024):
                        if len(data) + len(chunk) > max_bytes:
                            raise LyricsError("LRCLIB response exceeded the size limit.")
                        data.extend(chunk)
                    return json.loads(data)
            except RequestException as exc:
                raise LyricsError("Could not connect to LRCLIB.") from exc
            except (ValueError, RecursionError) as exc:
                raise LyricsError("LRCLIB returned an invalid response.") from exc
            finally:
                self._next_request = time.monotonic() + _REQUEST_INTERVAL

    def search(
        self, query: str = "", *, track_name: str = "", artist_name: str = "",
        album_name: str = "", limit: int = _MAX_RESULTS,
    ) -> list[LRCLIBResult]:
        if type(limit) is not int or limit < 0:
            raise ValueError("Lyrics search limit must be a nonnegative integer")
        limit = min(limit, _MAX_RESULTS)
        params = {key: value.strip() for key, value in (
            ("q", query), ("track_name", track_name), ("artist_name", artist_name),
            ("album_name", album_name),
        ) if value.strip()}
        if not limit or not ("q" in params or "track_name" in params):
            return []
        data = self._request("/api/v2/search", params=params, max_bytes=_MAX_SEARCH_BYTES)
        if not isinstance(data, list):
            raise LyricsError("LRCLIB returned invalid search results.")
        results = []
        seen: set[int] = set()
        for row in data:
            if not isinstance(row, dict):
                continue
            entry_id = row.get("id")
            if type(entry_id) is not int or entry_id <= 0 or entry_id in seen:
                continue
            results.append(LRCLIBResult(
                entry_id, _text(row.get("trackName")) or f"#{entry_id}",
                _text(row.get("artistName")), _text(row.get("albumName")),
                _duration(row.get("duration")), _flag(row.get("instrumental")),
                _flag(row.get("hasSyncedLyrics")), _flag(row.get("hasPlainLyrics")),
            ))
            seen.add(entry_id)
            if len(results) >= limit:
                break
        return results

    def get(self, lyrics_id: int) -> LyricsEntry:
        if type(lyrics_id) is not int or lyrics_id <= 0:
            raise ValueError("LRCLIB ID must be a positive integer")
        row = self._request(f"/api/get/{lyrics_id}", max_bytes=_MAX_LYRICS_BYTES)
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] != lyrics_id:
            raise LyricsError("LRCLIB returned an invalid lyrics record.")
        plain = _text(row.get("plainLyrics"))
        synced = _text(row.get("syncedLyrics"))
        instrumental = row.get("instrumental") is True
        if not instrumental and not (plain.strip() or synced.strip()):
            raise LyricsError("The selected LRCLIB record has no line-synced or plain lyrics.")
        return LyricsEntry(
            id=f"lrclib:{lyrics_id}",
            title=_text(row.get("trackName")) or _text(row.get("name")) or f"#{lyrics_id}",
            plain_lyrics=plain, synced_lyrics=synced, source="lrclib",
            artist=_text(row.get("artistName")), instrumental=instrumental,
        )

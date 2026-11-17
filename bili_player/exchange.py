from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .models import LibraryEntry, Track


_FORMAT = "bili-music-library"
_VERSION = 2
_MAX_FILE_BYTES = 16 * 1024 * 1024


def _cover_url(value: str) -> str:
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    if any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.netloc
            or not parsed.hostname
            or parsed.hostname.strip(".") == ""
            or "%" in parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        # Accessing port validates malformed and out-of-range ports without I/O.
        parsed.port
    except ValueError:
        return ""
    return value


def _entry_from_dict(item: object) -> LibraryEntry:
    if not isinstance(item, dict):
        raise ValueError("音乐库条目必须是对象")
    fields = dict(item)
    nested = fields.get("tracks", [])
    if not isinstance(nested, list) or any(not isinstance(track, dict) for track in nested):
        raise ValueError("专辑曲目必须是对象列表")
    try:
        fields["tracks"] = [Track(**track) for track in nested]
        entry = LibraryEntry(**fields)
    except TypeError as exc:
        raise ValueError("音乐库条目或曲目的字段不正确，请检查必填字段和额外字段") from exc
    entry.cover = _cover_url(entry.cover)
    return entry


def dumps_library(entries: list[LibraryEntry]) -> str:
    """Export a detached graph with URL covers / 导出独立图结构及网址封面。"""
    from .catalog import encode_library, normalize_library

    if not isinstance(entries, list) or any(not isinstance(entry, LibraryEntry) for entry in entries):
        raise ValueError("音乐库条目必须是 LibraryEntry 列表")
    # Normalization repairs legacy graphs in place, so it must never see the
    # caller's objects. Validate before normalizing to reject mutated models.
    detached = deepcopy(entries)
    for entry in detached:
        if not isinstance(entry.tracks, list) or any(not isinstance(track, Track) for track in entry.tracks):
            raise ValueError("专辑曲目必须是 Track 列表")
        _entry_from_dict(asdict(entry))
        entry.cover = _cover_url(entry.cover)
    normalize_library(detached)
    encoded = encode_library(detached)
    payload = {"format": _FORMAT, "version": _VERSION, "entries": encoded["entries"]}
    if "songs" in encoded:
        payload["songs"] = encoded["songs"]
    try:
        result = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        result.encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("音乐库包含无法导出的文本或数据") from exc
    return result


def loads_library(text: str) -> list[LibraryEntry]:
    """Validate the graph, then renew IDs / 完整验证图结构后重新生成 ID。"""
    from .catalog import decode_library, normalize_library

    if not isinstance(text, str):
        raise ValueError("音乐库 JSON 必须是字符串")
    try:
        payload = json.loads(text.removeprefix("\ufeff"))
    except (ValueError, RecursionError) as exc:
        raise ValueError("音乐库 JSON 格式不正确") from exc
    if not isinstance(payload, dict):
        raise ValueError("音乐库文件必须是 JSON 对象")
    version = payload.get("version")
    if type(version) is not int:
        raise ValueError("音乐库版本必须是整数")
    if "format" in payload:
        if payload["format"] != _FORMAT:
            raise ValueError("不支持的音乐库格式")
        if version not in (1, _VERSION):
            raise ValueError("不支持的音乐库版本")
        # Portable v1 is nested; portable v2 uses the Store v3 graph schema.
        version = 3 if version == _VERSION else 1
    elif version not in (1, 2, 3):
        raise ValueError("不支持的音乐库版本")
    if not isinstance(payload.get("entries"), list):
        raise ValueError("音乐库 entries 必须是列表")
    library = {"version": version, "entries": payload["entries"]}
    if "songs" in payload:
        library["songs"] = payload["songs"]
    # Queue, volume and settings from Store snapshots are deliberately excluded.
    # Decode the entire graph before assigning IDs, including all references.
    try:
        entries = decode_library(library)
    except (TypeError, KeyError) as exc:
        raise ValueError("音乐库条目或曲目的字段不正确，请检查必填字段和额外字段") from exc
    for entry in entries:
        entry.id = str(uuid4())
        entry.cover = _cover_url(entry.cover)
        if entry.kind == "single":
            for track in entry.tracks:
                # Albums already hold this exact object after decoding. Updating
                # it renews every folder reference without copying the song.
                track.id = entry.id
                track.song_id = entry.id
        elif entry.bvid == "":
            entry.resolved = True
    normalize_library(entries)
    return entries


def read_library(path: Path) -> list[LibraryEntry]:
    """Read bounded UTF-8 JSON, accepting BOM / 限量读取 UTF-8，兼容 BOM。"""
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(_MAX_FILE_BYTES + 1)
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError("音乐库文件过大，最大支持 16 MiB")
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("音乐库文件必须使用 UTF-8 编码") from exc
    except OSError as exc:
        raise ValueError(f"无法读取音乐库文件：{exc}") from exc
    return loads_library(text)


def write_library(path: Path, entries: list[LibraryEntry]) -> None:
    """Atomically replace the selected file / 原子替换所选导出文件。"""
    payload = dumps_library(entries)
    path = Path(path)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法写入音乐库文件：{exc}") from exc
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)

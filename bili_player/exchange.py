from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .models import LibraryEntry, Track

if TYPE_CHECKING:
    from .covers import CoverCache


_FORMAT = "bili-music-library"
_VERSION = 3
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
    return _dump_library(entries)


def _dump_library(entries, *, entry_ids=None, cover_reference=_cover_url) -> str:
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
    normalize_library(detached)
    if entry_ids is not None:
        selected = set(entry_ids)
        included = selected | {track.song_id for entry in detached if entry.id in selected and entry.kind == "album"
                               for track in entry.tracks}
        detached = [entry for entry in detached if entry.id in included]
    for entry in detached:
        entry.cover = cover_reference(entry.cover)
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
    """Validate and load an ID-preserving graph / 验证并载入保留 ID 的图结构。"""
    return _load_library(text)


def _load_library(text, *, cover_reference=_cover_url, package=False) -> list[LibraryEntry]:
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
    if package and (payload.get("format") != _FORMAT or version != _VERSION):
        raise ValueError("不支持的音乐库包格式")
    if "format" in payload:
        if payload["format"] != _FORMAT:
            raise ValueError("不支持的音乐库格式")
        if version not in (1, 2, _VERSION):
            raise ValueError("不支持的音乐库版本")
        # Portable versions 1/2/3 use nested / Store v3 / Store v4 schemas.
        version = {1: 1, 2: 3, 3: 4}[version]
    elif version not in (1, 2, 3, 4):
        raise ValueError("不支持的音乐库版本")
    if not isinstance(payload.get("entries"), list):
        raise ValueError("音乐库 entries 必须是列表")
    library = {"version": version, "entries": payload["entries"]}
    if "songs" in payload:
        library["songs"] = payload["songs"]
    # Queue, volume and settings from Store snapshots are deliberately excluded.
    # Decode the entire graph before returning it, including all references.
    try:
        entries = decode_library(library)
    except (TypeError, KeyError) as exc:
        raise ValueError("音乐库条目或曲目的字段不正确，请检查必填字段和额外字段") from exc
    for entry in entries:
        entry.cover = cover_reference(entry.cover)
        if entry.kind == "album" and entry.bvid == "":
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


def write_library_package(path: Path, entries: list[LibraryEntry], entry_ids: list[str]) -> None:
    """Write the selection and its song references with local artwork in one ZIP."""
    from .covers import MAX_IMAGE_BYTES

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                references = {}
                members = set()

                def add_cover(source):
                    url = _cover_url(source)
                    if url or not source.strip():
                        return url
                    if source not in references:
                        try:
                            with Path(source).expanduser().open("rb") as image:
                                data = image.read(MAX_IMAGE_BYTES + 1)
                        except (OSError, ValueError) as exc:
                            raise ValueError(f"无法读取本地封面：{source}") from exc
                        if len(data) > MAX_IMAGE_BYTES:
                            raise ValueError("封面图片超过 8 MB")
                        member = f"covers/{hashlib.sha256(data).hexdigest()}.img"
                        if member not in members:
                            archive.writestr(member, data)
                            members.add(member)
                        references[source] = member
                    return references[source]

                payload = _dump_library(entries, entry_ids=entry_ids, cover_reference=add_cover).encode("utf-8")
                if len(payload) > _MAX_FILE_BYTES:
                    raise ValueError("音乐库文件过大，最大支持 16 MiB")
                archive.writestr("library.json", payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_library_package(path: Path, covers: CoverCache) -> list[LibraryEntry]:
    """Load the ZIP and cache referenced artwork without extracting archive paths."""
    from .covers import MAX_IMAGE_BYTES

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("音乐库包中存在重复文件")
            if "library.json" not in names:
                raise ValueError("音乐库包缺少曲目数据")

            def read_member(member, limit, error):
                if archive.getinfo(member).file_size > limit:
                    raise ValueError(error)
                with archive.open(member) as file:
                    data = file.read(limit + 1)
                if len(data) > limit:
                    raise ValueError(error)
                return data

            restored = {}

            def restore_cover(source):
                url = _cover_url(source)
                if url or not source:
                    return url
                match = re.fullmatch(r"covers/([0-9a-f]{64})\.img", source)
                if not match or source not in names:
                    raise ValueError("音乐库包的封面引用无效")
                if source not in restored:
                    data = read_member(source, MAX_IMAGE_BYTES, "封面图片超过 8 MB")
                    if hashlib.sha256(data).hexdigest() != match.group(1):
                        raise ValueError("音乐库包的封面校验失败")
                    restored[source] = str(covers.store_image(data).resolve())
                return restored[source]

            data = read_member("library.json", _MAX_FILE_BYTES, "音乐库文件过大，最大支持 16 MiB")
            return _load_library(data.decode("utf-8-sig"), cover_reference=restore_cover, package=True)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError) as exc:
        raise ValueError("无法读取音乐库 ZIP 包") from exc

from __future__ import annotations

from dataclasses import asdict, replace
from uuid import uuid4

from .models import LibraryEntry, Track


Source = tuple[str, int]


def _source(track: Track) -> Source:
    return track.bvid, track.page


def _single_source(entry: LibraryEntry) -> Source:
    requested = entry.bvid, entry.page
    if not entry.tracks or any(_source(track) == requested for track in entry.tracks):
        return requested
    # Old edited snapshots can retain a stale card page/source.
    return _source(entry.tracks[0])


def _validate_entries(entries: object) -> None:
    if not isinstance(entries, list) or any(not isinstance(entry, LibraryEntry) for entry in entries):
        raise ValueError("音乐库条目必须是 LibraryEntry 列表")
    for entry in entries:
        entry.__post_init__()


def _normalize_legacy(entries: list[LibraryEntry]) -> list[LibraryEntry]:
    """Migrate pre-entity snapshots using legacy source matching.

    使用旧版来源匹配规则迁移尚未使用实体 ID 的快照。
    """
    _validate_entries(entries)
    singles: dict[Source, list[LibraryEntry]] = {}
    tracks: dict[Source, list[tuple[LibraryEntry, Track]]] = {}
    for entry in entries:
        if entry.kind == "single":
            singles.setdefault(_single_source(entry), []).append(entry)
        for track in entry.tracks:
            tracks.setdefault(_source(track), []).append((entry, track))

    canonical: dict[Source, LibraryEntry] = {}
    appended: list[LibraryEntry] = []
    used_ids = {entry.id for entry in entries}
    for source in dict.fromkeys([*singles, *tracks]):
        owners = singles.get(source, [])
        copies = tracks.get(source, [])
        # An explicit single wins even when its album appeared earlier.
        ordered = [(owner, track) for owner in owners for track in owner.tracks
                   if _source(track) == source]
        ordered.extend((owner, track) for owner, track in copies if owner.kind == "album")
        if not ordered:
            ordered = copies
        track = ordered[0][1] if ordered else None
        if owners:
            song = owners[0]
        else:
            assert track is not None
            identifier = track.id
            while identifier in used_ids:
                identifier = str(uuid4())
            used_ids.add(identifier)
            song = LibraryEntry(track.title, source[0], page=source[1], id=identifier,
                                artist=track.artist, date=track.date)
            appended.append(song)

        aliases = set(getattr(song, "_song_aliases", ()))
        for owner in owners:
            aliases.add(owner.id)
            aliases.update(getattr(owner, "_song_aliases", ()))
        for _, candidate in copies:
            aliases.add(candidate.id)
            if candidate.song_id:
                aliases.add(candidate.song_id)
        song._song_aliases = aliases

        previous = getattr(song, "_song_metadata", {})
        previous_track = getattr(song, "_song_track", None)
        metadata = {}
        for name in ("title", "artist", "date"):
            values = []
            for owner in owners:
                values.append(getattr(owner, name))
                values.extend(getattr(candidate, name) for candidate in owner.tracks
                              if _source(candidate) == source)
            values.extend(getattr(candidate, name) for _, candidate in ordered)
            value = next((value for value in values if value.strip()), "")
            if name in previous:
                if getattr(song, name) != previous[name]:
                    value = getattr(song, name)
                elif track is not None and track is previous_track and getattr(track, name) != previous[name]:
                    value = getattr(track, name)
            setattr(song, name, value)
            if track is not None:
                setattr(track, name, value)
            metadata[name] = value
        song._song_metadata = metadata
        song._song_track = track
        if not song.cover:
            song.cover = next((owner.cover for owner in [*owners, *(item[0] for item in copies)]
                               if owner.cover), "")
        song.bvid, song.page = source
        if track is not None:
            if not track.duration:
                track.duration = next((candidate.duration for _, candidate in ordered
                                       if candidate.duration), 0)
            track.id = song.id
            track.song_id = song.id
            song.tracks[:] = [track]
            song.resolved = True
        else:
            song.tracks[:] = []
            song.resolved = False
        canonical[source] = song

    retained = []
    seen_ids: set[str] = set()
    seen_objects: set[int] = set()
    for entry in [*entries, *appended]:
        if id(entry) in seen_objects:
            continue
        seen_objects.add(id(entry))
        if entry.kind == "single" and canonical.get(_single_source(entry)) is not entry:
            continue
        if entry.id in seen_ids:
            entry.id = str(uuid4())
            if entry.kind == "single" and entry.tracks:
                entry.tracks[0].id = entry.id
                entry.tracks[0].song_id = entry.id
        seen_ids.add(entry.id)
        if entry.kind == "album":
            members = []
            seen_sources: set[Source] = set()
            for old_track in entry.tracks:
                source = _source(old_track)
                if source not in seen_sources:
                    members.append(canonical[source].tracks[0])
                    seen_sources.add(source)
            entry.tracks[:] = members
        retained.append(entry)
    entries[:] = retained
    return entries


def normalize_library(entries: list[LibraryEntry]) -> list[LibraryEntry]:
    """Normalize an ID graph in place; equal sources remain independent songs.

    按 ID 就地规范化；来源相同的歌曲仍是独立实体。
    """
    _validate_entries(entries)
    retained: list[LibraryEntry] = []
    by_id: dict[str, LibraryEntry] = {}
    for entry in entries:
        previous = by_id.get(entry.id)
        if previous is not None:
            if previous.kind != entry.kind:
                raise ValueError("单曲和专辑不能使用同一个 ID")
            continue
        by_id[entry.id] = entry
        retained.append(entry)
    songs = {entry.id: entry for entry in retained if entry.kind == "single"}
    # Snapshot reference identities before assigning canonical IDs to owner tracks.
    aliases: dict[str, str] = {}
    objects: dict[int, str] = {}
    candidates: dict[str, list[Track]] = {identifier: [] for identifier in songs}
    owner_objects: set[int] = set()
    for identifier, song in songs.items():
        if len(song.tracks) > 1:
            raise ValueError("单曲最多只能包含一首歌曲")
        if song.tracks and id(song.tracks[0]) in owner_objects:
            song.tracks[0] = replace(song.tracks[0])
        for track in song.tracks:
            owner_objects.add(id(track))
            if not track.song_id or track.song_id == identifier:
                aliases.setdefault(track.id, identifier)
                objects.setdefault(id(track), identifier)
            candidates[identifier].append(track)
    memberships: list[tuple[LibraryEntry, list[str]]] = []
    for folder in retained:
        if folder.kind != "album":
            continue
        members: list[str] = []
        for track in folder.tracks:
            identifier = (track.song_id or objects.get(id(track))
                          or (track.id if track.id in songs else aliases.get(track.id, track.id)))
            if identifier in by_id and by_id[identifier].kind != "single":
                raise ValueError("专辑曲目引用了专辑 ID")
            if identifier not in songs:
                song = LibraryEntry(track.title, track.bvid, page=track.page, id=identifier,
                                    artist=track.artist, date=track.date, cover=folder.cover)
                songs[identifier] = by_id[identifier] = song
                candidates[identifier] = []
            candidates[identifier].append(track)
            if identifier not in members:
                members.append(identifier)
        memberships.append((folder, members))
    used_tracks: set[int] = set()
    for identifier, song in songs.items():
        options = candidates[identifier]
        song._song_aliases = set(getattr(song, "_song_aliases", ())) | {item.id for item in options}
        track = options[0] if options else None
        if track is not None and id(track) in used_tracks:
            track = replace(track)
        if track is not None:
            used_tracks.add(id(track))
        previous = getattr(song, "_song_metadata", {})
        previous_track = getattr(song, "_song_track", None)
        metadata = {}
        for name in ("title", "artist", "date"):
            value = next((value for value in [getattr(song, name),
                         *(getattr(candidate, name) for candidate in options)] if value.strip()), "")
            if name in previous:
                if getattr(song, name) != previous[name]:
                    value = getattr(song, name)
                elif track is not None and track is previous_track and getattr(track, name) != previous[name]:
                    value = getattr(track, name)
            setattr(song, name, value)
            if track is not None:
                setattr(track, name, value)
            metadata[name] = value
        song._song_metadata = metadata
        song._song_track = track
        if track is not None:
            if not track.duration:
                track.duration = next((candidate.duration for candidate in options if candidate.duration), 0)
            # The owner ID is authoritative even for freshly resolved API tracks.
            track.id = track.song_id = identifier
            song.bvid, song.page = track.bvid, track.page
            song.tracks[:] = [track]
            song.resolved = True
        else:
            song.tracks[:] = []
            song.resolved = False
    for folder, members in memberships:
        folder.tracks[:] = [songs[identifier].tracks[0] for identifier in members]
    retained_ids = {entry.id for entry in retained}
    retained.extend(song for identifier, song in songs.items() if identifier not in retained_ids)
    entries[:] = retained
    return entries


def merge_libraries(
    existing: list[LibraryEntry], incoming: list[LibraryEntry],
) -> list[LibraryEntry]:
    """Append imports in place, preserving existing metadata and card order.

    就地追加导入，保留现有元数据和卡片顺序。
    """
    _validate_entries(incoming)
    normalize_library(existing)
    if incoming is existing:
        return existing
    normalize_library(incoming)
    known_ids = {entry.id for entry in existing}
    for entry in incoming:
        if entry.id not in known_ids:
            existing.append(entry)
            known_ids.add(entry.id)
    return normalize_library(existing)


def copy_entries(
    entries: list[LibraryEntry], entry_ids: list[str] | None = None,
) -> list[LibraryEntry]:
    """Return detached copies of selected cards and their independent member songs.

    返回所选卡片和成员歌曲的独立副本；所选副本之间保留共享引用。
    """
    from copy import deepcopy

    detached = deepcopy(entries)
    normalize_library(detached)
    requested = None if entry_ids is None else set(entry_ids)
    if requested is not None and requested - {entry.id for entry in detached}:
        raise ValueError("要复制的条目不存在")
    selected = [entry for entry in detached if requested is None or entry.id in requested]
    originals = {entry.id: entry for entry in detached}
    copies: dict[str, LibraryEntry] = {}

    def clone(entry: LibraryEntry) -> LibraryEntry:
        if entry.id in copies:
            return copies[entry.id]
        copied = replace(entry, id=str(uuid4()), tracks=[])
        copies[entry.id] = copied
        if entry.kind == "single":
            copied.tracks[:] = [replace(track, id=copied.id, song_id=copied.id)
                                for track in entry.tracks]
        else:
            copied.tracks[:] = [clone(originals[track.song_id]).tracks[0]
                                for track in entry.tracks]
        return copied

    result = [clone(entry) for entry in selected]
    selected_ids = {entry.id for entry in selected}
    result.extend(copied for identifier, copied in copies.items() if identifier not in selected_ids)
    return normalize_library(result)


def find_song(entries: list[LibraryEntry], song_id: str) -> LibraryEntry | None:
    """Find a canonical single, including an in-memory migrated legacy ID.

    查找标准单曲，也接受内存迁移保留的旧 ID。
    """
    for entry in entries:
        if entry.kind == "single" and (
            entry.id == song_id or any(track.id == song_id for track in entry.tracks)
        ):
            return entry
    for entry in entries:
        if entry.kind == "single" and song_id in getattr(entry, "_song_aliases", ()):
            return entry
    return None


def delete_song(entries: list[LibraryEntry], song_id: str) -> list[LibraryEntry]:
    """Delete a song entity and all album memberships in place.

    就地删除歌曲实体及其在所有专辑中的引用。
    """
    normalize_library(entries)
    song = next((entry for entry in entries
                 if entry.kind == "single" and entry.id == song_id), None)
    if song is None:
        return entries
    for entry in entries:
        if entry.kind == "album":
            entry.tracks[:] = [track for track in entry.tracks if track.song_id != song_id]
    entries[:] = [entry for entry in entries if entry is not song]
    return entries


def encode_library(entries: list[LibraryEntry]) -> dict:
    """Encode v4: album folders contain only song IDs; equal sources are valid.

    编码为 v4：专辑文件夹只保存歌曲 ID，允许来源完全相同的独立歌曲。
    """
    normalize_library(entries)
    encoded = []
    for entry in entries:
        item = asdict(entry)
        if entry.kind == "album":
            item.pop("tracks")
            item["song_ids"] = [track.song_id for track in entry.tracks]
        encoded.append(item)
    return {"version": 4, "entries": encoded}


def _entry_from_dict(item: object) -> LibraryEntry:
    if not isinstance(item, dict):
        raise ValueError("音乐库条目必须是对象")
    fields = dict(item)
    nested = fields.get("tracks", [])
    if not isinstance(nested, list) or any(not isinstance(track, dict) for track in nested):
        raise ValueError("专辑曲目必须是对象列表")
    fields["tracks"] = [Track(**track) for track in nested]
    return LibraryEntry(**fields)


def decode_library(payload: dict) -> list[LibraryEntry]:
    """Decode nested v1/v2 snapshots or ID graphs in v3/v4.

    解码 v1/v2 嵌套快照或 v3/v4 ID 图结构，迁移前验证完整数据。
    """
    try:
        if not isinstance(payload, dict) or type(payload.get("version")) is not int:
            raise ValueError("不支持的音乐库格式")
        version = payload["version"]
        if version not in (1, 2, 3, 4) or not isinstance(payload.get("entries"), list):
            raise ValueError("不支持的音乐库格式")
        entries = []
        references = []
        ids: set[str] = set()
        track_sources: dict[str, Source] = {}
        for item in payload["entries"]:
            if not isinstance(item, dict):
                raise ValueError("音乐库条目必须是对象")
            fields = dict(item)
            if version >= 3 and (not isinstance(fields.get("id"), str) or not fields["id"].strip()):
                raise ValueError("v3 条目必须保存非空 ID")
            refs = None
            if version >= 3 and fields.get("kind", "single") == "album":
                if "tracks" in fields or "song_ids" not in fields:
                    raise ValueError("v3 专辑必须仅保存歌曲引用")
                refs = fields.pop("song_ids")
                if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
                    raise ValueError("专辑歌曲引用必须是非空 ID 列表")
                if len(refs) != len(set(refs)):
                    raise ValueError("专辑歌曲引用重复")
            entry = _entry_from_dict(fields)
            if entry.id in ids:
                raise ValueError("音乐库条目 ID 重复")
            ids.add(entry.id)
            for track in entry.tracks:
                if track.id in track_sources and track_sources[track.id] != _source(track):
                    raise ValueError("不同歌曲使用了重复的曲目 ID")
                track_sources[track.id] = _source(track)
            if version >= 3 and entry.kind == "single":
                if len(entry.tracks) > 1 or (entry.resolved and not entry.tracks):
                    raise ValueError("标准单曲必须恰好包含一首已解析歌曲")
                if entry.tracks:
                    track = entry.tracks[0]
                    if (track.id != entry.id or track.song_id != entry.id
                            or _source(track) != (entry.bvid, entry.page)):
                        raise ValueError("标准单曲 ID 或来源不一致")
            entries.append(entry)
            if refs is not None:
                references.append((entry, refs))
        if version >= 3:
            songs = {entry.id: entry for entry in entries if entry.kind == "single"}
            sources: set[Source] = set()
            for song in songs.values():
                source = song.bvid, song.page
                if version == 3 and source in sources:
                    raise ValueError("标准单曲来源重复")
                sources.add(source)
            for album, refs in references:
                for ref in refs:
                    if ref not in songs or not songs[ref].tracks:
                        raise ValueError("专辑引用的歌曲不存在或尚未解析")
                    album.tracks.append(songs[ref].tracks[0])
        if version < 3 and not any(track.song_id for entry in entries for track in entry.tracks):
            # Only pre-entity snapshots need source-based migration compatibility.
            return _normalize_legacy(entries)
        return normalize_library(entries)
    except (TypeError, KeyError) as exc:
        raise ValueError(f"音乐库数据格式不正确：{exc}") from exc

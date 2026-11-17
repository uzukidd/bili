from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from bili_player.catalog import (
    decode_library, delete_song, encode_library, find_song, merge_libraries,
    normalize_library,
)
from bili_player.models import LibraryEntry, Store, Track


BVID = "BV1xx411c7mD"
OTHER_BVID = "BV1yy411c7mD"


def song(title="歌曲", *, page=1, identifier="old-track", **kwargs):
    return Track(title, BVID, 100 + page, page, id=identifier, **kwargs)


def album(title, tracks=(), **kwargs):
    return LibraryEntry(title, BVID, "album", tracks=list(tracks), resolved=True, **kwargs)


class CatalogTests(unittest.TestCase):
    def test_two_albums_share_the_exact_canonical_single_track(self):
        first_track = song("首个标题", identifier="first-copy", artist="手填作者")
        second_track = song("冲突旧标题", identifier="second-copy", date="手填日期")
        first = album("专辑甲", [first_track, first_track])
        second = album("专辑乙", [second_track])
        entries = [first, second]
        tracks_list = first.tracks
        self.assertIs(normalize_library(entries), entries)
        self.assertEqual(entries[:2], [first, second])
        self.assertEqual(len(entries), 3)
        canonical = entries[2]
        self.assertEqual(canonical.kind, "single")
        self.assertEqual((canonical.id, first_track.id, first_track.song_id),
                         ("first-copy", "first-copy", "first-copy"))
        self.assertIs(first.tracks, tracks_list)
        self.assertEqual(len(first.tracks), 1)
        self.assertIs(first.tracks[0], first_track)
        self.assertIs(second.tracks[0], first_track)
        self.assertIs(canonical.tracks[0], first_track)
        self.assertEqual((canonical.title, canonical.artist, canonical.date),
                         ("首个标题", "手填作者", "手填日期"))
        self.assertIs(find_song(entries, "second-copy"), canonical)
        self.assertIs(normalize_library(entries)[2], canonical)

    def test_explicit_single_wins_even_after_albums_and_keeps_stable_entry_id(self):
        embedded = song("专辑旧标题", artist="旧作者", duration=80, date="旧日期")
        preferred = song("单曲缓存标题", identifier="single-track", artist="缓存作者")
        single = LibraryEntry("单曲手填名", BVID, id="single-entry", tracks=[preferred],
                              artist="手填作者", date="手填日期", cover="custom-cover")
        duplicate = LibraryEntry("后来的单曲", BVID, id="duplicate-entry",
                                 tracks=[song("后来的曲名", identifier="duplicate-track")])
        folder = album("专辑", [embedded])
        entries = [folder, single, duplicate]
        normalize_library(entries)
        self.assertEqual(entries, [folder, single])
        self.assertIs(single.tracks[0], preferred)
        self.assertIs(folder.tracks[0], preferred)
        self.assertEqual((single.id, preferred.id, preferred.song_id),
                         ("single-entry",) * 3)
        self.assertEqual((preferred.title, preferred.artist, preferred.date, preferred.duration),
                         ("单曲手填名", "手填作者", "手填日期", 80))
        self.assertEqual(single.cover, "custom-cover")
        for old_id in ("old-track", "single-track", "duplicate-entry", "duplicate-track"):
            self.assertIs(find_song(entries, old_id), single)

    def test_nonempty_fallback_prefers_earlier_single_cache_over_later_copies(self):
        first = LibraryEntry("", BVID, tracks=[song("缓存手填", artist="缓存作者")])
        second = LibraryEntry("后备标题", BVID, tracks=[song("后备缓存", identifier="later")],
                              artist="后备作者", date="后备日期", cover="saved-cover")
        entries = [first, second]
        normalize_library(entries)
        self.assertEqual(entries, [first])
        self.assertEqual((first.title, first.artist, first.date, first.cover),
                         ("缓存手填", "缓存作者", "后备日期", "saved-cover"))

    def test_editing_canonical_track_is_visible_in_every_album(self):
        entries = [album("甲", [song()]), album("乙", [song(identifier="copy")])]
        normalize_library(entries)
        canonical = entries[-1]
        canonical.tracks[0].title = "编辑曲名"
        canonical.tracks[0].artist = "编辑作者"
        self.assertEqual([entry.tracks[0].title for entry in entries], ["编辑曲名"] * 3)
        self.assertEqual([entry.tracks[0].artist for entry in entries], ["编辑作者"] * 3)
        self.assertIs(entries[0].tracks[0], entries[1].tracks[0])
        loaded = decode_library(encode_library(entries))
        self.assertEqual((loaded[-1].title, loaded[-1].artist), ("编辑曲名", "编辑作者"))
        self.assertEqual(loaded[0].tracks[0].title, "编辑曲名")
        canonical.tracks[0].artist = ""
        canonical.title = "从单曲卡片编辑"
        loaded = decode_library(encode_library(entries))
        self.assertEqual((loaded[-1].title, loaded[-1].artist), ("从单曲卡片编辑", ""))
        self.assertEqual(loaded[1].tracks[0].artist, "")

    def test_normalization_preserves_folder_order_membership_order_and_pages(self):
        first_page = song(identifier="page-one")
        second_page = song(page=2, identifier="page-two")
        first = album("甲", [second_page, first_page, second_page])
        empty = album("有 BV 的空专辑")
        last = album("乙", [song(identifier="another-copy")])
        entries = [first, empty, last]
        normalize_library(entries)
        self.assertEqual(entries[:3], [first, empty, last])
        self.assertEqual([track.page for track in first.tracks], [2, 1])
        self.assertEqual([entry.page for entry in entries[3:]], [2, 1])
        self.assertEqual(empty.tracks, [])
        self.assertTrue(empty.resolved)
        first.tracks.clear()
        normalize_library(entries)
        self.assertEqual(first.tracks, [])
        self.assertEqual(len(entries), 5)

    def test_unresolved_single_keeps_entity_until_resolution(self):
        single = LibraryEntry("保留手填名", BVID, page=2, id="pending", artist="手填作者")
        entries = [single]
        normalize_library(entries)
        self.assertIs(entries[0], single)
        self.assertEqual(single.tracks, [])
        self.assertFalse(single.resolved)
        single.tracks.append(song("获取标题", page=2, identifier="remote-track"))
        normalize_library(entries)
        self.assertEqual(len(entries), 1)
        self.assertEqual((single.tracks[0].id, single.tracks[0].song_id), ("pending", "pending"))
        self.assertEqual((single.tracks[0].title, single.tracks[0].artist),
                         ("保留手填名", "手填作者"))
        self.assertTrue(single.resolved)

    def test_removing_album_only_leaves_canonical_singles(self):
        folder = album("文件夹", [song()])
        entries = [folder]
        normalize_library(entries)
        canonical = entries[1]
        entries.remove(folder)
        normalize_library(entries)
        self.assertEqual(entries, [canonical])
        self.assertIs(canonical.tracks[0], folder.tracks[0])

    def test_delete_song_removes_every_membership_but_preserves_folders_and_other_music(self):
        first = album("甲", [song(), song(page=2, identifier="other")])
        second = album("乙", [song(identifier="legacy-alias")])
        entries = [first, second]
        normalize_library(entries)
        self.assertIs(delete_song(entries, "legacy-alias"), entries)
        self.assertEqual([entry.kind for entry in entries], ["album", "album", "single"])
        self.assertEqual([track.page for track in first.tracks], [2])
        self.assertEqual(second.tracks, [])
        self.assertTrue(second.resolved)
        before = list(entries)
        self.assertIs(delete_song(entries, "does-not-exist"), entries)
        self.assertEqual(entries, before)

    def test_merge_preserves_existing_metadata_even_for_album_only_song(self):
        existing_folder = album("原文件夹", [song("现有手填名", artist="现有作者")], id="existing-folder")
        existing = [existing_folder]
        incoming_single = LibraryEntry("导入新名称", BVID, id="imported-song",
                                       tracks=[song("导入缓存", identifier="imported-track")],
                                       artist="导入作者", date="后备日期")
        incoming_folder = album("新文件夹", [song("导入专辑副本", identifier="imported-copy"),
                                            song(page=2, identifier="new-source")], id="new-folder")
        self.assertIs(merge_libraries(existing, [incoming_single, incoming_folder]), existing)
        self.assertEqual([entry.title for entry in existing[:3]], ["原文件夹", "现有手填名", "新文件夹"])
        self.assertEqual(len(existing), 4)
        canonical = existing[1]
        self.assertEqual((canonical.title, canonical.artist, canonical.date),
                         ("现有手填名", "现有作者", "后备日期"))
        self.assertIs(incoming_folder.tracks[0], canonical.tracks[0])
        self.assertIs(existing_folder.tracks[0], canonical.tracks[0])
        merge_libraries(existing, [incoming_folder])
        self.assertEqual(len(existing), 4)

    def test_v3_references_roundtrip_and_contain_no_album_metadata_copies(self):
        entries = [album("甲", [song()]), album("乙", [song(identifier="copy")]),
                   LibraryEntry("待解析", OTHER_BVID, id="pending")]
        payload = encode_library(entries)
        self.assertEqual(payload["version"], 3)
        for item in payload["entries"][:2]:
            self.assertNotIn("tracks", item)
            self.assertEqual(item["song_ids"], [entries[-1].id])
        loaded = decode_library(copy.deepcopy(payload))
        self.assertEqual(loaded, entries)
        self.assertIs(loaded[0].tracks[0], loaded[1].tracks[0])
        self.assertIs(loaded[0].tracks[0], loaded[-1].tracks[0])
        self.assertFalse(loaded[2].resolved)

    def test_v3_rejects_missing_references_duplicate_ids_and_corrupt_shapes(self):
        payload = encode_library([album("甲", [song()])])
        invalid = []
        changed = copy.deepcopy(payload)
        changed["entries"][0]["song_ids"] = ["missing"]
        invalid.append(changed)
        changed = copy.deepcopy(payload)
        changed["entries"].append(copy.deepcopy(changed["entries"][1]))
        invalid.append(changed)
        for refs in (None, "not-a-list", [None], [""], ["old-track", "old-track"]):
            changed = copy.deepcopy(payload)
            changed["entries"][0]["song_ids"] = refs
            invalid.append(changed)
        for fields in ({"tracks": []}, {"song_ids": [payload["entries"][0]["id"]]}):
            changed = copy.deepcopy(payload)
            changed["entries"][0].update(fields)
            invalid.append(changed)
        for fields in ({"id": "different"}, {"song_id": "different"}, {"song_id": None}):
            changed = copy.deepcopy(payload)
            changed["entries"][1]["tracks"][0].update(fields)
            invalid.append(changed)
        changed = copy.deepcopy(payload)
        changed["entries"][1].pop("id")
        invalid.append(changed)
        changed = copy.deepcopy(payload)
        changed["entries"][1]["tracks"] = []
        invalid.append(changed)
        changed = copy.deepcopy(payload)
        duplicate = copy.deepcopy(changed["entries"][1])
        duplicate["id"] = duplicate["tracks"][0]["id"] = duplicate["tracks"][0]["song_id"] = "different-id"
        changed["entries"].append(duplicate)
        invalid.append(changed)
        for changed in invalid:
            with self.subTest(payload=changed), self.assertRaises(ValueError):
                decode_library(changed)

    def test_legacy_different_sources_cannot_share_a_track_id(self):
        first = asdict(album("甲", [song()]))
        second = asdict(album("乙", [song(page=2)]))
        with self.assertRaisesRegex(ValueError, "重复"):
            decode_library({"version": 2, "entries": [first, second]})

    def test_same_object_repeated_and_merge_with_self_keep_identity(self):
        single = LibraryEntry("歌曲", BVID, tracks=[song()])
        entries = [single, single]
        normalize_library(entries)
        identifier = single.id
        self.assertEqual(entries, [single])
        merge_libraries(entries, entries)
        self.assertEqual(entries, [single])
        self.assertEqual(single.id, identifier)

    def test_song_id_is_appended_and_validated(self):
        track = Track("歌曲", BVID, 101, 1, "作者", 50, "occurrence", "日期", "canonical")
        self.assertEqual((track.id, track.date, track.song_id), ("occurrence", "日期", "canonical"))
        with self.assertRaises(ValueError):
            Track("歌曲", BVID, 101, 1, song_id=None)


class CatalogStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "library.json"
        self.store = Store(self.path)

    def legacy(self, version):
        embedded = song("旧专辑曲名", identifier="legacy-album-track", artist="旧作者")
        canonical_track = song("单曲缓存名", identifier="legacy-single-track", date="保留日期")
        single = LibraryEntry("手填权威曲名", BVID, id="legacy-single", tracks=[canonical_track],
                              artist="手填权威作者", resolved=True)
        entries = [album("旧专辑", [embedded], id="legacy-album"), single]
        queue = [song("队列快照", identifier="queue-occurrence-one"),
                 song("队列快照", identifier="queue-occurrence-two")]
        data = {"version": version, "entries": [asdict(entry) for entry in entries],
                "tracks": [asdict(track) for track in queue], "volume": 37,
                "settings": {"theme": "dark", "language": "en_US"}}
        for entry in data["entries"]:
            for track in entry["tracks"]:
                track.pop("song_id")
        for track in data["tracks"]:
            track.pop("song_id")
        original = (json.dumps(data, ensure_ascii=False, indent=3).replace("\n", "\r\n") + "\r\n").encode("utf-8")
        self.path.write_bytes(original)
        return original

    def test_v1_v2_migrate_in_memory_then_backup_exact_bytes_once_before_v3_save(self):
        for version in (1, 2):
            with self.subTest(version=version):
                original = self.legacy(version)
                entries, queue, volume = self.store.load()
                self.assertEqual(self.path.read_bytes(), original)
                backup = self.path.with_name(self.path.name + f".v{version}.bak")
                self.assertFalse(backup.exists())
                self.assertIs(entries[0].tracks[0], entries[1].tracks[0])
                self.assertEqual(entries[0].tracks[0].title, "手填权威曲名")
                self.assertEqual([track.id for track in queue],
                                 ["queue-occurrence-one", "queue-occurrence-two"])
                self.assertEqual([track.song_id for track in queue], ["legacy-single"] * 2)
                self.assertIsNot(queue[0], entries[1].tracks[0])
                self.store.save(entries, queue, volume)
                self.assertEqual(backup.read_bytes(), original)
                payload = json.loads(self.path.read_bytes())
                self.assertEqual(payload["version"], 3)
                self.assertEqual(payload["entries"][0]["song_ids"], ["legacy-single"])
                self.assertNotIn("tracks", payload["entries"][0])
                reloaded = self.store.load()
                self.assertEqual(reloaded, (entries, queue, volume))
                self.assertIs(reloaded[0][0].tracks[0], reloaded[0][1].tracks[0])
                self.store.save([], [], 50)
                self.assertEqual(backup.read_bytes(), original)

    def test_migration_creates_singles_for_every_legacy_album_source(self):
        folder = album("专辑", [song(), song(page=2, identifier="second")])
        original = json.dumps({"version": 2, "entries": [asdict(folder)], "tracks": [], "volume": 50}).encode()
        self.path.write_bytes(original)
        entries, queue, volume = self.store.load()
        self.assertEqual([entry.kind for entry in entries], ["album", "single", "single"])
        self.assertIs(entries[0].tracks[0], entries[1].tracks[0])
        self.assertIs(entries[0].tracks[1], entries[2].tracks[0])
        self.assertEqual(self.path.read_bytes(), original)

    def test_new_queue_occurrences_keep_unique_ids_and_get_canonical_song_id(self):
        entries = [album("专辑", [song()])]
        normalize_library(entries)
        canonical = entries[1]
        queue = [replace(canonical.tracks[0], id="first-occurrence", song_id=""),
                 replace(canonical.tracks[0], id="second-occurrence", song_id="")]
        self.store.save(entries, queue, 40)
        loaded_entries, loaded_queue, _ = self.store.load()
        self.assertEqual([track.id for track in loaded_queue], ["first-occurrence", "second-occurrence"])
        self.assertEqual([track.song_id for track in loaded_queue], [canonical.id] * 2)
        self.assertIsNot(loaded_queue[0], loaded_entries[1].tracks[0])
        self.assertIsNot(loaded_queue[0], loaded_queue[1])

    def test_existing_backup_is_never_overwritten_even_without_load(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.legacy(version)
                backup = self.path.with_name(self.path.name + f".v{version}.bak")
                backup.write_bytes(b"previous backup")
                Store(self.path).save([], [], 40)
                self.assertEqual(backup.read_bytes(), b"previous backup")

    def test_backup_failure_and_replace_failure_preserve_original_and_cleanup(self):
        for version in (1, 2):
            with self.subTest(version=version):
                original = self.legacy(version)
                with patch.object(self.store, "_backup_legacy", side_effect=OSError("backup error")):
                    with self.assertRaises(OSError):
                        self.store.save([], [], 50)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
                with patch("bili_player.models.os.replace", side_effect=OSError("replace error")):
                    with self.assertRaises(OSError):
                        self.store.save([], [], 50)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(self.path.with_name(self.path.name + f".v{version}.bak").read_bytes(), original)
                self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_corrupt_v3_cannot_be_overwritten(self):
        payload = encode_library([album("专辑", [song()])])
        payload.update(tracks=[], volume=50)
        payload["entries"][0]["song_ids"] = ["missing"]
        original = json.dumps(payload).encode()
        self.path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "原文件已保留"):
            self.store.load()
        with self.assertRaisesRegex(ValueError, "原文件已保留"):
            self.store.save([], [], 50)
        self.assertEqual(self.path.read_bytes(), original)

    def test_empty_resolved_album_roundtrip_never_restores_remote_parts(self):
        folder = album("主动清空的专辑", id="empty-folder")
        self.store.save([folder], [], 50)
        entries, _, _ = self.store.load()
        self.assertEqual(entries, [folder])
        self.assertEqual(entries[0].tracks, [])
        self.assertTrue(entries[0].resolved)
        self.assertEqual(json.loads(self.path.read_bytes())["entries"][0]["song_ids"], [])


if __name__ == "__main__":
    unittest.main()

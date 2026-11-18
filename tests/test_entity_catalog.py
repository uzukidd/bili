from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from bili_player.catalog import (
    copy_entries, decode_library, delete_song, encode_library, merge_libraries,
    normalize_library,
)
from bili_player.exchange import dumps_library, loads_library
from bili_player.models import LibraryEntry, Store, Track


BVID = "BV1xx411c7mD"
OTHER_BVID = "BV1yy411c7mD"


def single(identifier: str) -> LibraryEntry:
    track = Track("相同曲名", BVID, 101, 1, "相同作者", 30, identifier, "相同日期", identifier)
    return LibraryEntry("相同曲名", BVID, id=identifier, tracks=[track], resolved=True,
                        artist="相同作者", date="相同日期")


def folder(identifier: str, *songs: LibraryEntry) -> LibraryEntry:
    return LibraryEntry("相同专辑", kind="album", id=identifier,
                        tracks=[song.tracks[0] for song in songs], resolved=True)


class EntityCatalogTests(unittest.TestCase):
    def test_fully_identical_sources_and_metadata_are_independent_by_id(self):
        first, second = single("first"), single("second")
        album = folder("album", first, second)
        entries = [album, first, second]
        normalize_library(entries)
        self.assertEqual([entry.id for entry in entries], ["album", "first", "second"])
        self.assertEqual([track.song_id for track in album.tracks], ["first", "second"])
        self.assertIs(album.tracks[0], first.tracks[0])
        self.assertIs(album.tracks[1], second.tracks[0])
        self.assertIsNot(first.tracks[0], second.tracks[0])
        first.title = "只改第一首"
        first.artist = "第一位作者"
        first.date = "第一首日期"
        normalize_library(entries)
        self.assertEqual((album.tracks[0].title, album.tracks[0].artist, album.tracks[0].date),
                         ("只改第一首", "第一位作者", "第一首日期"))
        self.assertEqual((second.title, second.artist, second.date),
                         ("相同曲名", "相同作者", "相同日期"))
        second.tracks[0].title = "只改第二首曲目"
        normalize_library(entries)
        self.assertEqual(second.title, "只改第二首曲目")
        self.assertEqual(first.title, "只改第一首")

    def test_separate_single_ids_split_accidentally_shared_track_object(self):
        shared = Track("原始曲名", BVID, 101, 1, artist="原始作者")
        first = LibraryEntry("第一首手填名", BVID, id="first", tracks=[shared], artist="第一首作者")
        second = LibraryEntry("", BVID, id="second", tracks=[shared])
        entries = [first, second]
        normalize_library(entries)
        self.assertIsNot(first.tracks[0], second.tracks[0])
        self.assertEqual((second.title, second.artist), ("原始曲名", "原始作者"))
        self.assertEqual((first.tracks[0].id, second.tracks[0].id), ("first", "second"))

    def test_track_ids_without_song_ids_share_only_the_same_id(self):
        one = Track("相同", BVID, 101, 1, id="one")
        two = replace(one, id="two")
        albums = [LibraryEntry("甲", kind="album", tracks=[one, two, one]),
                  LibraryEntry("乙", kind="album", tracks=[replace(one)])]
        entries = list(albums)
        normalize_library(entries)
        self.assertEqual([entry.id for entry in entries[2:]], ["one", "two"])
        self.assertEqual(len(albums[0].tracks), 2)
        self.assertIs(albums[0].tracks[0], albums[1].tracks[0])
        self.assertIsNot(albums[0].tracks[0], albums[0].tracks[1])

    def test_explicit_song_id_wins_over_track_id_and_source(self):
        first, second = single("first"), single("second")
        first.bvid = first.tracks[0].bvid = OTHER_BVID
        reference = replace(second.tracks[0], song_id="first")
        album = LibraryEntry("专辑", kind="album", tracks=[reference])
        entries = [album, second, first]
        normalize_library(entries)
        self.assertEqual(len(entries), 3)
        self.assertIs(album.tracks[0], first.tracks[0])
        self.assertEqual(album.tracks[0].bvid, OTHER_BVID)
        self.assertIsNot(album.tracks[0], second.tracks[0])

    def test_explicit_track_id_wins_over_another_owner_legacy_track_alias(self):
        first, second = single("first"), single("second")
        first.tracks[0].id = "second"
        first.tracks[0].song_id = ""
        reference = replace(second.tracks[0], song_id="")
        album = LibraryEntry("专辑", kind="album", tracks=[reference])
        entries = [album, first, second]
        normalize_library(entries)
        self.assertIs(album.tracks[0], second.tracks[0])
        self.assertIsNot(first.tracks[0], second.tracks[0])

    def test_delete_only_target_id_and_keep_other_identical_song(self):
        first, second = single("first"), single("second")
        albums = [folder("one", first, second), folder("two", second, first)]
        entries = [*albums, first, second]
        normalize_library(entries)
        first._song_aliases.add("legacy-alias")
        delete_song(entries, "legacy-alias")
        self.assertIn(first, entries)
        delete_song(entries, "first")
        self.assertEqual([entry.id for entry in entries], ["one", "two", "second"])
        for album in albums:
            self.assertEqual(album.tracks, second.tracks)
            self.assertIs(album.tracks[0], second.tracks[0])
        delete_song(entries, "second")
        self.assertEqual(entries, albums)
        self.assertTrue(all(not album.tracks and album.resolved for album in albums))

    def test_merge_compares_ids_and_preserves_reference_to_each_duplicate(self):
        first, second = single("first"), single("second")
        existing = [first]
        incoming = [folder("new-album", first, second), copy.deepcopy(first), second]
        incoming[1].title = "导入旧名称"
        merge_libraries(existing, incoming)
        self.assertEqual([entry.id for entry in existing], ["first", "new-album", "second"])
        self.assertEqual(first.title, "相同曲名")
        self.assertIs(existing[1].tracks[0], first.tracks[0])
        self.assertIs(existing[1].tracks[1], second.tracks[0])
        merge_libraries(existing, copy.deepcopy(incoming))
        self.assertEqual(len(existing), 3)

    def test_copy_single_renews_ids_and_does_not_modify_original(self):
        first = single("first")
        entries = [first, folder("album", first)]
        before = copy.deepcopy(entries)
        copied = copy_entries(entries, ["first"])
        self.assertEqual(len(copied), 1)
        self.assertNotEqual(copied[0].id, first.id)
        self.assertEqual((copied[0].tracks[0].id, copied[0].tracks[0].song_id),
                         (copied[0].id, copied[0].id))
        self.assertEqual(entries, before)
        copied[0].tracks[0].title = "副本编辑"
        normalize_library(copied)
        self.assertEqual(first.title, "相同曲名")
        self.assertEqual(entries[1].tracks[0].title, "相同曲名")
        self.assertEqual(copied[0].title, "副本编辑")

    def test_copy_albums_clones_members_once_across_selected_copies(self):
        first, second = single("first"), single("second")
        albums = [folder("one", first, second), folder("two", second, first)]
        entries = [*albums, first, second]
        copied = copy_entries(entries, ["one", "two", "first"])
        self.assertEqual([entry.kind for entry in copied], ["album", "album", "single", "single"])
        self.assertTrue({entry.id for entry in entries}.isdisjoint(entry.id for entry in copied))
        self.assertEqual(len({entry.id for entry in copied}), 4)
        self.assertIs(copied[0].tracks[0], copied[2].tracks[0])
        self.assertIs(copied[0].tracks[0], copied[1].tracks[1])
        self.assertIs(copied[0].tracks[1], copied[1].tracks[0])
        self.assertIsNot(copied[0].tracks[0], first.tracks[0])
        self.assertIsNot(copied[0].tracks[1], second.tracks[0])
        copied[0].tracks[0].title = "副本共享编辑"
        self.assertEqual(copied[1].tracks[1].title, "副本共享编辑")
        self.assertEqual(albums[0].tracks[0].title, "相同曲名")
        merge_libraries(entries, copied)
        self.assertEqual(len(entries), 8)

    def test_copy_empty_unresolved_and_all_entries(self):
        unresolved = LibraryEntry("待解析", BVID, id="pending")
        empty = LibraryEntry("空专辑", kind="album", id="empty", resolved=True)
        copies = copy_entries([unresolved, empty])
        self.assertFalse(copies[0].resolved)
        self.assertEqual(copies[0].tracks, [])
        self.assertTrue(copies[1].resolved)
        self.assertEqual(copies[1].tracks, [])
        self.assertEqual(copy_entries([unresolved, empty], []), [])
        with self.assertRaisesRegex(ValueError, "不存在"):
            copy_entries([unresolved], ["missing"])

    def test_v4_accepts_duplicate_sources_and_v3_still_enforces_original_schema(self):
        first, second = single("first"), single("second")
        payload = encode_library([folder("album", first, second), first, second])
        self.assertEqual(payload["version"], 4)
        self.assertEqual(payload["entries"][0]["song_ids"], ["first", "second"])
        self.assertNotIn("tracks", payload["entries"][0])
        restored = decode_library(copy.deepcopy(payload))
        self.assertIs(restored[0].tracks[0], restored[1].tracks[0])
        self.assertIs(restored[0].tracks[1], restored[2].tracks[0])
        payload["version"] = 3
        with self.assertRaisesRegex(ValueError, "来源重复"):
            decode_library(payload)

    def test_exchange_keeps_duplicate_ids_and_repeat_import_is_idempotent(self):
        first, second = single("first"), single("second")
        entries = [folder("album", first, second), first, second]
        text = dumps_library(entries)
        self.assertEqual(json.loads(text)["version"], 3)
        first_import, second_import = loads_library(text), loads_library(text)
        self.assertEqual([entry.id for entry in first_import], ["album", "first", "second"])
        self.assertIsNot(first_import[1].tracks[0], second_import[1].tracks[0])
        merge_libraries(first_import, second_import)
        self.assertEqual(len(first_import), 3)
        self.assertIs(first_import[0].tracks[0], first_import[1].tracks[0])
        self.assertIs(first_import[0].tracks[1], first_import[2].tracks[0])
        legacy = json.loads(dumps_library([entries[1]]))
        legacy["version"] = 2
        self.assertEqual(loads_library(json.dumps(legacy))[0].id, "first")


class EntityStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "library.json"
        self.store = Store(self.path)

    def test_duplicates_save_reload_edit_and_delete_independently(self):
        first, second = single("first"), single("second")
        entries = [folder("album", first, second), first, second]
        queue = [replace(first.tracks[0], id="occurrence-one"),
                 replace(second.tracks[0], id="occurrence-two"),
                 replace(first.tracks[0], id="occurrence-three", song_id=""),
                 replace(first.tracks[0], id="orphan", song_id="deleted-song")]
        queue_before = copy.deepcopy(queue)
        self.store.save(entries, queue, 40)
        self.assertEqual(queue, queue_before)
        restored, restored_queue, volume = self.store.load()
        self.assertEqual(restored_queue, queue_before)
        self.assertEqual([track.song_id for track in restored_queue], ["first", "second", "", "deleted-song"])
        restored[0].tracks[0].title = "只修改第一首"
        self.store.save(restored, restored_queue, volume)
        restored, restored_queue, volume = self.store.load()
        self.assertEqual([entry.title for entry in restored[1:]], ["只修改第一首", "相同曲名"])
        self.assertEqual(restored_queue, queue_before)
        delete_song(restored, "first")
        self.store.save(restored, restored_queue, volume)
        final, final_queue, _ = self.store.load()
        self.assertEqual([entry.id for entry in final], ["album", "second"])
        self.assertEqual(final[0].tracks[0].song_id, "second")
        self.assertEqual(final_queue, queue_before)

    def v3_bytes(self):
        song = single("first")
        payload = encode_library([folder("album", song), song])
        payload.update(version=3, tracks=[asdict(replace(song.tracks[0], id="occurrence", song_id="orphan"))],
                       volume=37, settings={"theme": "dark"})
        original = (json.dumps(payload, ensure_ascii=False, indent=3).replace("\n", "\r\n") + "\r\n").encode()
        self.path.write_bytes(original)
        return original

    def test_v3_load_and_first_v4_save_backup_exact_original_bytes_once(self):
        original = self.v3_bytes()
        entries, queue, volume = self.store.load()
        backup = self.path.with_name("library.json.v3.bak")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(backup.exists())
        self.assertEqual((queue[0].id, queue[0].song_id), ("occurrence", "orphan"))
        entries.append(single("second"))
        entries[0].tracks.append(entries[-1].tracks[0])
        self.store.save(entries, queue, volume)
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(json.loads(self.path.read_bytes())["version"], 4)
        reloaded = self.store.load()
        self.assertEqual(reloaded, (entries, queue, volume))
        self.store.save(*reloaded)
        self.assertEqual(backup.read_bytes(), original)

    def test_v3_backup_failure_and_replace_failure_preserve_original(self):
        original = self.v3_bytes()
        with patch.object(self.store, "_backup_legacy", side_effect=OSError("backup failed")):
            with self.assertRaises(OSError):
                self.store.save([], [], 50)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        with patch("bili_player.models.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.store.save([], [], 50)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.path.with_name("library.json.v3.bak").read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.path.with_name("library.json.v3.bak").write_bytes(b"already backed up")
        self.store.save([], [], 50)
        self.assertEqual(self.path.with_name("library.json.v3.bak").read_bytes(), b"already backed up")

    def test_legacy_explicit_queue_references_never_follow_source(self):
        for version in (1, 2):
            with self.subTest(version=version):
                song = single("song")
                for track in song.tracks:
                    track.song_id = ""
                queue = replace(song.tracks[0], id="occurrence", song_id="explicit-reference")
                payload = {"version": version, "entries": [asdict(song)],
                           "tracks": [asdict(queue)], "volume": 30}
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                _, restored, _ = self.store.load()
                self.assertEqual((restored[0].id, restored[0].song_id),
                                 ("occurrence", "explicit-reference"))

    def test_legacy_entity_references_preserve_duplicate_sources(self):
        first, second = single("first"), single("second")
        for version in (1, 2):
            entries = [folder("album", first, second), first, second]
            payload = {"version": version, "entries": [asdict(entry) for entry in entries],
                       "tracks": [], "volume": 30}
            self.path.write_text(json.dumps(payload), encoding="utf-8")
            restored, _, _ = self.store.load()
            self.assertEqual([entry.id for entry in restored], ["album", "first", "second"])
            self.assertEqual([track.song_id for track in restored[0].tracks], ["first", "second"])


if __name__ == "__main__":
    unittest.main()

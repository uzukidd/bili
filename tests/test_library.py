from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from bili_player.api import BilibiliClient, BilibiliError, VideoInfo
from bili_player.models import LibraryEntry, Store, Track


BVID = "BV1xx411c7mD"
OTHER_BVID = "BV1yy411c7mD"


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "library.json"
        self.store = Store(self.path)

    def write_data(self, **overrides):
        data = {"version": 2, "entries": [], "tracks": [], "volume": 40}
        data.update(overrides)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return self.path.read_bytes()

    def legacy_bytes(self):
        # Deliberately retain CRLF, whitespace and literal UTF-8 for exact backup checks.
        return (
            '{\r\n  "version": 1, "entries": '
            '[{"title":"旧专辑", "bvid":"' + BVID + '", "kind":"album", '
            '"page":1, "id":"legacy-album"}],\r\n'
            ' "tracks": [], "volume": 37\r\n}\r\n'
        ).encode("utf-8")

    def test_positional_compatibility_empty_albums_and_independent_lists(self):
        single = LibraryEntry("单曲", BVID, "single", 2, "single-id")
        self.assertEqual((single.page, single.id), (2, "single-id"))
        first = LibraryEntry("空专辑", "", "album", resolved=True)
        second = LibraryEntry("另一个", kind="album", resolved=True)
        first.tracks.append(Track("歌曲", BVID, 101, 1))
        self.assertEqual(second.tracks, [])
        with self.assertRaises(ValueError):
            LibraryEntry("没有 BV 的单曲")

    def test_empty_resolved_album_survives_every_save(self):
        for bvid in ("", BVID):
            with self.subTest(bvid=bvid):
                entry = LibraryEntry("已清空", bvid, "album", resolved=True)
                self.store.save([entry], [], 30)
                entries, queue, volume = self.store.load()
                self.assertEqual((entries, queue, volume), ([entry], [], 30))
                self.assertTrue(entries[0].resolved)
                self.assertEqual(entries[0].tracks, [])
                self.store.save(entries, queue, volume)
                self.assertEqual(self.store.load()[0][0], entry)

    def test_v1_load_does_not_write_and_first_v2_save_backs_up_exact_bytes(self):
        original = self.legacy_bytes()
        self.path.write_bytes(original)
        entries, queue, volume = self.store.load()
        self.assertEqual(self.path.read_bytes(), original)
        backup = self.path.with_name("library.json.v1.bak")
        self.assertFalse(backup.exists())
        self.assertFalse(entries[0].resolved)
        self.assertEqual(entries[0].tracks, [])
        self.assertEqual(entries[0].cover, "")
        self.assertEqual(entries[0].id, "legacy-album")
        self.assertEqual(self.store.settings, {"theme": "light", "language": "zh_CN"})
        self.store.save(entries, queue, volume, {"theme": "dark", "language": "en_US"})
        self.assertEqual(backup.read_bytes(), original)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        self.assertFalse(data["entries"][0]["resolved"])
        self.store.save([], [], 50)
        self.assertEqual(backup.read_bytes(), original)

    def test_v1_save_without_load_still_backs_up_once(self):
        original = self.legacy_bytes()
        self.path.write_bytes(original)
        Store(self.path).save([], [], 20)
        backup = self.path.with_name("library.json.v1.bak")
        self.assertEqual(backup.read_bytes(), original)
        self.path.write_bytes(original)
        backup.write_bytes(b"previous preserved backup")
        Store(self.path).save([], [], 20)
        self.assertEqual(backup.read_bytes(), b"previous preserved backup")

    def test_failed_migration_replace_preserves_original_and_exact_backup(self):
        original = self.legacy_bytes()
        self.path.write_bytes(original)
        with patch("bili_player.models.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.store.save([], [], 50)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.path.with_name("library.json.v1.bak").read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_backup_failure_prevents_replacement(self):
        original = self.legacy_bytes()
        self.path.write_bytes(original)
        with patch.object(self.store, "_backup_v1", side_effect=OSError("backup error")):
            with self.assertRaises(OSError):
                self.store.save([], [], 50)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_settings_defaults_roundtrip_and_omitted_save_preserves_edits(self):
        self.assertEqual(self.store.settings, {"theme": "light", "language": "zh_CN"})
        self.write_data(settings={"theme": "dark"})
        self.store.load()
        self.assertEqual(self.store.settings, {"theme": "dark", "language": "zh_CN"})
        self.store.settings["language"] = "en_US"
        self.store.save([], [], 25)
        fresh = Store(self.path)
        fresh.load()
        self.assertEqual(fresh.settings, {"theme": "dark", "language": "en_US"})
        fresh.save([], [], 25, {"theme": "light", "language": "zh_CN"})
        fresh.load()
        self.assertEqual(fresh.settings, {"theme": "light", "language": "zh_CN"})

    def test_invalid_settings_cannot_replace_file(self):
        self.store.save([], [], 40)
        original = self.path.read_bytes()
        for settings in ([], {"theme": "sepia"}, {"language": "fr_FR"},
                         {"theme": None}, {"language": True}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.store.save([], [], 40, settings)
            self.assertEqual(self.path.read_bytes(), original)
        for settings in (None, [], {"theme": "sepia"}, {"language": "fr_FR"}):
            original = self.write_data(settings=settings)
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(ValueError, "原文件已保留"):
                    self.store.load()
                with self.assertRaises(ValueError):
                    Store(self.path).save([], [], 40)
                self.assertEqual(self.path.read_bytes(), original)

    def test_edited_mixed_source_tracks_roundtrip_and_do_not_alias_queue(self):
        first = Track("重命名", OTHER_BVID, 202, 2, "编辑作者", 31, "edited-track")
        second = Track("另一首", BVID, 101, 1)
        album = LibraryEntry("我的专辑", BVID, "album", tracks=[first, second],
                             cover="https://image.test/cover.jpg", resolved=True)
        single = LibraryEntry("缓存单曲", OTHER_BVID, tracks=[first], resolved=True)
        self.store.save([album, single], [first], 60)
        entries, queue, _ = self.store.load()
        self.assertEqual(entries, [album, single])
        self.assertEqual(queue, [first])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["entries"][0]["tracks"][0], asdict(first))
        entries[0].tracks[0].title = "只改专辑"
        self.assertEqual(queue[0].title, "重命名")
        self.assertEqual(entries[1].tracks[0].title, "重命名")
        album.tracks = [second]
        self.store.save([album], queue, 60)
        loaded = self.store.load()[0][0]
        self.assertEqual(loaded.tracks, [second])
        self.assertTrue(loaded.resolved)
        self.assertEqual(loaded.cover, album.cover)

    def test_nested_and_metadata_validation_on_load_protects_original(self):
        valid = asdict(LibraryEntry("专辑", "", "album", resolved=True))
        malformed_tracks = [None, {}, [None], [{"title": "missing required fields"}],
                            [asdict(Track("曲目", BVID, 1, 1)) | {"cid": True}],
                            [asdict(Track("曲目", BVID, 1, 1)) | {"duration": -1}]]
        changes = [{"tracks": value} for value in malformed_tracks]
        changes += [{"cover": None}, {"resolved": 1}, {"resolved": "false"}, {"kind": "other"}]
        for change in changes:
            with self.subTest(change=change):
                original = self.write_data(entries=[valid | change])
                with self.assertRaisesRegex(ValueError, "原文件已保留"):
                    self.store.load()
                with self.assertRaises(ValueError):
                    Store(self.path).save([], [], 40)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertFalse(self.path.with_name("library.json.v1.bak").exists())

    def test_nested_mutations_revalidated_before_save(self):
        album = LibraryEntry("专辑", kind="album", resolved=True,
                             tracks=[Track("曲目", BVID, 101, 1)])
        self.store.save([album], [], 40)
        original = self.path.read_bytes()
        album.tracks[0].cid = 0
        with self.assertRaises(ValueError):
            self.store.save([album], [], 40)
        self.assertEqual(self.path.read_bytes(), original)
        for tracks in ([{}], None, "tracks"):
            album.tracks = tracks
            with self.subTest(tracks=tracks), self.assertRaises(ValueError):
                self.store.save([album], [], 40)
            self.assertEqual(self.path.read_bytes(), original)

    def test_unsupported_and_boolean_versions_are_rejected(self):
        for version in (0, 3, True, "2"):
            with self.subTest(version=version):
                original = self.write_data(version=version)
                with self.assertRaises(ValueError):
                    self.store.load()
                self.assertEqual(self.path.read_bytes(), original)


class VideoInfoTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.session.headers = {}
        self.response = self.session.get.return_value
        self.client = BilibiliClient(self.session, timeout=9)
        self.data = {
            "title": "视频原名", "pic": "//image.test/video.jpg", "owner": {"name": "作者"},
            "pages": [
                {"page": 2, "cid": 202, "part": "第二首", "duration": 20},
                {"page": 1, "cid": 101, "part": "第一首", "duration": 10},
            ],
        }

    def reply(self):
        self.response.json.return_value = {"code": 0, "data": copy.deepcopy(self.data)}

    def test_get_video_returns_cover_title_and_album_pages_in_one_request(self):
        self.reply()
        info = self.client.get_video(LibraryEntry("专辑", BVID, "album"))
        self.assertIsInstance(info, VideoInfo)
        self.assertEqual((info.title, info.cover), ("视频原名", "https://image.test/video.jpg"))
        self.assertEqual([track.cid for track in info.tracks], [202, 101])
        self.session.get.assert_called_once_with(
            "https://api.bilibili.com/x/web-interface/view", params={"bvid": BVID}, timeout=9,
        )

    def test_get_video_selects_single_page_and_missing_cover_defaults_empty(self):
        self.data.pop("pic")
        self.reply()
        info = self.client.get_video(LibraryEntry("单曲", BVID, page=1))
        self.assertEqual(info.cover, "")
        self.assertEqual([(track.cid, track.page) for track in info.tracks], [(101, 1)])

    def test_absolute_cover_preserved_and_bad_metadata_reported(self):
        self.data["pic"] = "https://image.test/absolute.jpg"
        self.reply()
        self.assertEqual(self.client.get_video(LibraryEntry("单曲", BVID)).cover, self.data["pic"])
        for change in ({"pic": 123}, {"title": ["bad"]}):
            self.data.update({"title": "视频原名", "pic": "https://image.test/absolute.jpg"})
            self.data.update(change)
            self.reply()
            with self.subTest(change=change), self.assertRaises(BilibiliError):
                self.client.get_video(LibraryEntry("单曲", BVID))

    def test_get_tracks_fetches_remote_without_mutating_edited_snapshot(self):
        edited = Track("我的标题", OTHER_BVID, 303, 3, id="preserved-track")
        album = LibraryEntry("我的专辑", BVID, "album", tracks=[edited],
                             cover="https://custom.test/cover.jpg", resolved=True)
        before = copy.deepcopy(album)
        self.reply()
        fetched = self.client.get_tracks(album)
        self.assertEqual([track.cid for track in fetched], [202, 101])
        self.assertEqual(album, before)
        self.assertIs(album.tracks[0], edited)
        self.session.get.assert_called_once()


if __name__ == "__main__":
    unittest.main()

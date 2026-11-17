from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID

import requests

from bili_player.api import HEADERS, BilibiliClient, BilibiliError
from bili_player.models import DEFAULT_VOLUME, LibraryEntry, Store, Track, normalize_bvid


BVID = "BV1xx411c7mD"
VIEW = {
    "code": 0,
    "data": {
        "title": "测试专辑",
        "owner": {"name": "音乐作者"},
        "pages": [
            {"page": 1, "cid": 101, "part": "第一首", "duration": 120},
            {"page": 2, "cid": 202, "part": "第二首", "duration": 180},
        ],
    },
}


class NormalizationTests(unittest.TestCase):
    def test_bvid_and_links(self):
        for value in (
            BVID, "  " + BVID + "\n", "bv" + BVID[2:],
            f"https://www.bilibili.com/video/{BVID}/?p=2&share_source=copy_web",
            f"http://m.bilibili.com/video/{BVID}", f"www.bilibili.com/video/{BVID}",
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_bvid(value), BVID)

    def test_invalid_values_are_rejected(self):
        for value in (
            "", "BV123", "BV1xx411c7mDextra", "av123", None,
            f"https://example.com/video/{BVID}",
            f"https://bilibili.com.example.org/video/{BVID}",
            f"https://www.bilibili.com/video/{BVID}/extra",
            f"https://www.bilibili.com/?bvid={BVID}",
            f"ftp://www.bilibili.com/video/{BVID}",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_bvid(value)

    def test_dataclasses_generate_independent_uuid_ids(self):
        first = LibraryEntry("标题", BVID)
        second = LibraryEntry("标题", BVID)
        track = Track("曲目", BVID, 101, 1)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(str(UUID(first.id)), first.id)
        self.assertEqual(str(UUID(track.id)), track.id)
        self.assertEqual((first.kind, first.page), ("single", 1))
        self.assertEqual((first.artist, first.date), ("", ""))
        self.assertEqual((track.artist, track.duration, track.date), ("", 0, ""))

    def test_manual_metadata_preserves_existing_positional_arguments(self):
        track = Track("曲目", BVID, 101, 1, "手填作者", 120, "track-id", "手填日期")
        self.assertEqual((track.artist, track.duration, track.id, track.date),
                         ("手填作者", 120, "track-id", "手填日期"))
        entry = LibraryEntry("专辑", BVID, "album", 2, "entry-id", [track],
                             "https://image.test/cover.jpg", True, "专辑作者", "专辑日期")
        self.assertEqual((entry.id, entry.tracks, entry.resolved, entry.artist, entry.date),
                         ("entry-id", [track], True, "专辑作者", "专辑日期"))

    def test_manual_metadata_accepts_empty_strings_and_rejects_non_strings(self):
        for field in ("artist", "date"):
            for value in ("", "手填文本", "2026-01-02", "不限定日期格式"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(getattr(Track("", BVID, 101, 1, **{field: value}), field), value)
                    self.assertEqual(getattr(LibraryEntry("", BVID, **{field: value}), field), value)
            for value in (None, False, 0, 3.5, [], {}):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        Track("曲目", BVID, 101, 1, **{field: value})
                    with self.assertRaises(ValueError):
                        LibraryEntry("专辑", BVID, **{field: value})

    def test_invalid_model_fields(self):
        for kwargs in ({"kind": "playlist"}, {"page": 0}, {"page": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LibraryEntry("标题", BVID, **kwargs)
        for kwargs in ({"cid": 0, "page": 1}, {"cid": 101, "page": 0},
                       {"cid": 101, "page": 1, "duration": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Track("曲目", BVID, **kwargs)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "nested" / "library.json"
        self.store = Store(self.path)
        self.entry = LibraryEntry("中文专辑", BVID, kind="album")
        self.track = Track("中文歌曲", BVID, 101, 1, artist="作者", duration=120)

    def test_first_run_is_empty_and_does_not_create_file(self):
        self.assertEqual(self.store.load(), ([], [], DEFAULT_VOLUME))
        self.assertFalse(self.path.exists())

    def test_round_trip_preserves_fields_ids_and_utf8(self):
        self.store.save([self.entry], [self.track], 42)
        self.assertEqual(self.store.load(), ([self.entry], [self.track], 42))
        self.assertIn("中文歌曲", self.path.read_text(encoding="utf-8"))
        self.store.save([], [], 0)
        self.assertEqual(self.store.load(), ([], [], 0))
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_corruption_is_preserved_and_cannot_be_overwritten(self):
        corruptions = [
            b"{broken json", b"\xff\xfe", b"[]",
            json.dumps({"version": 1, "entries": {}, "tracks": [], "volume": 50}).encode(),
            json.dumps({"version": 1, "entries": [], "tracks": [], "volume": 101}).encode(),
            json.dumps({"version": 1, "entries": [{"title": "bad"}], "tracks": [], "volume": 50}).encode(),
        ]
        self.path.parent.mkdir()
        for original in corruptions:
            with self.subTest(original=original):
                self.path.write_bytes(original)
                with self.assertRaisesRegex(ValueError, "原文件已保留"):
                    self.store.load()
                # Saving without a preceding successful load must also preserve corruption.
                with self.assertRaises(ValueError):
                    Store(self.path).save([], [], 50)
                self.assertEqual(self.path.read_bytes(), original)

    def test_failed_atomic_replace_keeps_previous_file_and_cleans_temp(self):
        self.store.save([self.entry], [self.track], 35)
        original = self.path.read_bytes()
        with patch("bili_player.models.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.store.save([], [], 80)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_invalid_volume_does_not_replace_store(self):
        self.store.save([], [], 20)
        original = self.path.read_bytes()
        for volume in (-1, 101, True, 3.5):
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                self.store.save([], [], volume)
            self.assertEqual(self.path.read_bytes(), original)

    def test_mutated_model_is_revalidated(self):
        self.track.cid = -1
        with self.assertRaises(ValueError):
            self.store.save([self.entry], [self.track], 50)
        self.assertFalse(self.path.exists())


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.session.headers = {}
        self.response = Mock()
        self.session.get.return_value = self.response
        self.client = BilibiliClient(session=self.session, timeout=7)
        self.track = Track("曲目", BVID, 202, 2)

    def reply(self, payload):
        self.response.json.return_value = copy.deepcopy(payload)

    def test_headers_and_album_page_expansion(self):
        self.reply(VIEW)
        tracks = self.client.get_tracks(LibraryEntry("专辑", BVID, kind="album"))
        self.assertEqual([track.title for track in tracks], ["第一首", "第二首"])
        self.assertEqual([track.cid for track in tracks], [101, 202])
        self.assertEqual([track.page for track in tracks], [1, 2])
        self.assertEqual([track.duration for track in tracks], [120, 180])
        self.assertTrue(all(track.artist == "" for track in tracks))
        self.assertEqual(self.session.headers, HEADERS)
        self.assertTrue(HEADERS["User-Agent"])
        self.assertEqual(HEADERS["Referer"], "https://www.bilibili.com/")
        self.session.get.assert_called_once_with(
            "https://api.bilibili.com/x/web-interface/view", params={"bvid": BVID}, timeout=7,
        )

    def test_original_single_page_uses_video_title_for_singles_and_albums(self):
        fixture = copy.deepcopy(VIEW)
        fixture["data"]["pages"] = [fixture["data"]["pages"][0]]
        fixture["data"]["pubdate"] = 1700000000
        for kind in ("single", "album"):
            with self.subTest(kind=kind):
                self.reply(fixture)
                entry = LibraryEntry("我的自定义标题", BVID, kind,
                                     artist="手填作者", date="手填日期")
                before = copy.deepcopy(entry)
                response_before = copy.deepcopy(self.response.json.return_value)
                info = self.client.get_video(entry)
                self.assertEqual(info.title, "测试专辑")
                self.assertEqual([track.title for track in info.tracks], ["测试专辑"])
                self.assertEqual((info.tracks[0].artist, info.tracks[0].date), ("", ""))
                self.assertEqual(entry, before)
                self.assertEqual(self.response.json.return_value, response_before)

    def test_multi_page_titles_survive_album_and_single_selection(self):
        for kind, expected in (("album", ["第一首", "第二首"]), ("single", ["第二首"])):
            with self.subTest(kind=kind):
                self.reply(VIEW)
                entry = LibraryEntry("我的自定义标题", BVID, kind, page=2)
                before = copy.deepcopy(entry)
                info = self.client.get_video(entry)
                self.assertEqual([track.title for track in info.tracks], expected)
                self.assertEqual(entry, before)

    def test_single_selects_page_number_not_list_index(self):
        fixture = copy.deepcopy(VIEW)
        fixture["data"]["pages"].reverse()
        self.reply(fixture)
        tracks = self.client.get_tracks(LibraryEntry("单曲", BVID, page=2))
        self.assertEqual(len(tracks), 1)
        self.assertEqual((tracks[0].cid, tracks[0].page, tracks[0].title), (202, 2, "第二首"))
        self.assertEqual(tracks[0].artist, "")

    def test_nonexistent_page_reports_error(self):
        self.reply(VIEW)
        with self.assertRaisesRegex(BilibiliError, "第 3 P"):
            self.client.get_tracks(LibraryEntry("单曲", BVID, page=3))

    def test_missing_page_title_falls_back_to_video_title(self):
        fixture = copy.deepcopy(VIEW)
        fixture["data"]["pages"][0].pop("part")
        self.reply(fixture)
        tracks = self.client.get_tracks(LibraryEntry("单曲", BVID))
        self.assertEqual(tracks[0].title, "测试专辑")

    def test_missing_or_malformed_pages_raise_readable_error(self):
        for pages in ([], None, [None], [{"page": 1}]):
            with self.subTest(pages=pages):
                self.reply({"code": 0, "data": {"pages": pages}})
                with self.assertRaises(BilibiliError):
                    self.client.get_tracks(LibraryEntry("专辑", BVID, kind="album"))

    def test_dash_audio_is_preferred_and_highest_bandwidth_selected(self):
        self.reply({"code": 0, "data": {
            "dash": {"audio": [
                {"bandwidth": 64000, "baseUrl": "https://cdn.test/low.m4a"},
                {"bandwidth": 192000, "base_url": "https://cdn.test/high.m4a"},
            ]},
            "durl": [{"url": "https://cdn.test/video.flv"}],
        }})
        self.assertEqual(self.client.get_stream(self.track), "https://cdn.test/high.m4a")
        self.session.get.assert_called_once_with(
            "https://api.bilibili.com/x/player/playurl",
            params={"bvid": BVID, "cid": 202, "fnval": 16, "fnver": 0, "fourk": 0, "qn": 64},
            timeout=7,
        )

    def test_stream_supports_backups_and_protocol_relative_urls(self):
        self.reply({"code": 0, "data": {"dash": {"audio": [
            {"backup_url": ["//cdn.test/audio.m4a"], "bandwidth": None},
        ]}}})
        self.assertEqual(self.client.get_stream(self.track), "https://cdn.test/audio.m4a")

    def test_durl_fallback_for_missing_or_unusable_dash(self):
        for dash in (None, {"audio": []}, {"audio": [{"baseUrl": "invalid"}]}):
            with self.subTest(dash=dash):
                self.reply({"code": 0, "data": {
                    "dash": dash, "durl": [{"url": "https://cdn.test/fallback.flv"}],
                }})
                self.assertEqual(self.client.get_stream(self.track), "https://cdn.test/fallback.flv")

    def test_no_stream_reports_error(self):
        self.reply({"code": 0, "data": {"dash": {"audio": None}, "durl": []}})
        with self.assertRaisesRegex(BilibiliError, "没有找到可播放"):
            self.client.get_stream(self.track)

    def test_api_error_contains_code_and_server_message(self):
        self.reply({"code": -404, "message": "视频已删除", "data": None})
        with self.assertRaisesRegex(BilibiliError, "-404.*视频已删除"):
            self.client.get_stream(self.track)

    def test_network_errors_are_translated(self):
        for error, message in (
            (requests.Timeout("timeout"), "超时"),
            (requests.ConnectionError("offline"), "连接哔哩哔哩失败"),
        ):
            with self.subTest(error=error):
                self.session.get.side_effect = error
                with self.assertRaisesRegex(BilibiliError, message):
                    self.client.get_stream(self.track)

    def test_http_error_is_translated(self):
        self.response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        with self.assertRaisesRegex(BilibiliError, "连接哔哩哔哩失败"):
            self.client.get_stream(self.track)

    def test_invalid_json_is_translated(self):
        self.response.json.side_effect = ValueError("bad JSON")
        with self.assertRaisesRegex(BilibiliError, "无效的 JSON"):
            self.client.get_stream(self.track)

    def test_malformed_api_envelopes(self):
        for payload in (
            [], {}, {"code": 0, "data": None}, {"code": 0, "data": []},
            {"code": [], "data": {}}, {"code": False, "data": {}},
        ):
            with self.subTest(payload=payload):
                self.reply(payload)
                with self.assertRaises(BilibiliError):
                    self.client.get_stream(self.track)


if __name__ == "__main__":
    unittest.main()

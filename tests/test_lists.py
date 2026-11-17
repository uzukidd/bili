"""List import regression tests without network / 空间列表导入离线回归测试。"""
from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import requests

from bili_player.api import BilibiliError, VideoInfo
from bili_player.lists import (
    BilibiliListClient, ListImportCancelled, Listing, ListSource, parse_list_url,
)
from bili_player.models import LibraryEntry, Store, Track


A, B, C = "BV1xx411c7mD", "BV1yy411c7mD", "BV1zz411c7mD"
URL = "https://space.bilibili.com/1459104794/lists/2029798"


def season_page(bvids, total, **meta):
    return {
        "meta": {"title": "List title", "cover": "//example.com/list.jpg", **meta},
        "page": {"total": total},
        "archives": [{"bvid": bvid} for bvid in bvids],
    }


def video(bvid, parts=2, cover="//example.com/video.jpg"):
    return VideoInfo("Video " + bvid, cover, [
        Track(f"Part {page}", bvid, page * 100, page, artist="Uploader", duration=page * 10)
        for page in range(1, parts + 1)
    ])


class ParseListTests(unittest.TestCase):
    def test_modern_default_and_explicit_type(self):
        source = parse_list_url(URL + "?spm_id_from=333.1387#section")
        self.assertEqual(source, ListSource(1459104794, 2029798, "auto", URL))
        for kind in ("season", "series"):
            source = parse_list_url(URL.replace("https://", "") + f"/?type={kind}")
            self.assertEqual(source.kind, kind)
            self.assertEqual(source.url, URL + f"?type={kind}")
        self.assertEqual(parse_list_url(URL.replace("https", "http")).url, URL)
        self.assertEqual(parse_list_url(URL.replace("space", "SPACE")).url, URL)

    def test_legacy_links(self):
        for route, kind in (("collectiondetail", "season"), ("seriesdetail", "series")):
            source = parse_list_url(f"https://space.bilibili.com/42/channel/{route}?sid=123")
            self.assertEqual(source, ListSource(42, 123, kind, f"https://space.bilibili.com/42/lists/123?type={kind}"))

    def test_strict_input(self):
        invalid = [
            None, 123, "", "   ", "BV1xx411c7mD", "ftp://space.bilibili.com/1/lists/2",
            "https://space.bilibili.com.evil.test/1/lists/2",
            "https://evilbilibili.com/1/lists/2", "https://www.bilibili.com/1/lists/2",
            "https://space.bilibili.com@evil.test/1/lists/2",
            "https://evil@space.bilibili.com/1/lists/2",
            "https://space.bilibili.com:443/1/lists/2", "https://space.bilibili.com:bad/1/lists/2",
            "https://space.bilibili.com/0/lists/2", "https://space.bilibili.com/1/lists/0",
            "https://space.bilibili.com/-1/lists/2", "https://space.bilibili.com/１/lists/2",
            "https://space.bilibili.com/1/lists/2/extra", "https://space.bilibili.com/1/video",
            "https://space.bilibili.com/1/lists/2?type=auto",
            "https://space.bilibili.com/1/lists/2?type=", URL + "?type=series&type=season",
            "https://space.bilibili.com/1/channel/seriesdetail?sid=2&sid=3",
            "https://space.bilibili.com/1/channel/seriesdetail?sid=2&type=season",
            "https://space.bilibili.com/1/channel/collectiondetail", URL + "\n.evil",
            "https://space.bilibili.com\\evil.test/1/lists/2",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_list_url(value)

    def test_manual_sources_validate_types(self):
        for args in ((True, 2), (1, False), (-1, 2), (1, 2, "other")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ListSource(*args)


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.client = BilibiliListClient(session=Mock())
        self.get = Mock(side_effect=AssertionError("Unexpected API request"))
        self.client._get = self.get
        self.source = parse_list_url(URL)

    def test_all_season_pages_preserve_server_order_and_deduplicate(self):
        self.get.side_effect = [season_page([B, A], 5), season_page([A, C], 5), season_page([C], 5)]
        listing = self.client.get_listing(self.source)
        self.assertEqual(listing, Listing("List title", "https://example.com/list.jpg", [B, A, C], 5, "season"))
        self.assertEqual([call.args[1]["page_num"] for call in self.get.call_args_list], [1, 2, 3])
        for call in self.get.call_args_list:
            self.assertTrue(call.args[0].endswith("seasons_archives_list"))
            self.assertEqual(call.args[1]["sort_reverse"], "false")
            self.assertEqual(call.args[1]["page_size"], 30)
            self.assertEqual(call.args[1]["mid"], self.source.mid)

    def test_auto_resolves_series_only_after_missing_season(self):
        self.get.side_effect = [
            BilibiliError("哔哩哔哩错误 -404：不存在"),
            {"meta": {"name": "Series name", "mid": self.source.mid, "series_id": self.source.list_id}},
            {"page": {"total": 3}, "archives": [{"bvid": A}, {"bvid": B}]},
            {"page": {"total": 3}, "archives": [{"bvid": C}]},
        ]
        listing = self.client.get_listing(self.source)
        self.assertEqual(listing, Listing("Series name", "", [A, B, C], 3, "series"))
        calls = self.get.call_args_list
        self.assertTrue(calls[1].args[0].endswith("/series/series"))
        self.assertEqual(calls[1].args[1], {"series_id": self.source.list_id})
        self.assertEqual([call.args[1]["pn"] for call in calls[2:]], [1, 2])
        self.assertEqual(calls[2].args[1]["only_normal"], "true")
        self.assertEqual(calls[2].args[1]["sort"], "asc")

    def test_explicit_kind_never_falls_back(self):
        for kind in ("season", "series"):
            with self.subTest(kind=kind):
                self.get.reset_mock()
                self.get.side_effect = BilibiliError("哔哩哔哩错误 -404：不存在")
                with self.assertRaises(BilibiliError):
                    self.client.get_listing(ListSource(1, 2, kind))
                self.assertEqual(self.get.call_count, 1)

    def test_throttling_and_connection_errors_never_trigger_type_fallback(self):
        for message in ("哔哩哔哩错误 -412：限制", "哔哩哔哩错误 -403：拒绝", "连接失败 HTTP 429", "请求超时", "JSON 格式不正确"):
            with self.subTest(message=message):
                self.get.reset_mock()
                self.get.side_effect = BilibiliError(message)
                with self.assertRaisesRegex(BilibiliError, message):
                    self.client.get_listing(self.source)
                self.assertEqual(self.get.call_count, 1)

    def test_missing_later_page_never_changes_recognized_kind(self):
        self.get.side_effect = [season_page([A], 2), BilibiliError("哔哩哔哩错误 -404：不存在")]
        with self.assertRaises(BilibiliError):
            self.client.get_listing(self.source)
        self.assertEqual(self.get.call_count, 2)

    def test_empty_incomplete_malformed_and_changed_lists_fail(self):
        cases = [
            [season_page([], 0)], [season_page([A], 2), season_page([], 2)],
            [season_page([A], 2), season_page([B], 3)], [season_page(["bad"], 1)],
            [season_page([A], True)], [{"page": {"total": 1}, "archives": [{"bvid": A}]}],
            [season_page([A], 1, mid=99)], [season_page([A], 1, season_id=99)],
            [season_page([A], 1, title=123)],
        ]
        for responses in cases:
            with self.subTest(responses=responses), self.assertRaises(BilibiliError):
                self.get.side_effect = responses
                self.client.get_listing(self.source)

    def test_series_metadata_owner_is_required_to_match(self):
        self.get.side_effect = [{"meta": {"name": "Wrong owner", "mid": 99}}]
        with self.assertRaisesRegex(BilibiliError, "不一致"):
            self.client.get_listing(ListSource(1, 2, "series"))
        self.assertEqual(self.get.call_count, 1)

    def test_cancel_before_first_request_and_between_pages(self):
        with self.assertRaises(ListImportCancelled):
            self.client.get_listing(self.source, cancelled=lambda: True)
        self.get.assert_not_called()
        flag = [False]
        def response(*_args):
            flag[0] = True
            return season_page([A], 2)
        self.get.side_effect = response
        with self.assertRaises(ListImportCancelled):
            self.client.get_listing(self.source, cancelled=lambda: flag[0])
        self.assertEqual(self.get.call_count, 1)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.client = BilibiliListClient(session=Mock())
        self.source = parse_list_url(URL)
        self.listing = Listing("Album title", "https://example.com/list.jpg", [A, B, C], 3, "season")
        self.client.get_listing = Mock(return_value=self.listing)
        self.videos = {bvid: video(bvid) for bvid in (A, B, C)}
        self.client.get_video = Mock(side_effect=lambda entry: self.videos[entry.bvid])

    def test_all_parts_independent_ids_blank_artists_and_listing_cover(self):
        before = deepcopy(self.videos)
        progress = Mock()
        result = self.client.import_album(self.source, progress=progress)
        self.assertEqual(result.total, 3)
        self.assertEqual(result.skipped, [])
        album = result.album
        self.assertEqual((album.title, album.kind, album.bvid, album.resolved), ("Album title", "album", "", True))
        self.assertEqual(album.cover, self.listing.cover)
        self.assertEqual([(track.bvid, track.page) for track in album.tracks], [(bvid, page) for bvid in (A, B, C) for page in (1, 2)])
        self.assertTrue(all(track.artist == "" for track in album.tracks))
        originals = {track.id for info in self.videos.values() for track in info.tracks}
        self.assertEqual(len({track.id for track in album.tracks}), 6)
        self.assertTrue(originals.isdisjoint(track.id for track in album.tracks))
        self.assertEqual(self.videos, before)
        self.assertEqual([call.args for call in progress.call_args_list], [(0, 3, ""), (1, 3, A), (2, 3, B), (3, 3, C)])
        self.assertTrue(all(call.args[0].kind == "album" for call in self.client.get_video.call_args_list))

    def test_first_part_only_uses_video_title_and_first_successful_cover(self):
        self.listing.cover = ""
        self.videos[A].cover = ""
        result = self.client.import_album(self.source, expand_parts=False)
        self.assertEqual([track.page for track in result.album.tracks], [1, 1, 1])
        self.assertEqual([track.title for track in result.album.tracks], ["Video " + bvid for bvid in (A, B, C)])
        self.assertEqual(result.album.cover, "https://example.com/video.jpg")
        self.assertTrue(all(call.args[0].kind == "single" for call in self.client.get_video.call_args_list))
        self.assertTrue(all(call.args[0].page == 1 for call in self.client.get_video.call_args_list))

    def test_deleted_private_and_incomplete_videos_are_skipped_with_bv_notes(self):
        for message in ("哔哩哔哩错误 -404：已删除", "哔哩哔哩错误 62012：仅作者可见", "哔哩哔哩错误 -101：需要登录", "视频分 P 数据不完整：CID", "视频没有可播放的分 P"):
            with self.subTest(message=message):
                self.client.get_video.side_effect = [BilibiliError(message), self.videos[B], self.videos[C]]
                result = self.client.import_album(self.source)
                self.assertEqual(len(result.skipped), 1)
                self.assertIn(A, result.skipped[0])
                self.assertIn(message, result.skipped[0])
                self.assertEqual([track.bvid for track in result.album.tracks], [B, B, C, C])

    def test_throttle_or_network_after_success_aborts_without_return_or_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            store = Store(path)
            store.save([LibraryEntry("Original", A)], [], 70)
            original = path.read_bytes()
            for message in ("哔哩哔哩错误 -412：稍后重试", "连接失败 HTTP 429", "连接失败 HTTP 412", "请求超时", "哔哩哔哩错误 -403：拒绝", "哔哩哔哩返回了无效的 JSON 数据", "哔哩哔哩错误 -500：内部错误"):
                with self.subTest(message=message):
                    self.client.get_video.reset_mock()
                    self.client.get_video.side_effect = [self.videos[A], BilibiliError(message), self.videos[C]]
                    with self.assertRaisesRegex(BilibiliError, "未保存专辑"):
                        self.client.import_album(self.source)
                    self.assertEqual(self.client.get_video.call_count, 2)
                    self.assertEqual(path.read_bytes(), original)

    def test_actual_base_http_failures_are_not_silently_skipped(self):
        for status in (412, 429, 500):
            with self.subTest(status=status):
                response = Mock()
                response.status_code = status
                response.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}", response=response)
                session = Mock()
                session.get.return_value = response
                client = BilibiliListClient(session=session)
                client.get_listing = Mock(return_value=self.listing)
                with self.assertRaisesRegex(BilibiliError, str(status)):
                    client.import_album(self.source)
                self.assertEqual(session.get.call_count, 1)

    def test_no_playable_videos_fails_with_notes(self):
        self.client.get_video.side_effect = BilibiliError("哔哩哔哩错误 -404：已删除")
        with self.assertRaisesRegex(BilibiliError, "没有可播放的曲目") as caught:
            self.client.import_album(self.source)
        self.assertIn(A, str(caught.exception))
        self.assertIn(C, str(caught.exception))

    def test_cancel_in_progress_callback_prevents_next_request(self):
        cancelled = [False]
        calls = []
        def progress(done, total, bvid):
            calls.append((done, total, bvid))
            if done == 1:
                cancelled[0] = True
        with self.assertRaises(ListImportCancelled):
            self.client.import_album(self.source, progress=progress, cancelled=lambda: cancelled[0])
        self.assertEqual(self.client.get_video.call_count, 1)
        self.assertEqual(calls, [(0, 3, ""), (1, 3, A)])

    def test_cancel_initial_progress_prevents_all_video_requests(self):
        flag = [False]
        def progress(*_args):
            flag[0] = True
        with self.assertRaises(ListImportCancelled):
            self.client.import_album(self.source, progress=progress, cancelled=lambda: flag[0])
        self.client.get_video.assert_not_called()

    def test_cancel_during_video_request_prevents_completion_and_more_requests(self):
        flag = [False]
        def get_video(_entry):
            flag[0] = True
            return self.videos[A]
        self.client.get_video.side_effect = get_video
        progress = Mock()
        with self.assertRaises(ListImportCancelled):
            self.client.import_album(self.source, progress=progress, cancelled=lambda: flag[0])
        self.assertEqual(self.client.get_video.call_count, 1)
        self.assertEqual(progress.call_count, 1)

    def test_completed_snapshot_roundtrip_preserves_shared_songs_and_folder_order(self):
        album = self.client.import_album(self.source).album
        other = LibraryEntry("Other folder", kind="album", resolved=True,
                             tracks=deepcopy([album.tracks[3], album.tracks[0], album.tracks[4]]))
        expected_order = [[(track.bvid, track.page) for track in folder.tracks]
                          for folder in (album, other)]
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "review.json")
            entries = [album, other]
            store.save(entries, [], 70)
            for _roundtrip in range(2):
                restored, queue, volume = store.load()
                folders = [entry for entry in restored if entry.kind == "album"]
                songs = [entry for entry in restored if entry.kind == "single"]
                self.assertEqual(restored, entries)
                self.assertEqual([folder.id for folder in folders], [album.id, other.id])
                self.assertEqual([[(track.bvid, track.page) for track in folder.tracks]
                                  for folder in folders], expected_order)
                self.assertEqual(len(songs), 6)
                self.assertEqual(len({(song.bvid, song.page) for song in songs}), 6)
                by_id = {song.id: song for song in songs}
                for folder in folders:
                    for track in folder.tracks:
                        self.assertEqual(track.id, track.song_id)
                        self.assertIs(track, by_id[track.song_id].tracks[0])
                for first_index, second_index in ((3, 0), (0, 1), (4, 2)):
                    self.assertIs(folders[0].tracks[first_index], folders[1].tracks[second_index])
                self.assertEqual((queue, volume), ([], 70))
                store.save(restored, queue, volume)

    def test_invalid_expand_option_does_not_make_requests(self):
        with self.assertRaises(ValueError):
            self.client.import_album(self.source, expand_parts="yes")
        self.client.get_listing.assert_not_called()


if __name__ == "__main__":
    unittest.main()

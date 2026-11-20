import json
import tempfile
import unittest
from email.utils import formatdate
from pathlib import Path
from unittest.mock import patch

import requests

from bili_player.lyrics import (
    LRCLIBClient, LyricsCooldownError, LyricsEntry, LyricsError, LyricsLine, LyricsStore, parse_lrc,
)


def response(data=None, *, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


class LyricsStoreTests(unittest.TestCase):
    def test_persists_independent_song_entries_and_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "lyrics.json"
            store = LyricsStore(path)
            first = LyricsEntry("local:1", "原版", plain_lyrics="你好\n世界")
            second = LyricsEntry("lrclib:1", "另一个版本", synced_lyrics="[00:01.20]Hello", source="lrclib")
            self.assertIsNone(store.load("song-a").active)
            store.save("song-a", first)
            self.assertEqual(store.save("song-a", second, select=False).active, first)
            store.save("song-b", second)
            store.set_active("song-a", second.id)

            reopened = LyricsStore(path)
            self.assertEqual(reopened.load("song-a").entries, (first, second))
            self.assertEqual(reopened.load("song-a").active, second)
            self.assertEqual(reopened.load("song-b").entries, (second,))
            revision = LyricsEntry(second.id, "Updated", plain_lyrics="new words")
            self.assertEqual(reopened.save("song-a", revision).entries, (first, revision))
            self.assertEqual(reopened.remove("song-a", revision.id).active, first)
            reopened.set_active("song-a", None)
            self.assertIsNone(LyricsStore(path).load("song-a").active)
            self.assertFalse(reopened.remove("song-a", first.id).entries)

    def test_failed_replace_preserves_file_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lyrics.json"
            store = LyricsStore(path)
            first = LyricsEntry("local:1", "Original")
            store.save("song", first)
            with patch("bili_player.lyrics.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    store.save("song", LyricsEntry("local:2", "New"))
            self.assertEqual(store.load("song").entries, (first,))
            self.assertEqual(list(path.parent.iterdir()), [path])


class LyricsDisplayTests(unittest.TestCase):
    def test_signed_offset_repeated_cues_and_lookup(self):
        display = parse_lrc(
            "\ufeff[ar: Artist]\r\n[00:02.3][00:04.567] <00:02.31>重复\r\n"
            "[00:00.03]First\r\n[00:03]\r\n[offset:+50]\r\n[offset:invalid]"
        )
        self.assertTrue(display.synced)
        self.assertEqual(display.lines, (
            LyricsLine("First", -20), LyricsLine("重复", 2250),
            LyricsLine("", 2950), LyricsLine("重复", 4517),
        ))
        self.assertEqual([display.line_index(ms) for ms in (-30, 0, 2249, 2250, 2950, 6000)],
                         [-1, 0, 0, 1, 2, 3])
        self.assertEqual(parse_lrc("[offset:-500]\n[00:01]Later").lines, (LyricsLine("Later", 1500),))

    def test_plain_fallback_and_preview_limits(self):
        entry = LyricsEntry("1", "Title", "<b>Plain</b>\n\nNext", "[ti:Title]\n[00:03]")
        self.assertEqual(entry.display().lines, (LyricsLine("<b>Plain</b>"), LyricsLine(""), LyricsLine("Next")))
        self.assertEqual(entry.display().line_index(3000), -1)
        self.assertEqual(entry.preview(max_lines=1), "<b>Plain</b>…")
        self.assertEqual(entry.preview(max_chars=5), "<b>P…")
        self.assertEqual(parse_lrc("[ti:Title]\nNo timestamps").lines, (LyricsLine("No timestamps"),))


class LRCLIBClientTests(unittest.TestCase):
    def test_lightweight_search_and_selected_get(self):
        search = response([
            {"id": True},
            {"id": 1, "trackName": "歌", "artistName": "作者", "duration": 123,
             "hasSyncedLyrics": True, "hasPlainLyrics": False},
            {"id": 2, "trackName": "Other"},
        ])
        selected = response({"id": 1, "trackName": "歌", "artistName": "作者",
                             "plainLyrics": None, "syncedLyrics": "[00:00]Lyrics"})
        client = LRCLIBClient()
        with patch("bili_player.lyrics.network.get", side_effect=[search, selected]) as get:
            self.assertEqual(client.search(artist_name="作者"), [])
            results = client.search(track_name=" 歌 ", artist_name="作者", limit=1)
            self.assertEqual(len(results), 1)
            self.assertEqual((results[0].id, results[0].title, results[0].duration), (1, "歌", 123.0))
            self.assertIs(results[0].has_synced_lyrics, True)
            self.assertEqual(get.call_count, 1)
            search_call = get.call_args
            self.assertEqual(search_call.args, ("https://lrclib.net/api/v2/search",))
            self.assertEqual(search_call.kwargs["params"], {"track_name": "歌", "artist_name": "作者"})
            self.assertIn("https://github.com/uzukidd/bili", search_call.kwargs["headers"]["User-Agent"])
            with patch("bili_player.lyrics.time.sleep") as sleep:
                entry = client.get(results[0].id)
                sleep.assert_called_once()
            self.assertEqual(get.call_args.args, ("https://lrclib.net/api/get/1",))
            self.assertEqual((entry.id, entry.source, entry.preview()), ("lrclib:1", "lrclib", "Lyrics"))

    def test_cooldown_blocks_search_and_get_until_retry_after(self):
        for status, retry_after in ((429, "12"), (503, formatdate(1012, usegmt=True))):
            with (
                self.subTest(status=status),
                patch("bili_player.lyrics.time.monotonic", return_value=100) as clock,
                patch("bili_player.lyrics.time.time", return_value=1000),
                patch("bili_player.lyrics.random.uniform", return_value=0.5),
                patch("bili_player.lyrics.network.get", side_effect=[
                    response(status=status, headers={"Retry-After": retry_after}), response([]),
                ]) as get,
            ):
                client = LRCLIBClient()
                with self.assertRaises(LyricsCooldownError) as failure:
                    client.search("test")
                self.assertEqual(failure.exception.retry_after, 12.5)
                clock.return_value = 112
                with self.assertRaises(LyricsCooldownError) as failure:
                    client.get(1)
                self.assertEqual(failure.exception.retry_after, 0.5)
                self.assertEqual(get.call_count, 1)
                clock.return_value = 112.5
                self.assertEqual(client.search("next"), [])
                self.assertEqual(get.call_count, 2)

    def test_oversized_payload_is_rejected(self):
        with patch("bili_player.lyrics.network.get", return_value=response("a" * (256 * 1024))):
            with self.assertRaisesRegex(LyricsError, "size limit"):
                LRCLIBClient().search("test")


if __name__ == "__main__":
    unittest.main()

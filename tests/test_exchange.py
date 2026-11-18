from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from bili_player.exchange import dumps_library, loads_library, read_library, write_library
from bili_player.models import LibraryEntry, Track


BVID = "BV1xx411c7mD"
OTHER_BVID = "BV1yy411c7mD"


def document(entries, **overrides):
    payload = {"format": "bili-music-library", "version": 1, "entries": entries}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.first = Track("第一首", BVID, 101, 1, "手填作者 / Artist", 31, "first-track", "春天（未确定）")
        self.second = Track("第二首", OTHER_BVID, 202, 2, "", 47, "second-track", "2026 年")
        self.album = LibraryEntry(
            "中文专辑", BVID, "album", 2, "album-entry", [self.first, self.second],
            "https://images.example/cover.jpg?size=200", True, "专辑手填作者", "任意日期文字",
        )
        self.single = LibraryEntry("单曲", OTHER_BVID, tracks=[self.second], resolved=True,
                                   id="single-entry", artist="单曲作者", date="未知")

    def test_roundtrip_preserves_metadata_order_empty_albums_and_unresolved_entries(self):
        from bili_player.catalog import normalize_library

        entries = [self.album, self.single, LibraryEntry("待解析", "BV1zz411c7mD"),
                   LibraryEntry("空专辑", kind="album", resolved=True)]
        before = copy.deepcopy(entries)
        expected = normalize_library(copy.deepcopy(entries))
        text = dumps_library(entries)
        self.assertIn("中文专辑", text)
        payload = json.loads(text)
        self.assertEqual(set(payload), {"format", "version", "entries"})
        self.assertEqual((payload["format"], payload["version"]), ("bili-music-library", 3))
        for item in payload["entries"]:
            if item["kind"] == "album":
                self.assertNotIn("tracks", item)
                self.assertIn("song_ids", item)
        imported = loads_library(text)
        self.assertEqual(len(imported), len(expected))
        for source, restored in zip(expected, imported):
            for field in ("title", "bvid", "kind", "page", "cover", "resolved", "artist", "date"):
                self.assertEqual(getattr(restored, field), getattr(source, field))
            self.assertEqual(
                [(track.title, track.bvid, track.cid, track.page, track.artist, track.duration, track.date)
                 for track in restored.tracks],
                [(track.title, track.bvid, track.cid, track.page, track.artist, track.duration, track.date)
                 for track in source.tracks],
            )
        unresolved = next(entry for entry in imported if entry.title == "待解析")
        self.assertFalse(unresolved.resolved)
        empty = next(entry for entry in imported if entry.title == "空专辑")
        self.assertEqual((empty.tracks, empty.resolved), ([], True))
        self.assertEqual(entries, before)

    def test_ids_preserve_shared_songs_and_import_objects_are_independent(self):
        another_album = copy.deepcopy(self.album)
        another_album.id = "second-album"
        text = dumps_library([self.album, another_album, self.single])
        first, second = loads_library(text), loads_library(text)
        identifiers = [entry.id for entry in first]
        self.assertEqual(identifiers, [entry.id for entry in second])
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertIn("album-entry", identifiers)
        self.assertIn("single-entry", identifiers)
        albums = [entry for entry in first if entry.kind == "album"]
        singles = [entry for entry in first if entry.kind == "single"]
        self.assertEqual((len(albums), len(singles)), (2, 2))
        self.assertIs(albums[0].tracks[0], albums[1].tracks[0])
        canonical = next(entry for entry in singles if entry.id == albums[0].tracks[0].song_id)
        self.assertIs(albums[0].tracks[0], canonical.tracks[0])
        self.assertEqual((canonical.tracks[0].id, canonical.tracks[0].song_id), (canonical.id, canonical.id))
        albums[0].tracks[0].title = "共享修改"
        self.assertEqual(albums[1].tracks[0].title, "共享修改")
        other_album = next(entry for entry in second if entry.kind == "album")
        self.assertEqual(other_album.tracks[0].title, "第一首")

    def test_covers_are_url_only_in_both_directions_without_file_or_network_access(self):
        cases = {
            "http://images.example/a.jpg": "http://images.example/a.jpg",
            "https://images.example:8443/a.jpg?q=中文": "https://images.example:8443/a.jpg?q=中文",
            "//images.example/a.jpg": "https://images.example/a.jpg",
            " https://images.example/a.jpg ": "https://images.example/a.jpg",
            "https://[::1]:443/a.jpg": "https://[::1]:443/a.jpg",
            "": "", r"C:\private\cover.jpg": "", "/private/cover.jpg": "",
            "file:///private/cover.jpg": "", "data:image/png;base64,abc": "",
            "ftp://images.example/a.jpg": "", "https:///a.jpg": "",
            "https://user:secret@images.example/a.jpg": "", "https://@images.example/a.jpg": "",
            "https://images.example:bad/a.jpg": "", "https://images.example:99999/a.jpg": "",
            "https://[broken/a.jpg": "", "https://images.example/a\nb.jpg": "",
            "https://images.example\\a.jpg": "", "https://./a.jpg": "",
        }
        before = asdict(self.album)
        with patch("pathlib.Path.open", side_effect=AssertionError("unexpected file access")), \
             patch("builtins.open", side_effect=AssertionError("unexpected file access")), \
             patch("socket.socket", side_effect=AssertionError("unexpected network access")):
            for original, expected in cases.items():
                with self.subTest(cover=original):
                    entry = copy.deepcopy(self.album)
                    entry.cover = original
                    payload = json.loads(dumps_library([entry]))
                    self.assertEqual(payload["entries"][0]["cover"], expected)
                    data = asdict(entry)
                    imported = loads_library(document([data]))
                    self.assertEqual(imported[0].cover, expected)
                    self.assertEqual(entry.cover, original)
        self.assertEqual(asdict(self.album), before)

    def test_store_v1_v2_snapshots_extract_only_entries_and_default_old_fields(self):
        legacy = {"title": "旧专辑", "bvid": BVID, "kind": "album", "id": "legacy"}
        for version in (1, 2):
            with self.subTest(version=version):
                payload = {"version": version, "entries": [legacy], "tracks": "invalid ignored queue",
                           "volume": -500, "settings": {"theme": "invalid ignored theme"}}
                imported = loads_library(json.dumps(payload))[0]
                self.assertEqual((imported.artist, imported.date, imported.cover, imported.tracks), ("", "", "", []))
                self.assertFalse(imported.resolved)
                self.assertEqual(imported.id, "legacy")

    def test_old_nested_tracks_default_optional_metadata(self):
        legacy = {"title": "旧专辑", "bvid": BVID, "kind": "album", "resolved": True,
                  "tracks": [{"title": "旧曲目", "bvid": BVID, "cid": 101, "page": 1}]}
        track = loads_library(document([legacy]))[0].tracks[0]
        self.assertEqual((track.artist, track.date, track.duration), ("", "", 0))

    def test_blank_bvid_albums_become_resolved_without_mutating_export_source(self):
        entry = LibraryEntry("本地整理的空专辑", kind="album")
        self.assertFalse(entry.resolved)
        text = dumps_library([entry])
        self.assertFalse(entry.resolved)
        self.assertTrue(loads_library(text)[0].resolved)
        for version in (1, 2):
            payload = {"version": version, "entries": [{"title": "空专辑", "kind": "album"}]}
            self.assertTrue(loads_library(json.dumps(payload))[0].resolved)

    def test_empty_library_and_bom_text(self):
        self.assertEqual(loads_library(dumps_library([])), [])
        self.assertEqual(loads_library("\ufeff" + dumps_library([])), [])

    def test_bad_payloads_versions_and_formats_have_chinese_errors(self):
        bad = [None, b"{}", "", "{", "[]", "null", "true", document([], version=True),
               document([], version=1.0), document([], version="1"), document([], version=4),
               document([], version=99), document([], format="other"), document([], format=None),
               json.dumps({"version": 5, "entries": []}), json.dumps({"entries": []}),
               document(None), document({}), document([None]), document([[]])]
        for text in bad:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "[\u4e00-\u9fff]"):
                loads_library(text)

    def test_bad_fields_reject_entire_payload_after_valid_first_entry(self):
        bad = [
            {"title": None}, {"title": 123}, {"artist": []}, {"date": False},
            {"id": ""}, {"id": 1}, {"bvid": "bad"}, {"kind": []}, {"kind": "playlist"},
            {"page": True}, {"page": 0}, {"cover": None}, {"resolved": 1}, {"tracks": {}},
            {"tracks": [True]}, {"tracks": [asdict(self.first), {"title": "missing fields"}]},
            {"extra": "unknown"},
        ]
        for overrides in bad:
            malformed = asdict(self.album)
            malformed.update(overrides)
            with self.subTest(overrides=overrides), \
                 patch("bili_player.catalog.uuid4") as new_id, \
                 self.assertRaisesRegex(ValueError, "[\u4e00-\u9fff]"):
                loads_library(document([asdict(self.single), malformed]))
            new_id.assert_not_called()

    def test_nested_track_fields_are_strictly_validated(self):
        for overrides in ({"title": False}, {"artist": None}, {"date": 12}, {"bvid": ""},
                          {"cid": True}, {"cid": 0}, {"page": False}, {"duration": -1},
                          {"duration": 2.5}, {"id": None}, {"unexpected": "field"}):
            malformed = asdict(self.album)
            malformed["tracks"][1].update(overrides)
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "[\u4e00-\u9fff]"):
                loads_library(document([malformed]))

    def test_export_revalidates_mutated_models_without_changing_them(self):
        for field, value in (("title", None), ("artist", 12), ("date", []), ("page", True),
                             ("resolved", "yes"), ("cover", None), ("tracks", [asdict(self.first)])):
            entry = copy.deepcopy(self.album)
            setattr(entry, field, value)
            before = copy.deepcopy(entry)
            with self.subTest(field=field), self.assertRaises(ValueError):
                dumps_library([self.single, entry])
            self.assertEqual(entry, before)
        for field, value in (("artist", None), ("date", False), ("cid", -1), ("duration", True)):
            entry = copy.deepcopy(self.album)
            setattr(entry.tracks[0], field, value)
            with self.subTest(track_field=field), self.assertRaises(ValueError):
                dumps_library([entry])
        for entries in (None, (), {}, [asdict(self.album)], [self.album, self.first]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                dumps_library(entries)


    def test_one_song_shared_by_two_folders_roundtrips_without_embedded_copies(self):
        song = LibraryEntry("单曲名称", BVID, id="song", tracks=[self.first],
                            artist="手填作者 / Artist", date="任意日期：春天", resolved=True)
        folders = [LibraryEntry("文件夹一", kind="album", id="folder-one", tracks=[self.first]),
                   LibraryEntry("文件夹二", kind="album", id="folder-two", tracks=[self.first])]
        entries = [*folders, song]
        before = copy.deepcopy(entries)
        payload = json.loads(dumps_library(entries))
        self.assertEqual(len(payload["entries"]), 3)
        serialized_song = next(item for item in payload["entries"] if item["kind"] == "single")
        self.assertEqual(len(serialized_song["tracks"]), 1)
        for item in payload["entries"][:2]:
            self.assertEqual(item["song_ids"], [serialized_song["id"]])
            self.assertNotIn("tracks", item)
        with patch("bili_player.catalog.uuid4") as new_id:
            imported = loads_library(json.dumps(payload))
        new_id.assert_not_called()
        restored_song = next(entry for entry in imported if entry.kind == "single")
        self.assertEqual(restored_song.id, "song")
        self.assertEqual((restored_song.tracks[0].id, restored_song.tracks[0].song_id),
                         (restored_song.id, restored_song.id))
        self.assertEqual((restored_song.artist, restored_song.date),
                         ("手填作者 / Artist", "任意日期：春天"))
        for folder in imported[:2]:
            self.assertIs(folder.tracks[0], restored_song.tracks[0])
        exported_again = json.loads(dumps_library(imported))
        for item in exported_again["entries"][:2]:
            self.assertEqual(item["song_ids"], [restored_song.id])
        self.assertEqual(entries, before)
        self.assertIs(entries[0].tracks[0], entries[1].tracks[0])

    def test_legacy_duplicate_sources_prefer_explicit_single_and_keep_both_folders(self):
        album_copy = copy.deepcopy(self.album)
        album_copy.id = "other-folder"
        duplicate = copy.deepcopy(self.single)
        duplicate.id = "duplicate-song"
        duplicate.title = "后来的名称"
        duplicate.artist = "后来的作者"
        entries = [asdict(self.album), asdict(album_copy), asdict(self.single), asdict(duplicate)]
        for version in (1, 2):
            with self.subTest(version=version):
                imported = loads_library(json.dumps({"version": version, "entries": entries}))
                singles = [entry for entry in imported if entry.kind == "single"]
                albums = [entry for entry in imported if entry.kind == "album"]
                self.assertEqual((len(singles), len(albums)), (2, 2))
                song = next(entry for entry in singles if entry.bvid == OTHER_BVID)
                self.assertEqual((song.title, song.artist, song.date), ("单曲", "单曲作者", "未知"))
                self.assertIs(albums[0].tracks[1], song.tracks[0])
                self.assertIs(albums[1].tracks[1], song.tracks[0])

    def test_store_v3_ignores_queue_settings_and_preserves_graph(self):
        payload = json.loads(dumps_library([self.album, self.single]))
        payload.pop("format")
        payload.update(version=3, tracks={"invalid": "ignored queue"}, volume=False,
                       settings={"theme": "invalid ignored theme"})
        imported = loads_library(json.dumps(payload))
        self.assertEqual(len(imported), 3)
        album = next(entry for entry in imported if entry.kind == "album")
        songs = {entry.id: entry for entry in imported if entry.kind == "single"}
        for track in album.tracks:
            self.assertIs(track, songs[track.song_id].tracks[0])

    def test_dangling_or_invalid_normalized_references_reject_whole_document(self):
        valid = json.loads(dumps_library([self.album, self.single]))
        album = next(item for item in valid["entries"] if item["kind"] == "album")
        for replacement in (["missing-song"], [album["id"]], [None], "not a list",
                            album["song_ids"] * 2):
            malformed = copy.deepcopy(valid)
            next(item for item in malformed["entries"] if item["kind"] == "album")["song_ids"] = replacement
            with self.subTest(refs=replacement), patch("bili_player.catalog.uuid4") as new_id, \
                 self.assertRaisesRegex(ValueError, "[\u4e00-\u9fff]"):
                loads_library(json.dumps(malformed))
            new_id.assert_not_called()
        malformed = copy.deepcopy(valid)
        next(item for item in malformed["entries"] if item["kind"] == "album")["tracks"] = []
        with self.assertRaises(ValueError):
            loads_library(json.dumps(malformed))


class ExchangeFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "音乐库.json"
        self.entry = LibraryEntry("歌曲", BVID, artist="作者", date="任意日期")

    def test_atomic_roundtrip_utf8_existing_overwrite_and_empty_library(self):
        self.path.write_bytes(b"existing nonempty file")
        write_library(self.path, [self.entry])
        raw = self.path.read_bytes()
        self.assertIn("歌曲", raw.decode("utf-8"))
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        imported = read_library(self.path)
        self.assertEqual((imported[0].title, imported[0].artist, imported[0].date), ("歌曲", "作者", "任意日期"))
        self.assertEqual(imported[0].id, self.entry.id)
        write_library(self.path, [])
        self.assertEqual(read_library(self.path), [])
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_bom_is_accepted_and_import_never_rewrites_source(self):
        original = ("\ufeff" + dumps_library([self.entry])).encode("utf-8")
        self.path.write_bytes(original)
        self.assertEqual(read_library(self.path)[0].title, "歌曲")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_read_errors_are_value_errors_and_files_are_untouched(self):
        for original in (b"\xff", b"{broken", b"[]"):
            with self.subTest(original=original):
                self.path.write_bytes(original)
                with self.assertRaisesRegex(ValueError, "[\u4e00-\u9fff]"):
                    read_library(self.path)
                self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, "无法读取"):
            read_library(self.directory / "missing.json")
        with self.assertRaisesRegex(ValueError, "无法读取"):
            read_library(self.directory)

    def test_read_size_is_bounded_and_oversized_input_rejected(self):
        raw = io.BytesIO(b" " * 1025)
        with patch("bili_player.exchange._MAX_FILE_BYTES", 1024), \
             patch("pathlib.Path.open", return_value=raw):
            with self.assertRaisesRegex(ValueError, "文件过大"):
                read_library(self.path)
        # The configured production bound stays at 16 MiB.
        from bili_player.exchange import _MAX_FILE_BYTES
        self.assertEqual(_MAX_FILE_BYTES, 16 * 1024 * 1024)

    def test_replace_failure_preserves_existing_nonempty_file_and_cleans_temporary(self):
        self.path.write_bytes(b"preserve this existing nonempty file")
        original = self.path.read_bytes()

        def failed_replace(source, destination):
            temporary = Path(source)
            self.assertEqual(temporary.parent, self.path.parent)
            self.assertEqual(Path(destination), self.path)
            self.assertEqual(loads_library(temporary.read_text(encoding="utf-8"))[0].title, "歌曲")
            raise OSError("replacement failed")

        with patch("bili_player.exchange.os.replace", side_effect=failed_replace):
            with self.assertRaisesRegex(ValueError, "无法写入"):
                write_library(self.path, [self.entry])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_fsync_failure_preserves_original_and_cleans_temporary(self):
        self.path.write_bytes(b"original")
        with patch("bili_player.exchange.os.fsync", side_effect=OSError("flush failed")), \
             patch("bili_player.exchange.os.replace") as replace:
            with self.assertRaisesRegex(ValueError, "无法写入"):
                write_library(self.path, [])
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"original")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_validation_failure_leaves_existing_file_untouched(self):
        self.path.write_bytes(b"original")
        self.entry.date = False
        with patch("bili_player.exchange.tempfile.NamedTemporaryFile") as temporary:
            with self.assertRaises(ValueError):
                write_library(self.path, [self.entry])
        temporary.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"original")

    def test_missing_parent_reports_error_without_creating_directories(self):
        parent = self.directory / "missing"
        with self.assertRaisesRegex(ValueError, "无法写入"):
            write_library(parent / "library.json", [])
        self.assertFalse(parent.exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from bili_player.catalog import decode_library, encode_library, normalize_library
from bili_player.dialogs import AlbumDialog, EntryDialog, TrackDialog
from bili_player.models import LibraryEntry, Store, Track, normalize_youtube_id, video_url


BV = "BV1xx411c7mD"
YT = "Dq_6j5m-0aQ"
OTHER_YT = "dQw4w9WgXcQ"


def youtube_track() -> Track:
    return Track("YouTube song", YT, 0, 1, duration=60, provider="youtube")


class YouTubeModelTests(unittest.TestCase):
    def test_normalizes_only_supported_video_urls(self):
        for value in (
            f" {YT} ", f"https://www.youtube.com/watch?v={YT}&list=anything#t=20",
            f"www.youtube.com/watch?si=anything&v={YT}", f"https://youtu.be/{YT}?t=5",
            f"https://m.youtube.com/shorts/{YT}", f"https://youtube.com/live/{YT}",
            f"https://www.youtube.com/embed/{YT}", f"https://www.youtube-nocookie.com/embed/{YT}",
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_youtube_id(value), YT)
        for value in (
            None, "", BV, "ABCDEFGHIJ!", f"https://evil.test/watch?v={YT}",
            f"https://www.youtube.com.evil.test/watch?v={YT}",
            f"https://evil.test@www.youtube.com/watch?v={YT}",
            f"https://www.youtube.com@evil.test/watch?v={YT}",
            f"https://www.youtube.com:8080/watch?v={YT}", f"ftp://youtu.be/{YT}",
            f"https://you\ntu.be/{YT}", f"https://youtu.be/{YT}/extra",
            f"https://www.youtube.com/channel/{YT}", f"https://www.youtube.com/playlist?v={YT}",
            f"https://www.youtube.com/watch?v={YT}&v={OTHER_YT}",
            f"https://www.youtube.com/watch?v={YT}&v=", f"https://www.youtube.com/watch#v={YT}",
            f"https://www.youtube.com/redirect?q=https://youtu.be/{YT}",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_youtube_id(value)

    def test_model_defaults_constraints_and_watch_urls(self):
        bili = Track("Bili", BV, 9, 2, "Artist", 20, "track", "Date", "song")
        self.assertEqual((bili.provider, bili.song_id), ("bilibili", "song"))
        self.assertEqual(bili.video_url, f"https://www.bilibili.com/video/{BV}/")
        yt = youtube_track()
        self.assertEqual(yt.video_url, f"https://www.youtube.com/watch?v={YT}")
        entry = LibraryEntry("YouTube song", f"https://youtu.be/{YT}", provider="youtube")
        self.assertEqual(entry.bvid, YT)
        self.assertEqual(entry.video_url, video_url("youtube", YT))
        self.assertEqual(LibraryEntry("Folder", kind="album").video_url, "")
        for invalid in ({"cid": 1}, {"cid": False}, {"page": 2}, {"provider": "unknown"}, {"bvid": BV}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replace(yt, **invalid)
        for invalid in ({"kind": "album"}, {"page": 2}, {"provider": "unknown"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replace(entry, **invalid)

    def test_round_trip_mixed_album_and_queue(self):
        bili = Track("Bili", BV, 9, 1)
        yt = youtube_track()
        album = LibraryEntry("Mixed", kind="album", tracks=[bili, yt], resolved=True)
        entries = normalize_library([album])
        with TemporaryDirectory() as root:
            store = Store(Path(root) / "library.json")
            store.save(entries, [replace(yt, id="queue-entry")], 70)
            decoded, queue, _ = store.load()
        album, bili_song, yt_song = decoded
        self.assertEqual((album.provider, bili_song.provider, yt_song.provider),
                         ("bilibili", "bilibili", "youtube"))
        self.assertIs(album.tracks[0], bili_song.tracks[0])
        self.assertIs(album.tracks[1], yt_song.tracks[0])
        self.assertEqual((queue[0].provider, queue[0].song_id), ("youtube", yt_song.id))

    def test_v4_rejects_an_owner_track_with_a_different_source(self):
        entry = LibraryEntry("YouTube song", YT, tracks=[youtube_track()], provider="youtube")
        document = encode_library([entry])
        document["entries"][0]["tracks"][0].update(bvid=BV, cid=9, provider="bilibili")
        with self.assertRaisesRegex(ValueError, "来源不一致"):
            decode_library(document)

    def test_legacy_migration_defaults_bilibili_and_links_queue_by_provider(self):
        bili = asdict(Track("Bili", BV, 9, 1, id="bili"))
        bili.pop("provider")
        yt = youtube_track()
        album = {"id": "album", "title": "Mixed", "kind": "album", "tracks": [bili, asdict(yt)]}
        payload = {
            "version": 2, "entries": [album], "volume": 70,
            "tracks": [dict(bili, id="bili-queue"), asdict(replace(yt, id="bili"))],
        }
        with TemporaryDirectory() as root:
            path = Path(root) / "library.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            entries, queue, _ = Store(path).load()
        self.assertEqual([track.provider for track in entries[0].tracks], ["bilibili", "youtube"])
        self.assertEqual([track.song_id for track in queue], ["bili", yt.id])


class YouTubeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_import_selector_preserves_bilibili_rules_and_enforces_youtube_single(self):
        dialog = EntryDialog(language="en_US")
        self.assertEqual(dialog.source_input.currentData(), "bilibili")
        dialog.kind_input.setCurrentIndex(dialog.kind_input.findData("album"))
        dialog.bvid_input.setText(BV)
        self.assertEqual(dialog.value().kind, "album")
        dialog.source_input.setCurrentIndex(dialog.source_input.findData("youtube"))
        self.assertFalse(dialog.kind_input.isEnabled())
        self.assertFalse(dialog.page_input.isEnabled())
        self.assertEqual(dialog.bvid_input.placeholderText(), "YouTube video ID or URL")
        dialog.accept()
        self.assertEqual(dialog.error.text(), "Enter a valid YouTube video ID or URL.")
        dialog.bvid_input.setText(f"https://youtu.be/{YT}")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        value = dialog.value()
        self.assertEqual((value.provider, value.kind, value.page, value.bvid), ("youtube", "single", 1, YT))

    def test_editing_youtube_id_invalidates_entry_cache(self):
        track = youtube_track()
        entry = LibraryEntry(track.title, YT, tracks=[track], resolved=True, provider="youtube")
        dialog = EntryDialog(entry=entry)
        self.assertFalse(dialog.source_input.isEnabled())
        dialog.bvid_input.setText(f"https://www.youtube.com/watch?v={YT}")
        self.assertEqual(dialog.value().tracks, entry.tracks)
        dialog.bvid_input.setText(OTHER_YT)
        edited = dialog.value()
        self.assertEqual((edited.provider, edited.bvid, edited.resolved, edited.tracks),
                         ("youtube", OTHER_YT, False, []))
        track_dialog = TrackDialog(None, track)
        track_dialog.bvid_input.setText(OTHER_YT)
        edited_track = track_dialog.value()
        self.assertEqual((edited_track.provider, edited_track.bvid, edited_track.cid, edited_track.page),
                         ("youtube", OTHER_YT, 0, 1))

    def test_album_resolves_uncached_youtube_single_and_changed_track(self):
        entry = LibraryEntry("YouTube song", YT, provider="youtube")
        parent = QWidget()
        parent._submit = lambda work, success, _failure: success(work())
        album = AlbumDialog(parent, LibraryEntry("Mixed", kind="album"), [entry])
        yt_track = youtube_track()
        singles_picker = Mock()
        singles_picker.exec.return_value = QDialog.DialogCode.Accepted
        singles_picker.value.return_value = [entry]
        track_picker = Mock()
        track_picker.exec.return_value = QDialog.DialogCode.Accepted
        with patch("bili_player.dialogs.YouTubeClient") as youtube, patch("bili_player.dialogs.BilibiliClient") as bili:
            youtube.return_value.get_tracks.return_value = [yt_track]
            with patch("bili_player.dialogs._SinglesDialog", return_value=singles_picker):
                album.add_existing_singles()
            youtube.return_value.get_tracks.assert_called_once_with(entry)
            original = album.value().tracks[0]
            self.assertEqual((original.provider, original.song_id), ("youtube", entry.id))
            self.assertIn("YouTube", album.tracks_list.item(0).text())
            album.tracks_list.item(0).setSelected(True)
            track_picker.value.return_value = replace(original, bvid=OTHER_YT)
            youtube.return_value.get_tracks.return_value = [replace(yt_track, bvid=OTHER_YT, duration=99)]
            with patch("bili_player.dialogs.TrackDialog", return_value=track_picker):
                album.edit_selected()
            requested = youtube.return_value.get_tracks.call_args.args[0]
            self.assertEqual((requested.provider, requested.bvid), ("youtube", OTHER_YT))
            updated = album.value().tracks[0]
            self.assertEqual((updated.song_id, updated.provider, updated.bvid, updated.duration),
                             (entry.id, "youtube", OTHER_YT, 99))
            bili.assert_not_called()

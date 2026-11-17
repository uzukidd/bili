"""Album dialog regressions without real network calls or playback.

专辑对话框回归测试；不访问网络，也不启动播放。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import call, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QWidget,
)

from bili_player import dialogs
from bili_player.api import VideoInfo
from bili_player.models import LibraryEntry, Store, Track


BVID = "BV1xx411c7mD"
OTHER_BVID = "BV1yy411c7mD"


class PendingParent(QWidget):
    """Queue workers so tests can deliver completion in a chosen order."""

    def __init__(self, language="zh_CN"):
        super().__init__()
        self.language = language
        self.jobs = []

    def _submit(self, fn, done, error=None):
        self.jobs.append((fn, done, error))

    def complete(self, index):
        fn, done, error = self.jobs[index]
        try:
            result = fn()
        except Exception as exc:
            error(str(exc))
        else:
            done(result)


class AlbumDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = PendingParent()
        self.open_dialogs = []
        self.client_patch = patch("bili_player.dialogs.BilibiliClient", autospec=True)
        self.client = self.client_patch.start().return_value
        self.client.get_video.side_effect = AssertionError("Unexpected get_video call")
        self.client.get_tracks.side_effect = AssertionError("Unexpected get_tracks call")
        self.warning_patch = patch("bili_player.dialogs.QMessageBox.warning")
        self.warning = self.warning_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.addCleanup(self.warning_patch.stop)

    def tearDown(self):
        for dialog in reversed(self.open_dialogs):
            dialog.reject()
            dialog.deleteLater()
        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()
        self.warning.assert_not_called()

    def tracks(self):
        return [
            Track(f"Song {page}", BVID, page * 100, page, artist=f"Artist {page}", duration=page * 10)
            for page in (1, 2, 3)
        ]

    def album(self, **changes):
        entry = LibraryEntry(
            "My album", BVID, "album", tracks=self.tracks(),
            cover="https://example.com/original.jpg", resolved=True,
        )
        return replace(entry, **changes)

    def open_album(self, entry=None, singles=None, language=None):
        dialog = dialogs.AlbumDialog(
            self.parent, entry if entry is not None else self.album(),
            singles if singles is not None else [], language=language,
        )
        self.open_dialogs.append(dialog)
        return dialog

    def select_rows(self, dialog, *rows):
        dialog.tracks_list.clearSelection()
        if rows:
            dialog.tracks_list.setCurrentRow(rows[0])
            for row in rows:
                dialog.tracks_list.item(row).setSelected(True)

    def picker(self, name, value):
        mocked = patch(f"bili_player.dialogs.{name}")
        factory = mocked.start()
        self.addCleanup(mocked.stop)
        factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
        factory.return_value.value.return_value = deepcopy(value)
        return factory.return_value

    def resolve_video(self, tracks, cover="https://example.com/import.jpg"):
        self.client.get_video.side_effect = None
        self.client.get_video.return_value = VideoInfo("Video title", cover, tracks)

    def test_cancel_preserves_original_and_returned_snapshots_are_independent(self):
        original = self.album()
        before = deepcopy(original)
        dialog = self.open_album(original)
        dialog.title_input.setText("Discarded title")
        dialog.cover_input.setText("https://example.com/discarded.jpg")
        dialog.entry.tracks[0].artist = "Snapshot-only artist"
        dialog.add_tracks([Track("Added", OTHER_BVID, 999, 1)])
        self.select_rows(dialog, 0, 2)
        dialog.remove_selected()
        self.select_rows(dialog, 1)
        dialog.move_selected(-1)
        returned = dialog.value()
        returned.tracks[0].title = "External mutation"
        self.assertNotEqual(dialog._tracks()[0].title, "External mutation")
        dialog.reject()
        self.assertEqual(original, before)
        self.assertFalse(dialog._alive)
        self.assertIsNot(dialog.entry.tracks[0], original.tracks[0])

    def test_add_remove_reorder_edit_save_and_reopen_preserves_album(self):
        original = self.album()
        before = deepcopy(original)
        dialog = self.open_album(original)
        dialog.title_input.setText("Edited album")
        dialog.cover_input.setText("https://example.com/custom.jpg")
        self.select_rows(dialog, 1)
        dialog.remove_selected()
        added = Track("Added song", OTHER_BVID, 900, 4, artist="Guest")
        dialog.add_tracks([added])
        self.select_rows(dialog, 2)
        dialog.move_selected(-2)
        self.select_rows(dialog, 1)
        edited = replace(dialog._tracks()[1], title="Renamed song", artist="Custom artist")
        self.picker("TrackDialog", edited)
        dialog.edit_selected()
        self.assertEqual(self.parent.jobs, [])
        dialog.accept()
        committed = dialog.value()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(committed.resolved)
        self.assertEqual(committed.title, "Edited album")
        self.assertEqual(committed.cover, "https://example.com/custom.jpg")
        self.assertEqual([track.title for track in committed.tracks], ["Added song", "Renamed song", "Song 3"])
        self.assertEqual(committed.tracks[1].artist, "Custom artist")
        self.assertNotEqual(committed.tracks[0].id, added.id)
        self.assertNotIn(original.tracks[1].id, [track.id for track in committed.tracks])
        self.assertEqual(original, before)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.json")
            store.save([committed], [], 40)
            saved = store.load()[0][0]
            reopened = self.open_album(saved)
            self.assertEqual(reopened.value(), committed)
            self.assertEqual([track.page for track in reopened._tracks()], [4, 1, 3])
        committed.tracks[0].title = "Caller mutation"
        self.assertEqual(dialog.value().tracks[0].title, "Added song")
        self.client.get_tracks.assert_not_called()
        self.client.get_video.assert_not_called()

    def test_pending_import_disables_save_and_late_result_after_cancel_is_ignored(self):
        original = self.album()
        before = deepcopy(original)
        dialog = self.open_album(original)
        source = LibraryEntry("Imported", OTHER_BVID, "album")
        self.picker("EntryDialog", source)
        self.resolve_video([Track("Late song", OTHER_BVID, 800, 1)])
        dialog.add_bv()
        self.assertEqual(len(self.parent.jobs), 1)
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.accept()
        self.assertTrue(dialog._alive)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        tracks_before = dialog._tracks()
        cover_before = dialog.cover_input.text()
        dialog.reject()
        self.parent.complete(0)
        self.assertEqual(dialog._tracks(), tracks_before)
        self.assertEqual(dialog.cover_input.text(), cover_before)
        self.assertEqual(original, before)
        self.assertFalse(dialog._alive)

    def test_late_failure_after_cancel_does_not_show_message(self):
        dialog = self.open_album()
        self.picker("EntryDialog", LibraryEntry("Import", OTHER_BVID, "album"))
        dialog.add_bv()
        dialog.reject()
        self.parent.jobs[0][2]("Late failure")
        self.warning.assert_not_called()

    def test_save_waits_for_every_concurrent_import(self):
        dialog = self.open_album(self.album(tracks=[]))
        self.picker("EntryDialog", LibraryEntry("Import", OTHER_BVID, "album"))
        self.resolve_video([Track("Imported", OTHER_BVID, 900, 1)])
        dialog.add_bv()
        dialog.add_bv()
        self.assertEqual(len(self.parent.jobs), 2)
        self.assertFalse(dialog.save_button.isEnabled())
        self.parent.complete(1)
        self.assertFalse(dialog.save_button.isEnabled())
        self.parent.complete(0)
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertEqual(len(dialog._tracks()), 2)
        self.assertEqual(len({track.id for track in dialog._tracks()}), 2)

    def test_import_appends_all_parts_and_preserves_custom_album_title_and_cover(self):
        original = self.album()
        dialog = self.open_album(original)
        self.picker("EntryDialog", LibraryEntry("Ignored source name", OTHER_BVID, "album"))
        imported = [Track("Part A", OTHER_BVID, 901, 1), Track("Part B", OTHER_BVID, 902, 2)]
        self.resolve_video(imported)
        dialog.add_bv()
        self.parent.complete(0)
        result = dialog.value()
        self.assertEqual(result.title, original.title)
        self.assertEqual(result.cover, original.cover)
        self.assertEqual([track.title for track in result.tracks][-2:], ["Part A", "Part B"])
        self.assertEqual(len(result.tracks), 5)
        self.assertTrue(all(copied.id != source.id for copied, source in zip(result.tracks[-2:], imported)))

    def test_import_uses_video_cover_only_if_cover_still_blank(self):
        dialog = self.open_album(self.album(tracks=[], cover=""))
        self.picker("EntryDialog", LibraryEntry("My custom single", OTHER_BVID))
        self.resolve_video([Track("API title", OTHER_BVID, 900, 1)])
        dialog.add_bv()
        self.parent.complete(0)
        self.assertEqual(dialog.cover_input.text(), "https://example.com/import.jpg")
        self.assertEqual(dialog._tracks()[0].title, "My custom single")
        dialog.cover_input.clear()
        dialog.add_bv()
        dialog.cover_input.setText("https://example.com/user-chosen.jpg")
        self.parent.complete(1)
        self.assertEqual(dialog.cover_input.text(), "https://example.com/user-chosen.jpg")

    def test_cached_singles_copy_independent_ids_without_network(self):
        cached = Track("Cached title", BVID, 101, 1, artist="Original artist")
        single = LibraryEntry("Custom single title", BVID, tracks=[cached], resolved=True)
        before = deepcopy(single)
        dialog = self.open_album(self.album(tracks=[]), [single])
        self.picker("_SinglesDialog", [single])
        dialog.add_existing_singles()
        self.parent.complete(0)
        dialog.add_existing_singles()
        self.parent.complete(1)
        tracks = dialog._tracks()
        self.assertEqual([track.title for track in tracks], ["Custom single title"] * 2)
        self.assertEqual(len({cached.id, tracks[0].id, tracks[1].id}), 3)
        self.assertEqual(tracks[0].artist, "Original artist")
        self.select_rows(dialog, 0)
        dialog.remove_selected()
        dialog._tracks()[0].artist = "Outside mutation"
        self.assertEqual(single, before)
        self.assertEqual(dialog._tracks()[0].artist, "Original artist")
        self.client.get_tracks.assert_not_called()
        self.client.get_video.assert_not_called()

    def test_multiple_uncached_singles_resolve_sequentially_in_one_worker(self):
        singles = [LibraryEntry(BVID, BVID), LibraryEntry("Custom second", OTHER_BVID, page=2)]
        responses = [[Track("API first", BVID, 100, 1)], [Track("API second", OTHER_BVID, 200, 2)]]
        self.client.get_tracks.side_effect = responses
        dialog = self.open_album(self.album(tracks=[]), singles)
        self.picker("_SinglesDialog", singles)
        dialog.add_existing_singles()
        self.assertEqual(len(self.parent.jobs), 1)
        self.client.get_tracks.assert_not_called()
        self.parent.complete(0)
        self.assertEqual(self.client.get_tracks.call_args_list, [call(singles[0]), call(singles[1])])
        self.assertEqual([track.title for track in dialog._tracks()], ["API first", "Custom second"])
        self.assertEqual([entry.tracks for entry in singles], [[], []])

    def test_changed_source_refreshes_cid_and_keeps_user_metadata_and_identity(self):
        original = self.album()
        dialog = self.open_album(original)
        self.select_rows(dialog, 0)
        old = dialog._tracks()[0]
        edited = replace(old, bvid=OTHER_BVID, page=2, title="Edited title", artist="Edited artist")
        self.picker("TrackDialog", edited)
        self.client.get_tracks.side_effect = None
        self.client.get_tracks.return_value = [Track("API title", OTHER_BVID, 9876, 2, artist="API artist", duration=88)]
        dialog.edit_selected()
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertEqual(dialog._tracks()[0].cid, old.cid)
        self.parent.complete(0)
        changed = dialog._tracks()[0]
        self.assertEqual((changed.bvid, changed.page, changed.cid, changed.duration), (OTHER_BVID, 2, 9876, 88))
        self.assertEqual((changed.title, changed.artist, changed.id), ("Edited title", "Edited artist", old.id))
        request = self.client.get_tracks.call_args.args[0]
        self.assertEqual((request.kind, request.bvid, request.page), ("single", OTHER_BVID, 2))
        self.assertEqual(original.tracks[0], old)
        self.assertTrue(dialog.save_button.isEnabled())

    def test_source_edit_completion_never_resurrects_removed_track(self):
        dialog = self.open_album()
        self.select_rows(dialog, 1)
        old = dialog._tracks()[1]
        self.picker("TrackDialog", replace(old, bvid=OTHER_BVID, page=4, title="Removed edit"))
        self.client.get_tracks.side_effect = None
        self.client.get_tracks.return_value = [Track("API replacement", OTHER_BVID, 9876, 4)]
        dialog.edit_selected()
        dialog.remove_selected()
        self.select_rows(dialog, 1)
        dialog.move_selected(-1)
        before = dialog._tracks()
        self.parent.complete(0)
        self.assertEqual(dialog._tracks(), before)
        self.assertNotIn(old.id, [track.id for track in dialog._tracks()])
        self.assertTrue(dialog.save_button.isEnabled())

    def test_move_multiple_selected_tracks_preserves_order_and_stops_at_edges(self):
        dialog = self.open_album()
        dialog.add_tracks([Track("Song 4", BVID, 400, 4)])
        self.select_rows(dialog, 1, 2)
        dialog.move_selected(-1)
        self.assertEqual([track.page for track in dialog._tracks()], [2, 3, 1, 4])
        self.assertEqual(sorted(dialog.tracks_list.row(item) for item in dialog.tracks_list.selectedItems()), [0, 1])
        dialog.move_selected(-1)
        self.assertEqual([track.page for track in dialog._tracks()], [2, 3, 1, 4])
        dialog.move_selected(2)
        self.assertEqual([track.page for track in dialog._tracks()], [1, 4, 2, 3])

    def test_entry_edit_preserves_cache_for_metadata_and_invalidates_changed_sources(self):
        original = LibraryEntry(
            "Original single", BVID, tracks=[self.tracks()[0]],
            cover="https://example.com/single.jpg", resolved=True,
        )
        before = deepcopy(original)
        for changed_field in (None, "bvid", "page"):
            with self.subTest(changed_field=changed_field):
                dialog = dialogs.EntryDialog(self.parent, original)
                self.open_dialogs.append(dialog)
                dialog.title_input.setText("Renamed single")
                if changed_field == "bvid":
                    dialog.bvid_input.setText(OTHER_BVID)
                elif changed_field == "page":
                    dialog.page_input.setValue(2)
                value = dialog.value()
                self.assertEqual(value.id, original.id)
                if changed_field is None:
                    self.assertEqual((value.tracks, value.cover, value.resolved), (original.tracks, original.cover, True))
                    value.tracks[0].title = "Changed returned copy"
                else:
                    self.assertEqual((value.tracks, value.cover, value.resolved), ([], "", False))
                self.assertEqual(original, before)

    def test_english_dialog_owned_labels_buttons_placeholders_and_validation(self):
        self.parent.language = "en_US"
        album = self.open_album(self.album(tracks=[replace(self.tracks()[0], artist="")]))
        entry = dialogs.EntryDialog(self.parent)
        track = dialogs.TrackDialog(self.parent, self.tracks()[0])
        singles = dialogs._SinglesDialog(self.parent, [LibraryEntry("Library single", BVID)])
        self.open_dialogs.extend([entry, track, singles])
        entry.accept()
        track.title_input.clear()
        track.accept()
        album.title_input.clear()
        album.accept()
        self.assertEqual(album.windowTitle(), "Edit album")
        self.assertEqual(album.save_button.text(), "Save")
        self.assertEqual(entry.error.text(), "Enter a valid BV ID or Bilibili video URL.")
        self.assertEqual(track.error.text(), "Enter a track title.")
        self.assertEqual(album.error.text(), "Enter an album name.")
        self.assertIn("Unknown artist", album.tracks_list.item(0).text())
        self.assertFalse(singles.save_button.isEnabled())
        singles.singles_list.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(singles.save_button.isEnabled())
        self.assertEqual(len(singles.value()), 1)
        for dialog in (album, entry, track, singles):
            texts = [dialog.windowTitle()]
            texts.extend(widget.text() for widget in dialog.findChildren(QLabel))
            texts.extend(widget.text() for widget in dialog.findChildren(QPushButton))
            texts.extend(widget.placeholderText() for widget in dialog.findChildren(QLineEdit))
            for widget in dialog.findChildren(QComboBox):
                texts.extend(widget.itemText(index) for index in range(widget.count()))
            with self.subTest(dialog=type(dialog).__name__):
                self.assertIsNone(re.search(r"[\u3400-\u9fff]", "\n".join(texts)))

    def test_failed_import_restores_save_and_translates_english_error(self):
        dialog = self.open_album(language="en_US")
        self.picker("EntryDialog", LibraryEntry("Import", OTHER_BVID, "album"))
        dialog.add_bv()
        self.assertFalse(dialog.save_button.isEnabled())
        self.parent.jobs[0][2]("Offline")
        self.assertTrue(dialog.save_button.isEnabled())
        self.warning.assert_called_once()
        title, message = self.warning.call_args.args[1:]
        self.assertEqual(title, "Operation failed")
        self.assertEqual(message, "Could not complete the operation. Try again later.\nOffline")
        self.warning.reset_mock()


if __name__ == "__main__":
    unittest.main()

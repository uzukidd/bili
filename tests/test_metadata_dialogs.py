"""Manual single and track metadata regressions without network calls.

单曲和曲目手填信息回归测试；不访问网络。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QDialog, QPushButton

from bili_player.dialogs import AlbumDialog, EntryDialog, TrackDialog
from bili_player.models import LibraryEntry, Track


BVID = "BV1xx411c7mD"
OTHER_BVID = "BV1yy411c7mD"


class MetadataDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.open_dialogs = []
        client_patch = patch("bili_player.dialogs.BilibiliClient", autospec=True)
        self.client_factory = client_patch.start()
        self.addCleanup(client_patch.stop)

    def tearDown(self):
        self.client_factory.assert_not_called()
        for dialog in reversed(self.open_dialogs):
            dialog.reject()
            dialog.deleteLater()
        self.app.processEvents()

    def entry_dialog(self, entry=None):
        dialog = EntryDialog(entry=entry)
        self.open_dialogs.append(dialog)
        return dialog

    def track_dialog(self, track):
        dialog = TrackDialog(None, track)
        self.open_dialogs.append(dialog)
        return dialog

    def cached_single(self, **changes):
        entry = LibraryEntry(
            "Chosen title", BVID,
            tracks=[Track("Cached title", BVID, 100, 1, artist="Cached author", date="Cached date")],
            cover="https://example.com/cover.jpg", resolved=True,
            artist="作曲者 あおい", date="任意日期",
        )
        return replace(entry, **changes)

    def test_single_import_accepts_freeform_metadata_and_preserves_chosen_title(self):
        dialog = self.entry_dialog()
        dialog.bvid_input.setText(BVID)
        dialog.title_input.setText("  私の曲  ")
        dialog.artist_input.setText("  作曲者 あおい  ")
        dialog.date_input.setText("  任意日期・春  ")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        value = dialog.value()
        self.assertEqual(
            (value.title, value.artist, value.date), ("私の曲", "作曲者 あおい", "任意日期・春"),
        )
        self.assertEqual((value.tracks, value.cover, value.resolved), ([], "", False))

    def test_empty_author_and_date_are_allowed_for_import_and_existing_entry(self):
        for entry in (None, self.cached_single()):
            with self.subTest(existing=entry is not None):
                dialog = self.entry_dialog(entry)
                dialog.bvid_input.setText(BVID)
                dialog.artist_input.setText("  ")
                dialog.date_input.setText("  ")
                dialog.accept()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertEqual((dialog.value().artist, dialog.value().date), ("", ""))

    def test_entry_initial_metadata_prefers_entry_and_falls_back_per_field(self):
        for artist, date, expected_artist, expected_date in (
            ("Manual author", "Manual date", "Manual author", "Manual date"),
            ("", "Manual date", "Cached author", "Manual date"),
            ("Manual author", "", "Manual author", "Cached date"),
            ("", "", "Cached author", "Cached date"),
        ):
            with self.subTest(artist=artist, date=date):
                dialog = self.entry_dialog(self.cached_single(artist=artist, date=date))
                self.assertEqual(dialog.artist_input.text(), expected_artist)
                self.assertEqual(dialog.date_input.text(), expected_date)
        empty = self.entry_dialog(LibraryEntry("Uncached", BVID))
        self.assertEqual((empty.artist_input.text(), empty.date_input.text()), ("", ""))

    def test_album_hides_metadata_fields_and_labels_without_applying_them(self):
        dialog = self.entry_dialog()
        dialog.bvid_input.setText(BVID)
        dialog.artist_input.setText("Should not apply")
        dialog.date_input.setText("任意日期")
        dialog.kind_input.setCurrentIndex(1)
        for widget in (dialog.artist_input, dialog.artist_label, dialog.date_input, dialog.date_label):
            self.assertTrue(widget.isHidden())
        self.assertFalse(dialog.page_input.isEnabled())
        self.assertEqual((dialog.value().artist, dialog.value().date), ("", ""))
        dialog.kind_input.setCurrentIndex(0)
        for widget in (dialog.artist_input, dialog.artist_label, dialog.date_input, dialog.date_label):
            self.assertFalse(widget.isHidden())
        self.assertEqual((dialog.value().artist, dialog.value().date), ("Should not apply", "任意日期"))
        original = self.cached_single(kind="album")
        existing = self.entry_dialog(original)
        existing.artist_input.setText("Discard hidden change")
        existing.date_input.setText("Discard hidden date")
        self.assertEqual((existing.value().artist, existing.value().date), (original.artist, original.date))

    def test_source_changes_preserve_manual_metadata_and_reset_only_cached_data(self):
        for source in ("bvid", "page"):
            with self.subTest(source=source):
                original = self.cached_single()
                before = deepcopy(original)
                dialog = self.entry_dialog(original)
                if source == "bvid":
                    dialog.bvid_input.setText(OTHER_BVID)
                else:
                    dialog.page_input.setValue(2)
                value = dialog.value()
                self.assertEqual((value.title, value.id), (original.title, original.id))
                self.assertEqual((value.artist, value.date), ("作曲者 あおい", "任意日期"))
                self.assertEqual((value.tracks, value.cover, value.resolved), ([], "", False))
                self.assertEqual(original, before)

    def test_single_edit_selects_cover_and_keeps_it_when_video_source_changes(self):
        original = self.cached_single()
        dialog = self.entry_dialog(original)
        self.assertEqual(dialog.cover_input.text(), original.cover)
        picture = os.path.abspath("new cover.png")
        with patch("bili_player.dialogs.QFileDialog.getOpenFileName", return_value=(picture, "")):
            choose_button = next(button for button in dialog.findChildren(QPushButton)
                                 if button.text() == "选择图片")
            choose_button.click()
        self.assertEqual(dialog.value().cover, picture)
        dialog.bvid_input.setText(OTHER_BVID)
        dialog.cover_input.setText(" https://example.com/new.jpg ")
        self.assertEqual(dialog.value().cover, "https://example.com/new.jpg")
        self.assertFalse(dialog.value().resolved)
        self.assertEqual(original.cover, "https://example.com/cover.jpg")

    def test_existing_single_kind_is_disabled_while_import_kind_can_be_chosen(self):
        existing = self.entry_dialog(self.cached_single())
        self.assertFalse(existing.kind_input.isEnabled())
        self.assertEqual(existing.kind_input.currentData(), "single")
        self.assertEqual(existing.value().kind, "single")
        self.assertTrue(existing.page_input.isEnabled())
        importing = self.entry_dialog()
        self.assertTrue(importing.kind_input.isEnabled())
        importing.kind_input.setCurrentIndex(1)
        self.assertEqual(importing.kind_input.currentData(), "album")
        self.assertFalse(importing.page_input.isEnabled())

    def test_entry_edits_and_cancel_keep_original_and_returned_snapshots_independent(self):
        original = self.cached_single()
        before = deepcopy(original)
        dialog = self.entry_dialog(original)
        dialog.title_input.setText("Discarded title")
        dialog.artist_input.setText("Discarded author")
        dialog.date_input.setText("Discarded date")
        value = dialog.value()
        self.assertEqual((value.tracks, value.cover, value.resolved), (original.tracks, original.cover, True))
        value.tracks[0].artist = "Returned snapshot mutation"
        value.tracks[0].date = "Returned date mutation"
        self.assertEqual(dialog.value().tracks, original.tracks)
        dialog.reject()
        self.assertEqual(original, before)

    def test_track_metadata_and_source_changes_are_local_and_dates_are_not_parsed(self):
        original = self.cached_single().tracks[0]
        before = deepcopy(original)
        dialog = self.track_dialog(original)
        self.assertEqual((dialog.artist_input.text(), dialog.date_input.text()), (original.artist, original.date))
        dialog.bvid_input.setText(OTHER_BVID)
        dialog.page_input.setValue(2)
        dialog.title_input.setText("  私の曲  ")
        dialog.artist_input.setText("  作曲者 あおい  ")
        dialog.date_input.setText("  任意日期・春  ")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        value = dialog.value()
        self.assertEqual((value.title, value.artist, value.date), ("私の曲", "作曲者 あおい", "任意日期・春"))
        self.assertEqual((value.bvid, value.page), (OTHER_BVID, 2))
        self.assertEqual((value.cid, value.duration, value.id), (original.cid, original.duration, original.id))
        value.artist = "Returned snapshot mutation"
        self.assertEqual(original, before)

    def test_track_blank_metadata_and_cancel_do_not_mutate_original(self):
        original = self.cached_single().tracks[0]
        before = deepcopy(original)
        dialog = self.track_dialog(original)
        dialog.artist_input.setText("  ")
        dialog.date_input.setText("  ")
        self.assertEqual((dialog.value().artist, dialog.value().date), ("", ""))
        dialog.reject()
        self.assertEqual(original, before)

    def test_single_copy_applies_each_nonempty_field_and_preserves_other_track_metadata(self):
        original = self.cached_single().tracks[0]
        before = deepcopy(original)
        for artist, date in (("手填作者", ""), ("", "任意日期"), ("", "")):
            with self.subTest(artist=artist, date=date):
                entry = LibraryEntry(BVID, BVID, artist=artist, date=date)
                copied = AlbumDialog._single_title(entry, [original])[0]
                self.assertEqual(copied.title, original.title)
                self.assertEqual(copied.artist, artist or original.artist)
                self.assertEqual(copied.date, date or original.date)
                self.assertIsNot(copied, original)
                self.assertEqual((copied.id, copied.song_id), (original.id, original.song_id))
                self.assertEqual(original, before)


if __name__ == "__main__":
    unittest.main()

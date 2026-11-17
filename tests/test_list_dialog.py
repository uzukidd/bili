"""Offscreen list-dialog tests; no real network / 空间列表对话框离线测试。"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from copy import deepcopy
from threading import Thread
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QThread
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from bili_player.list_dialog import ListImportDialog
from bili_player.lists import ListImportResult, Listing, parse_list_url
from bili_player.models import LibraryEntry, Track


URL = "https://space.bilibili.com/1459104794/lists/2029798"
BVID = "BV1xx411c7mD"


class PendingParent(QWidget):
    def __init__(self, language="zh_CN"):
        super().__init__()
        self.language = language
        self.jobs = []
        self.entries = []
        self.tracks = []
        self._save = Mock()
        self._load_cover = Mock()

    def _submit(self, fn, done, error):
        self.jobs.append({"fn": fn, "done": done, "error": error, "finished": False})

    def succeed(self, index, result):
        self.jobs[index]["finished"] = True
        self.jobs[index]["done"](result)

    def fail(self, index, message):
        self.jobs[index]["finished"] = True
        self.jobs[index]["error"](message)

    def complete(self, index):
        try:
            result = self.jobs[index]["fn"]()
        except Exception as exc:
            self.fail(index, str(exc))
        else:
            self.succeed(index, result)


class ListDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = PendingParent()
        self.dialogs = []
        self.client_patch = patch("bili_player.list_dialog.BilibiliListClient", autospec=True)
        self.client = self.client_patch.start().return_value
        self.addCleanup(self.client_patch.stop)
        self.listing = Listing("Public list", "https://example.com/list.jpg", [BVID], 1, "season")
        self.result = ListImportResult(
            LibraryEntry("API album", kind="album", resolved=True, cover=self.listing.cover,
                         tracks=[Track("Song", BVID, 123, 1)]),
            ["BV1yy411c7mD：已删除"], 2,
        )
        self.client.get_listing.return_value = self.listing
        self.client.import_album.return_value = self.result

    def tearDown(self):
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog) and dialog._alive:
                dialog.reject()
        for index, job in enumerate(self.parent.jobs):
            if not job["finished"]:
                self.parent.fail(index, "test cleanup")
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.deleteLater()
        self.parent.close()
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(self.parent.entries, [])
        self.assertEqual(self.parent.tracks, [])
        self.parent._save.assert_not_called()

    def open_dialog(self, initial_url=URL, language=None):
        dialog = ListImportDialog(self.parent, initial_url=initial_url, language=language)
        self.dialogs.append(dialog)
        return dialog

    def preview(self, dialog):
        dialog.read_list()
        self.parent.complete(len(self.parent.jobs) - 1)

    def test_initial_state_and_keyboard_accept_cannot_return_partial_album(self):
        dialog = self.open_dialog()
        self.assertEqual(dialog.url_input.text(), URL)
        self.assertTrue(dialog.read_button.isEnabled())
        self.assertTrue(dialog.expand_parts.isChecked())
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertFalse(dialog.title_input.isEnabled())
        self.assertIsNone(dialog.import_result)
        dialog.accept()
        self.assertTrue(dialog._alive)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(self.parent.jobs, [])

    def test_invalid_input_does_not_start_worker_or_import(self):
        dialog = self.open_dialog("https://evil.test/1/lists/2")
        dialog.read_list()
        self.assertTrue(dialog.error_label.text())
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertTrue(dialog.read_button.isEnabled())
        dialog.start_import()
        self.assertEqual(dialog.error_label.text(), "请先读取空间列表。")
        self.assertEqual(self.parent.jobs, [])
        self.client.get_listing.assert_not_called()
        self.client.import_album.assert_not_called()

    def test_preview_populates_name_count_cover_and_blocks_concurrent_reads(self):
        dialog = self.open_dialog()
        dialog.read_list()
        self.assertTrue(dialog._busy)
        for widget in (dialog.url_input, dialog.read_button, dialog.title_input, dialog.expand_parts, dialog.import_button):
            self.assertFalse(widget.isEnabled())
        self.assertTrue(dialog.cancel_button.isEnabled())
        dialog.read_list()
        dialog.start_import()
        self.assertEqual(len(self.parent.jobs), 1)
        self.parent.complete(0)
        self.assertFalse(dialog._busy)
        self.assertEqual(dialog.preview_title.text(), self.listing.title)
        self.assertEqual(dialog.title_input.text(), self.listing.title)
        self.assertEqual(dialog.count_label.text(), "合集 · 1 个视频")
        self.assertEqual(dialog.cover_widget.width(), 96)
        self.parent._load_cover.assert_called_once_with(self.listing.cover, dialog.cover_widget)
        self.assertTrue(dialog.import_button.isEnabled())
        self.assertTrue(dialog.title_input.isEnabled())
        call = self.client.get_listing.call_args
        self.assertEqual(call.args[0], parse_list_url(URL))
        self.assertFalse(call.kwargs["cancelled"]())

    def test_series_preview_and_optional_cover_loader(self):
        self.listing.kind = "series"
        self.parent._load_cover = None
        dialog = self.open_dialog()
        self.preview(dialog)
        self.assertEqual(dialog.count_label.text(), "系列 · 1 个视频")
        self.assertTrue(dialog.import_button.isEnabled())

    def test_url_change_invalidates_previous_preview_and_cover(self):
        dialog = self.open_dialog()
        self.preview(dialog)
        pixmap = QPixmap(10, 10)
        dialog.cover_widget.set_pixmap(pixmap)
        dialog.url_input.setText("https://space.bilibili.com/1/lists/2")
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertEqual(dialog.title_input.text(), "")
        self.assertEqual(dialog.preview_title.text(), "")
        self.assertEqual(dialog.cover_widget._requested_cover, "")
        self.assertTrue(dialog.cover_widget._pixmap.isNull())
        dialog.start_import()
        self.client.import_album.assert_not_called()

    def test_custom_title_and_checkbox_are_captured_and_returned_without_mutation(self):
        dialog = self.open_dialog()
        self.preview(dialog)
        original = deepcopy(self.result)
        dialog.title_input.setText("  My editable album  ")
        dialog.expand_parts.setChecked(False)
        dialog.start_import()
        self.assertTrue(dialog._busy)
        dialog.accept()
        self.assertIsNone(dialog.import_result)
        self.assertTrue(dialog._alive)
        self.parent.complete(1)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertFalse(dialog._alive)
        self.assertEqual(dialog.import_result.album.title, "My editable album")
        self.assertEqual(dialog.import_result.skipped, self.result.skipped)
        self.assertEqual(self.result, original)
        call = self.client.import_album.call_args
        self.assertEqual(call.args[0], parse_list_url(URL))
        self.assertFalse(call.kwargs["expand_parts"])
        self.assertTrue(callable(call.kwargs["progress"]))
        self.assertTrue(callable(call.kwargs["cancelled"]))

    def test_default_import_expands_parts_and_blank_title_is_rejected(self):
        dialog = self.open_dialog()
        self.preview(dialog)
        dialog.title_input.setText(" ")
        dialog.start_import()
        self.assertEqual(len(self.parent.jobs), 1)
        self.assertTrue(dialog.error_label.text())
        dialog.title_input.setText("Album")
        dialog.start_import()
        self.parent.complete(1)
        self.assertTrue(self.client.import_album.call_args.kwargs["expand_parts"])

    def test_cancel_before_preview_completion_ignores_late_success(self):
        dialog = self.open_dialog()
        dialog.read_list()
        dialog.reject()
        self.assertFalse(dialog._alive)
        self.assertTrue(dialog._busy)
        self.assertTrue(dialog._cancelled.is_set())
        self.assertFalse(dialog._disposed)
        self.parent.succeed(0, self.listing)
        self.assertFalse(dialog._busy)
        self.assertTrue(dialog._disposed)
        self.assertIsNone(dialog.import_result)
        self.assertEqual(dialog.preview_title.text(), "")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.parent._load_cover.assert_not_called()

    def test_cancel_during_import_retains_signal_until_worker_returns(self):
        dialog = self.open_dialog()
        self.preview(dialog)
        dialog.start_import()
        pending_result = self.parent.jobs[1]["fn"]()
        callbacks = self.client.import_album.call_args.kwargs
        self.assertFalse(callbacks["cancelled"]())
        dialog.reject()
        self.assertTrue(callbacks["cancelled"]())
        self.assertFalse(dialog._disposed)
        before = dialog.status_label.text()
        callbacks["progress"](1, 2, BVID)
        self.app.processEvents()
        self.assertFalse(sip.isdeleted(dialog))
        self.assertEqual(dialog.status_label.text(), before)
        self.parent.succeed(1, pending_result)
        self.assertIsNone(dialog.import_result)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertFalse(dialog._busy)
        self.assertTrue(dialog._disposed)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(sip.isdeleted(dialog))

    def test_late_failure_after_cancel_is_ignored_and_disposed(self):
        dialog = self.open_dialog()
        self.preview(dialog)
        dialog.start_import()
        dialog.reject()
        self.parent.fail(1, "late network failure")
        self.assertEqual(dialog.error_label.text(), "")
        self.assertIsNone(dialog.import_result)
        self.assertTrue(dialog._disposed)

    def test_preview_failure_restores_controls_and_allows_retry(self):
        dialog = self.open_dialog()
        dialog.read_list()
        self.parent.fail(0, "哔哩哔哩错误 -412：请求被暂时限制，请稍后重试")
        self.assertFalse(dialog._busy)
        self.assertTrue(dialog.url_input.isEnabled())
        self.assertTrue(dialog.read_button.isEnabled())
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertIn("-412", dialog.error_label.text())
        self.assertIsNone(dialog.import_result)
        self.preview(dialog)
        self.assertTrue(dialog.import_button.isEnabled())

    def test_import_failure_restores_preview_without_result_and_allows_retry(self):
        dialog = self.open_dialog()
        self.preview(dialog)
        dialog.title_input.setText("Keep my title")
        dialog.start_import()
        self.parent.fail(1, "network outage")
        self.assertFalse(dialog._busy)
        self.assertTrue(dialog.import_button.isEnabled())
        self.assertTrue(dialog.title_input.isEnabled())
        self.assertEqual(dialog.title_input.text(), "Keep my title")
        self.assertIsNone(dialog.import_result)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.error_label.text(), "network outage")
        dialog.start_import()
        self.parent.complete(2)
        self.assertEqual(dialog.import_result.album.title, "Keep my title")

    def test_progress_from_real_worker_is_queued_to_gui_thread(self):
        dialog = self.open_dialog()
        self.preview(dialog)
        self.client.import_album.side_effect = lambda _source, **kwargs: (
            kwargs["progress"](1, 2, BVID), self.result,
        )[1]
        dialog.start_import()
        changes = []
        dialog.progress_bar.valueChanged.connect(lambda _value: changes.append(QThread.currentThread()))
        outcomes = []
        worker = Thread(target=lambda: outcomes.append(self.parent.jobs[1]["fn"]()))
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(dialog.progress_bar.value(), 0)
        self.assertEqual(changes, [])
        self.app.processEvents()
        self.assertEqual(dialog.progress_bar.maximum(), 2)
        self.assertEqual(dialog.progress_bar.value(), 1)
        self.assertIn(BVID, dialog.status_label.text())
        self.assertTrue(changes)
        self.assertTrue(all(thread == self.app.thread() for thread in changes))
        self.parent.succeed(1, outcomes[0])

    def test_missing_submit_is_a_visible_recoverable_error(self):
        self.parent._submit = None
        dialog = self.open_dialog()
        dialog.read_list()
        self.assertFalse(dialog._busy)
        self.assertTrue(dialog.read_button.isEnabled())
        self.assertEqual(dialog.error_label.text(), "当前窗口无法启动后台任务。")
        self.client.get_listing.assert_not_called()

    def test_language_override_and_error_translation(self):
        self.parent.language = "zh_CN"
        dialog = self.open_dialog(language="en_US")
        self.assertEqual(dialog.cancel_button.text(), "Cancel")
        dialog.read_list()
        self.parent.fail(0, "请求哔哩哔哩超时，请稍后重试")
        self.assertEqual(dialog.error_label.text(), "Bilibili request timed out. Try again later.")


if __name__ == "__main__":
    unittest.main()

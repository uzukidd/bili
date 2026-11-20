"""Settings dialog and audio device routing regression tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtMultimedia import QAudioDevice, QMediaDevices, QMediaPlayer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from bili_player.app import MainWindow
from bili_player.models import Store
from bili_player.settings_dialog import SettingsDialog


def output(identity, description):
    device = Mock()
    device.id.return_value = identity
    device.description.return_value = description
    device.isNull.return_value = False
    return device


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "library.json"
        self.window = MainWindow(self.path)
        self.addCleanup(self.close_window, self.window)

    def close_window(self, window):
        window._quitting = True
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_settings_button_opens_single_dialog_with_live_controls(self):
        seen = []

        def interact():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                dialog.theme_combo.setCurrentIndex(1)
                dialog.theme_combo.activated.emit(1)
                dialog.language_combo.setCurrentIndex(1)
                dialog.language_combo.activated.emit(1)
                seen.append((dialog.windowTitle(), dialog.theme_combo.currentText(),
                             dialog.output_label.text(), dialog.cache_summary.text(),
                             dialog.clear_cache_button.isEnabled()))
            if dialog is not None:
                dialog.accept()

        QTimer.singleShot(0, interact)
        self.window.settings_button.click()
        self.assertEqual(seen, [("Settings", "Dark", "Audio output device",
                                 "Audio cache: 0.0 MiB · 0 tracks", True)])
        self.assertEqual(self.window.settings_button.text(), "Settings")
        self.assertIsNone(self.window._settings_dialog)
        store = Store(self.path)
        store.load()
        self.assertEqual((store.settings["theme"], store.settings["language"]), ("dark", "en_US"))

    def test_youtube_session_defaults_to_chrome_and_anonymous_choice_persists(self):
        from bili_player.models import LibraryEntry

        dialog = SettingsDialog(self.window)
        dialog.refresh_preferences()
        self.assertIs(dialog.youtube_session_combo.currentData(), True)
        dialog.youtube_session_combo.setCurrentIndex(1)
        dialog.youtube_session_combo.activated.emit(1)
        reopened = MainWindow(self.path)
        self.addCleanup(self.close_window, reopened)
        entry = LibraryEntry("Video", "BaW_jenozKc", provider="youtube")
        self.assertFalse(reopened._source_client(entry).use_chrome)

    def test_youtube_cookie_mode_shows_instructions_and_restores_path(self):
        from bili_player.models import LibraryEntry
        from bili_player.youtube import YouTubeError

        entry = LibraryEntry("Video", "BaW_jenozKc", provider="youtube")
        cookies = Path(self.directory.name) / "YouTube cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tsession\tdummy-secret\n",
                           encoding="utf-8")
        dialog = self.window._settings_dialog = SettingsDialog(self.window)
        dialog.refresh_preferences()
        self.assertTrue(dialog.youtube_cookie_panel.isHidden())
        index = dialog.youtube_session_combo.findData("file")
        dialog.youtube_session_combo.setCurrentIndex(index)
        dialog.youtube_session_combo.activated.emit(index)
        self.assertFalse(dialog.youtube_cookie_panel.isHidden())
        self.assertIn("Get cookies.txt LOCALLY", dialog.youtube_cookie_hint.text())
        self.assertIn("robots.txt", dialog.youtube_cookie_hint.text())
        self.assertIn("请勿分享", dialog.youtube_cookie_hint.text())
        with patch("bili_player.settings_dialog.QFileDialog.getOpenFileName", return_value=(str(cookies), "")):
            dialog.youtube_cookie_choose.click()
        self.assertEqual(dialog.youtube_cookie_path.text(), str(cookies.resolve()))
        self.assertNotIn("dummy-secret", self.path.read_text(encoding="utf-8"))

        reopened = MainWindow(self.path)
        self.addCleanup(self.close_window, reopened)
        client = reopened._source_client(entry)
        self.assertFalse(client.use_chrome)
        self.assertEqual(client.cookie_file, str(cookies.resolve()))
        dialog.youtube_cookie_clear.click()
        self.assertEqual(dialog.youtube_cookie_path.text(), "")
        self.assertEqual(self.window._source_client(entry).cookie_file, "")
        with self.assertRaisesRegex(YouTubeError, "请在设置中选择"):
            self.window._source_client(entry).get_video(entry)
        dialog.youtube_session_combo.setCurrentIndex(1)
        dialog.youtube_session_combo.activated.emit(1)
        self.assertTrue(dialog.youtube_cookie_panel.isHidden())
        self.assertIsNone(self.window._source_client(entry).cookie_file)

    def test_long_settings_scroll_without_moving_close_button(self):
        self.window.set_youtube_session("file")
        dialog = self.window._settings_dialog = SettingsDialog(self.window)
        self.window._refresh_settings()
        dialog.resize(560, 360)
        dialog.show()
        try:
            self.app.processEvents()
            area = dialog.scroll_area
            bar = area.verticalScrollBar()
            bar.setValue(0)
            self.assertTrue(bar.isVisible())
            self.assertGreater(bar.maximum(), 0)
            self.assertTrue(dialog.close_button.isVisible())
            close_pos = dialog.close_button.mapTo(dialog, dialog.close_button.rect().center())

            area.ensureWidgetVisible(dialog.youtube_cookie_hint)
            hint = dialog.youtube_cookie_hint
            self.assertTrue(area.viewport().rect().contains(hint.mapTo(area.viewport(), hint.rect().center())))
            bar.setValue(bar.maximum())
            cache = dialog.clear_cache_button
            self.assertTrue(area.viewport().rect().contains(cache.mapTo(area.viewport(), cache.rect().center())))
            self.assertEqual(dialog.close_button.mapTo(dialog, dialog.close_button.rect().center()), close_pos)
        finally:
            dialog.close()
            self.window._settings_dialog = None

    def test_proxy_applies_only_after_validation_and_persists_on_reopen(self):
        from bili_player.network import proxy_for_url

        dialog = self.window._settings_dialog = SettingsDialog(self.window)
        dialog.refresh_preferences()
        self.assertEqual(dialog.proxy_combo.currentData(), "system")
        self.assertFalse(dialog.proxy_address.isEnabled())
        dialog.proxy_combo.setCurrentIndex(dialog.proxy_combo.findData("manual"))
        self.assertTrue(dialog.proxy_address.isEnabled())
        dialog.proxy_address.setText("https://user:pass@localhost:7890")
        dialog.proxy_apply.click()
        self.assertEqual(self.window.proxy_mode, "system")
        self.assertIn("代理地址", dialog.proxy_status.text())
        dialog.proxy_address.setText("  http://127.0.0.1:7890/  ")
        dialog.proxy_apply.click()
        self.assertEqual(dialog.proxy_address.text(), "http://127.0.0.1:7890")
        self.assertEqual(proxy_for_url("https://www.youtube.com/"), "http://127.0.0.1:7890")
        self.assertIsNone(proxy_for_url("http://127.0.0.1:4567/audio"))
        reopened = MainWindow(self.path)
        self.addCleanup(self.close_window, reopened)
        self.assertEqual((reopened.proxy_mode, reopened.proxy_url), ("manual", "http://127.0.0.1:7890"))
        self.assertIsNone(reopened.set_proxy_settings("direct", ""))
        self.assertIsNone(proxy_for_url("https://www.youtube.com/"))

    def test_mini_style_previews_select_and_restore_saved_style(self):
        dialog = self.window._settings_dialog = SettingsDialog(self.window)
        dialog.refresh_preferences()
        classic = dialog.mini_cover_buttons["classic"]
        record = dialog.mini_cover_buttons["record"]
        self.assertFalse(classic.icon().isNull())
        self.assertFalse(record.icon().isNull())
        size = classic.iconSize()
        self.assertNotEqual(classic.icon().pixmap(size).toImage(), record.icon().pixmap(size).toImage())
        self.assertTrue(classic.isChecked())

        with patch.object(self.window.player, "stop") as stop:
            record.click()
            stop.assert_not_called()
        self.assertTrue(record.isChecked())
        self.assertFalse(classic.isChecked())
        self.assertEqual(self.window.mini_cover.cover_style, "record")

        reopened = MainWindow(self.path)
        self.addCleanup(self.close_window, reopened)
        self.assertEqual(reopened.mini_cover.cover_style, "record")
        classic.click()
        self.assertEqual(self.window.mini_cover.cover_style, "classic")

    def test_visible_record_rotates_pauses_and_resumes_at_current_angle(self):
        self.window.set_mini_cover_style("record")
        self.window.set_mini_mode(True)
        self.window.show()
        cover = self.window.mini_cover
        pixmap = QPixmap(160, 160)
        pixmap.fill(QColor("#9e75c7"))
        painter = QPainter(pixmap)
        painter.fillRect(0, 0, 80, 160, QColor("#e382a6"))
        painter.end()
        cover.set_pixmap(pixmap)
        initial_image = cover.grab().toImage()
        states = QMediaPlayer.PlaybackState
        self.window._playback_state(states.PlayingState)
        QTest.qWait(65)
        self.window._playback_state(states.PausedState)
        paused_angle = cover.angle
        paused_image = cover.grab().toImage()
        self.assertGreater(paused_angle, 0)
        self.assertNotEqual(initial_image, paused_image)
        QTest.qWait(65)
        self.assertEqual(cover.angle, paused_angle)
        self.assertEqual(cover.grab().toImage(), paused_image)
        self.window._playback_state(states.PlayingState)
        self.assertAlmostEqual(cover.angle, paused_angle, delta=1)
        QTest.qWait(65)
        self.assertGreater(cover.angle, paused_angle)

        self.window.set_mini_mode(False)
        hidden_angle = cover.angle
        QTest.qWait(65)
        self.assertEqual(cover.angle, hidden_angle)

    def test_output_selection_routes_existing_player_and_survives_restart(self):
        default = output(b"built-in", "Speakers")
        headset = output(b"headset", "Headphones")
        key = headset.id().hex()
        with patch.object(QMediaDevices, "audioOutputs", return_value=[default, headset]), \
             patch.object(QMediaDevices, "defaultAudioOutput", return_value=default), \
             patch.object(self.window.audio, "device", return_value=default), \
             patch.object(self.window.audio, "setDevice") as switch, \
             patch.object(self.window.player, "setSource") as source, \
             patch.object(self.window.player, "stop") as stop:
            dialog = self.window._settings_dialog = SettingsDialog(self.window)
            self.window._refresh_settings()
            self.assertIn("Speakers", dialog.output_combo.itemText(0))
            index = dialog.output_combo.findData(key)
            dialog.output_combo.setCurrentIndex(index)
            dialog.output_combo.activated.emit(index)
            switch.assert_called_once_with(headset)
            source.assert_not_called()
            stop.assert_not_called()
            self.assertEqual(dialog.output_combo.currentData(), key)
        reopened = MainWindow(self.path)
        self.addCleanup(self.close_window, reopened)
        self.assertEqual(reopened.audio_output_id, key)

    def test_default_follows_system_and_saved_device_returns_after_reconnect(self):
        default = output(b"speaker", "Speakers")
        headset = output(b"headset", "Headphones")
        key = headset.id().hex()
        with patch.object(QMediaDevices, "audioOutputs", return_value=[default]) as outputs, \
             patch.object(QMediaDevices, "defaultAudioOutput", return_value=default) as system, \
             patch.object(self.window.audio, "device", return_value=QAudioDevice()), \
             patch.object(self.window.audio, "setDevice") as switch:
            dialog = self.window._settings_dialog = SettingsDialog(self.window)
            self.window.set_audio_output(key)
            switch.assert_called_with(default)
            self.assertEqual(dialog.output_combo.currentData(), key)
            self.assertIn("不可用", dialog.output_hint.text())
            outputs.return_value = [default, headset]
            self.window.media_devices.audioOutputsChanged.emit()
            switch.assert_called_with(headset)
            self.assertEqual(dialog.output_combo.currentText(), "Headphones")
            self.window.set_audio_output("")
            switch.assert_called_with(default)
            system.return_value = headset
            self.window.media_devices.audioOutputsChanged.emit()
            switch.assert_called_with(headset)
            self.assertEqual(dialog.output_combo.currentData(), "")

    def test_no_devices_and_cache_read_failure_are_visible_in_settings(self):
        self.window._settings_dialog = dialog = SettingsDialog(self.window)
        with patch.object(QMediaDevices, "audioOutputs", return_value=[]), \
             patch.object(QMediaDevices, "defaultAudioOutput", return_value=QAudioDevice()), \
             patch.object(self.window.audio, "setDevice") as switch, \
             patch.object(self.window.proxy, "cache_info", side_effect=OSError):
            self.window._refresh_settings()
            self.assertEqual(dialog.output_combo.count(), 1)
            self.assertIn("未检测到", dialog.output_hint.text())
            self.assertIn("暂不可用", dialog.cache_summary.text())
            switch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

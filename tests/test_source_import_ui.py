"""Exercise the YouTube import, queue, source menu, and cache as one flow."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import requests
from PyQt6.QtWidgets import QApplication

from bili_player.api import VideoInfo
from bili_player.app import MainWindow
from bili_player.dialogs import EntryDialog
from bili_player.models import Store, Track
from tests.test_player import AudioFixture


class SourceImportTests(unittest.TestCase):
    def test_youtube_entry_import_queue_playback_and_offline_replay(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            window = MainWindow(path)
            fixture = AudioFixture()
            try:
                window._submit = lambda work, done, error=None: done(work())
                window._load_cover = lambda *_: None
                dialog = EntryDialog(window)
                dialog.source_input.setCurrentIndex(dialog.source_input.findData("youtube"))
                dialog.bvid_input.setText("https://youtu.be/BaW_jenozKc")
                source = dialog.value()
                info = VideoInfo("Example", "https://i.ytimg.com/vi/BaW_jenozKc/default.jpg", [
                    Track("Example", "BaW_jenozKc", 0, 1, duration=2, provider="youtube"),
                ])
                with patch("bili_player.app.YouTubeClient.get_video", return_value=info) as metadata:
                    window.import_entry(source)
                    metadata.assert_called_once_with(source)
                song, = window.entries
                self.assertEqual((song.title, song.cover, song.provider), (info.title, info.cover, "youtube"))
                menu = window._build_entry_menu(song)
                action = next(action for action in menu.actions() if action.data() == "source")
                with patch("bili_player.app.QDesktopServices.openUrl") as open_url:
                    action.trigger()
                self.assertEqual(open_url.call_args.args[0].toString(), "https://www.youtube.com/watch?v=BaW_jenozKc")
                window.resolve_entry(song, False)
                queued, = window._tracks()
                self.assertEqual(queued.song_id, song.id)
                with patch("bili_player.app.YouTubeClient.get_stream", return_value=fixture.url) as extract, \
                     patch.object(window.player, "setSource") as set_source, patch.object(window.player, "play"):
                    window.play_item(window.queue.item(0))
                    extract.assert_called_once_with(queued)
                    url = set_source.call_args.args[0].toString()
                    self.assertEqual(requests.get(url, timeout=3).content, fixture.data)
                    window.play_item(window.queue.item(0))
                    extract.assert_called_once()
                entries, queue, _ = Store(path).load()
                self.assertEqual(entries[0].provider, "youtube")
                self.assertEqual(queue[0].provider, "youtube")
            finally:
                fixture.close()
                window._quitting = True
                window.close()
                window.deleteLater()
                app.processEvents()

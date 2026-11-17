"""Manually verify a public BV with Qt; no library changes or audible playback."""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from bili_player.app import MainWindow, STYLE
from bili_player.dialogs import AlbumDialog
from bili_player.models import LibraryEntry, normalize_bvid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bvid", help="Public BV ID or full video URL")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--album", action="store_true", help="Import all video parts as an editable album")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--language", choices=("zh_CN", "en_US"), default="zh_CN")
    parser.add_argument("--preview-pair", type=Path, help="Save light/Chinese and dark/English previews")
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    bvid = normalize_bvid(args.bvid)
    entry = LibraryEntry(bvid, bvid, "album" if args.album else "single")
    with tempfile.TemporaryDirectory() as directory:
        window = MainWindow(Path(directory) / "library.json")
        errors = []
        window._error = errors.append
        window.player.errorOccurred.connect(lambda _code, message: errors.append(message))
        try:
            window.set_theme(args.theme)
            window.set_language(args.language)
            window.audio.setVolume(0)
            window.import_entry(entry)
            deadline = time.monotonic() + 25
            while not window.entries and not errors and time.monotonic() < deadline:
                QTest.qWait(50)
            if errors or not window.entries:
                raise RuntimeError("Import failed: " + "; ".join(errors))
            imported = window.entries[0]
            print(f"Import OK: {len(imported.tracks)} tracks; video_cover={bool(imported.cover)}")
            window.resolve_entry(imported, play=True)
            deadline = time.monotonic() + 40
            while window.player.position() == 0 and not errors and time.monotonic() < deadline:
                QTest.qWait(50)
            if errors:
                raise RuntimeError("Qt playback failed: " + "; ".join(errors))
            if not window.player.duration() or not window.player.position():
                raise RuntimeError("Timed out waiting for muted Qt playback")
            source = window.player.source()
            identity = window.current_id
            window.set_theme("dark" if args.theme == "light" else "light")
            window.set_language("en_US" if args.language == "zh_CN" else "zh_CN")
            QTest.qWait(100)
            if window.player.source() != source or window.current_id != identity:
                raise RuntimeError("Appearance switch replaced the playing source")
            window.player.pause()
            tracks = window._tracks()
            print(f"Metadata OK: {tracks[0].title}")
            print(f"Qt playback OK: {window.player.duration()} ms; seekable={window.player.isSeekable()}")
            deadline = time.monotonic() + 15
            while imported.cover and imported.cover not in window._cover_pixmaps and time.monotonic() < deadline:
                QTest.qWait(50)
            if imported.cover and imported.cover not in window._cover_pixmaps:
                raise RuntimeError("The video's default cover did not load")
            print(f"Cover cache OK; live theme/language switch OK")
            window.set_theme(args.theme)
            window.set_language(args.language)
            window._status("已暂停")
            window.show()
            QTest.qWait(150)

            def capture(path):
                path.parent.mkdir(parents=True, exist_ok=True)
                QTest.qWait(150)
                if not window.grab().save(str(path)):
                    raise RuntimeError("Screenshot could not be saved")
                print(f"Screenshot: {path}")

            if args.screenshot:
                capture(args.screenshot)
            if args.preview_pair:
                window.set_theme("light")
                window.set_language("zh_CN")
                capture(args.preview_pair / "day-zh.png")
                window.set_theme("dark")
                window.set_language("en_US")
                capture(args.preview_pair / "night-en.png")
                if imported.kind == "album":
                    editor = AlbumDialog(window, imported, [])
                    editor.show()
                    QTest.qWait(150)
                    destination = args.preview_pair / "album-editor-en.png"
                    if not editor.grab().save(str(destination)):
                        raise RuntimeError("Album editor screenshot could not be saved")
                    editor.reject()
                    print(f"Screenshot: {destination}")
        finally:
            window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

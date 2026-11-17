"""Manually verify a public BV with Qt; no library changes or audible playback."""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QPoint
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from bili_player.app import MainWindow, STYLE, copy_tracks
from bili_player.details import EntryDetailsDialog
from bili_player.dialogs import AlbumDialog
from bili_player.exchange import read_library
from bili_player.models import LibraryEntry, normalize_bvid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bvid", help="Public BV ID or full video URL")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--album", action="store_true", help="Import all video parts as an editable album")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--language", choices=("zh_CN", "en_US"), default="zh_CN")
    parser.add_argument("--author", default="", help="Manual author text for a single")
    parser.add_argument("--date", default="", help="Manual date text for a single")
    parser.add_argument("--preview-pair", type=Path, help="Save light/Chinese and dark/English previews")
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    bvid = normalize_bvid(args.bvid)
    entry = LibraryEntry(bvid, bvid, "album" if args.album else "single",
                         artist=args.author if not args.album else "", date=args.date if not args.album else "")
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
            if any(track.artist != entry.artist or track.date != entry.date for track in imported.tracks):
                raise RuntimeError("Imported author/date differ from manual input")
            print("Import metadata OK: author/date match manual input; no uploader synced")
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

            def capture(path, widget=None):
                path.parent.mkdir(parents=True, exist_ok=True)
                QTest.qWait(150)
                if not (widget or window).grab().save(str(path)):
                    raise RuntimeError("Screenshot could not be saved")
                print(f"Screenshot: {path}")

            if args.screenshot:
                capture(args.screenshot)
            if args.preview_pair:
                sample_single = imported
                if imported.kind == "album" and imported.tracks:
                    first = imported.tracks[0]
                    sample_single = window.song_for_track(first, imported)
                    if sample_single is None:
                        raise RuntimeError("Imported album has no canonical song entity")
                export_path = args.preview_pair / "library-export-example.json"
                export_path.parent.mkdir(parents=True, exist_ok=True)
                if not window.export_json_path(export_path):
                    raise RuntimeError("Library export failed")
                exported = read_library(export_path)
                if len(exported) != len(window.entries):
                    raise RuntimeError("Export did not include the entire library")
                print(f"Entire-library JSON export/read OK: {len(exported)} entries")
                window.search.setFocus()
                window.set_theme("light")
                window.set_language("zh_CN")
                capture(args.preview_pair / "day-zh.png")
                window.set_theme("dark")
                window.set_language("en_US")
                capture(args.preview_pair / "night-en.png")
                card = window.cards._cards[0]
                QTest.mouseMove(window.search, QPoint(4, 4))
                card._set_hovered(False)
                capture(args.preview_pair / "card-rest-en.png", card)
                QTest.mouseMove(card, QPoint(8, 8))
                card._set_hovered(True)
                capture(args.preview_pair / "card-hover-en.png", card)
                menu = window._build_entry_menu(imported)
                menu.popup(card.mapToGlobal(card.rect().center()))
                capture(args.preview_pair / "card-menu-en.png", menu)
                menu.close()
                menu.deleteLater()
                for sample, filename in ((imported, "entry-details-en.png"), (sample_single, "single-details-en.png")):
                    details = EntryDetailsDialog(window, sample)
                    details.show()
                    capture(args.preview_pair / filename, details)
                    details.close()
                    details.deleteLater()
                single_info = EntryDetailsDialog(window, sample_single, language="zh_CN")
                single_info.show()
                capture(args.preview_pair / "single-info-zh.png", single_info)
                single_info.close()
                single_info.deleteLater()
                if imported.kind == "album":
                    editor = AlbumDialog(window, imported, [])
                    editor.show()
                    QTest.qWait(150)
                    destination = args.preview_pair / "album-editor-en.png"
                    if not editor.grab().save(str(destination)):
                        raise RuntimeError("Album editor screenshot could not be saved")
                    editor.reject()
                    print(f"Screenshot: {destination}")
                window.set_language("zh_CN")
                window.mode.setCurrentIndex(3)
                window.set_mini_mode(True)
                window.set_always_on_top(True)
                capture(args.preview_pair / "mini-pinned-zh.png")
                if sys.platform == "win32":
                    import ctypes
                    from ctypes import wintypes
                    get_style = ctypes.windll.user32.GetWindowLongW
                    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
                    get_style.restype = ctypes.c_long
                    if not get_style(int(window.winId()), -20) & 0x00000008:
                        raise RuntimeError("Windows did not apply the topmost window flag")
                    print("Native Windows topmost flag OK")
                    if get_style(int(window.winId()), -16) & 0x00C00000:
                        raise RuntimeError("Native Windows caption is still present")
                    print("Native Windows caption removed; custom title controls active")
                before_queue = window.size()
                window.toggle_mini_queue()
                capture(args.preview_pair / "mini-queue-zh.png")
                if window.size() != before_queue:
                    raise RuntimeError("Queue overlay resized the mini player")
                if window.mini_queue_panel.geometry().bottom() >= window.mini_play.geometry().top():
                    raise RuntimeError("Queue overlay did not appear above the controls")
                if not window.mini_queue_panel.geometry().intersects(window.mini_cover.geometry()):
                    raise RuntimeError("Queue overlay did not cover album artwork")
                print("Mini queue overlay above controls OK; window size unchanged")
                window.set_always_on_top(False)
        finally:
            window._quitting = True
            window.close()
            window.pool.waitForDone(20000)
            app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

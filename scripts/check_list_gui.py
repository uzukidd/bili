"""Exercise the real list-import dialog in a temporary library.

在临时音乐库验证列表预览、导入、保存及界面截图，不修改用户音乐库。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6 import sip
from PyQt6.QtCore import QPoint
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from bili_player.app import MainWindow
from bili_player.details import EntryDetailsDialog
from bili_player.list_dialog import ListImportDialog
from bili_player.models import Store


def wait_for(test, timeout, description):
    end = time.monotonic() + timeout
    while not test() and time.monotonic() < end:
        QTest.qWait(40)
    if not test():
        raise RuntimeError(description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--previews", type=Path)
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    app = QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as directory:
        window = MainWindow(Path(directory) / "library.json")
        dialog = ListImportDialog(window, args.url)
        try:
            window.show()
            dialog.show()
            dialog.read_list()
            wait_for(lambda: not dialog._busy, 40, "List preview timed out")
            if dialog._listing is None:
                raise RuntimeError(dialog.error_label.text())
            print(f"Preview OK: {dialog._listing.title}; videos={dialog._listing.total}")
            if args.previews:
                args.previews.mkdir(parents=True, exist_ok=True)
                wait_for(lambda: not dialog._listing.cover or dialog._listing.cover in window._cover_pixmaps,
                         20, "List cover timed out")
                QTest.qWait(100)
                dialog.grab().save(str(args.previews / "list-preview-zh.png"))
            dialog.start_import()
            wait_for(lambda: dialog.import_result is not None or (not dialog._busy and not sip.isdeleted(dialog)),
                     180, "List import timed out")
            if dialog.import_result is None:
                raise RuntimeError(dialog.error_label.text())
            result = dialog.import_result
            if result.skipped:
                raise RuntimeError(f"Example has skipped videos: {result.skipped}")
            window.commit_list_import(result)
            stored = Store(window.store.path).load()[0]
            if stored[0].title != result.album.title or stored[0].tracks != window.entries[0].tracks:
                raise RuntimeError("Imported album did not survive save/reopen")
            singles = [e for e in stored if e.kind == "single"]
            if len(singles) != len(stored[0].tracks):
                raise RuntimeError("Imported tracks were not registered as song entities")
            if any(not any(t is song.tracks[0] for song in singles) for t in stored[0].tracks):
                raise RuntimeError("Album membership is not shared with song entities")
            if any(t.artist for t in stored[0].tracks):
                raise RuntimeError("An uploader was synced to artist")
            print(f"GUI import/save OK: {stored[0].title}; tracks={len(stored[0].tracks)}; skipped=0")
            if args.previews:
                window.set_theme("dark")
                card = window.cards._cards[0]
                wait_for(lambda: not stored[0].cover or stored[0].cover in window._cover_pixmaps,
                         20, "Album cover timed out")
                QTest.mouseMove(card, QPoint(8, 8))
                card._set_hovered(True)
                QTest.qWait(100)
                card.grab().save(str(args.previews / "theme-hover-zh.png"))
                window.set_card_scale(1.3)
                QTest.qWait(100)
                window.grab().save(str(args.previews / "library-130-zh.png"))
                details = EntryDetailsDialog(window, window.entries[0])
                details.show()
                QTest.qWait(100)
                details.grab().save(str(args.previews / "list-album-details-zh.png"))
                details.scroll_area.verticalScrollBar().setValue(95)
                QTest.qWait(100)
                details.grab().save(str(args.previews / "album-partly-collapsed-zh.png"))
                details.scroll_area.verticalScrollBar().setValue(260)
                QTest.qWait(100)
                if not details.sticky_header.isVisible() or details.cover_opacity.opacity() != 0:
                    raise RuntimeError("Album hero did not collapse to its title")
                details.grab().save(str(args.previews / "album-collapsed-zh.png"))
                details.close()
                details.deleteLater()
                print(f"Previews: {args.previews}")
        finally:
            if not sip.isdeleted(dialog) and dialog._alive:
                dialog.reject()
            window._quitting = True
            window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

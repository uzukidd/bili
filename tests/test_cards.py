"""Library tile geometry, drag/drop swapping, and mini-window pin controls."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PyQt6.QtGui import QContextMenuEvent, QColor, QDragEnterEvent, QDropEvent, QEnterEvent, QMouseEvent, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton

from bili_player.app import MainWindow
from bili_player.cards import ENTRY_MIME, LibraryCard
from bili_player.models import LibraryEntry, Store, Track

BV = "BV1GJ411x7h7"


class InternalDragEnter(QDragEnterEvent):
    def __init__(self, source, mime):
        super().__init__(QPoint(20, 20), Qt.DropAction.MoveAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self._source = source

    def source(self):
        return self._source


class InternalDrop(QDropEvent):
    def __init__(self, source, mime):
        super().__init__(QPointF(20, 20), Qt.DropAction.MoveAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self._source = source

    def source(self):
        return self._source


class TileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "library.json"
        self.window = MainWindow(self.path)
        self.window._load_cover = lambda *_: None
        self.window._error = Mock()
        self.entries = [
            LibraryEntry("First album", "", "album", resolved=True),
            LibraryEntry("First single", BV, tracks=[Track("My track", BV, 123, 1, artist="Manually entered")], resolved=True),
            LibraryEntry("Second album with a very very long title that should never resize its square tile", "", "album", resolved=True),
            LibraryEntry("Second single", BV, page=2, resolved=True),
        ]
        self.window.entries = self.entries.copy()
        self.window._render_library()
        self.window.show()
        QTest.qWait(30)

    def tearDown(self):
        self.window._quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def cards(self):
        return [self.window.grid.itemAt(i).widget() for i in range(self.window.grid.count())]

    def mime(self, card):
        mime = QMimeData()
        mime.setData(ENTRY_MIME, card.entry_id.encode("utf-8"))
        return mime

    def test_all_shows_folders_and_only_singles_outside_every_album(self):
        self.window._normalize_library()
        first, second = self.entries[1], self.entries[3]
        self.entries[0].tracks = list(first.tracks)
        # Folder membership is explicit by entity ID, independent of video source.
        second.tracks = [Track("Second song", BV, 456, 2, song_id=second.id)]
        self.entries[2].tracks = list(second.tracks)
        self.window._normalize_library()
        self.window._render_library()
        self.assertEqual([c.entry_id for c in self.cards()], [self.entries[0].id, self.entries[2].id])
        self.window.tabs.setCurrentIndex(2)
        self.assertEqual({c.entry_id for c in self.cards()}, {first.id, second.id})
        self.window.tabs.setCurrentIndex(1)
        self.assertEqual([c.entry_id for c in self.cards()], [self.entries[0].id, self.entries[2].id])

    def test_single_reappears_only_after_last_album_reference_is_removed(self):
        self.window._normalize_library()
        song = self.entries[1]
        for album in (self.entries[0], self.entries[2]):
            album.tracks = list(song.tracks)
        self.window._render_library()
        self.assertNotIn(song.id, [c.entry_id for c in self.cards()])
        self.window.delete_library_entries([self.entries[0].id], album_contents="keep")
        self.assertNotIn(song.id, [c.entry_id for c in self.cards()])
        edited = self.entries[2]
        from dataclasses import replace
        self.window.save_album(replace(edited, tracks=[]))
        self.assertIn(song.id, [c.entry_id for c in self.cards()])
        self.assertIs(self.window._find(song.id), song)

    def test_all_filter_uses_membership_before_search_without_deleting_music(self):
        self.window._normalize_library()
        song = self.entries[1]
        self.entries[0].tracks = list(song.tracks)
        self.window.search.setText("First single")
        self.assertNotIn(song.id, [getattr(c, "entry_id", None) for c in self.cards()])
        self.window.tabs.setCurrentIndex(2)
        self.assertEqual([c.entry_id for c in self.cards()], [song.id])
        self.assertIs(self.window._find(song.id), song)

    def test_album_and_single_covers_remain_square_after_resize_and_filter(self):
        for width in (1020, 1220, 1600):
            self.window.resize(width, 820)
            QTest.qWait(25)
            cards = self.cards()
            self.assertEqual(len(cards), 4)
            for card in cards:
                self.assertIsInstance(card, LibraryCard)
                self.assertGreater(card.height(), card.width())
                self.assertGreaterEqual(card.width(), 224)
                self.assertLessEqual(card.width(), 284)
                self.assertEqual(card.artwork.width(), card.artwork.height())
                self.assertTrue(card.rect().contains(card.artwork.geometry()))
                self.assertTrue(card.rect().contains(card.title_label.geometry()))
                self.assertTrue(card.rect().contains(card.subtitle_label.geometry()))
                self.assertTrue(card.artwork.rect().contains(card.play_button.geometry()))
                self.assertFalse(hasattr(card, "grip"))
        self.window.tabs.setCurrentIndex(1)
        QTest.qWait(25)
        self.assertEqual(len(self.cards()), 2)
        self.assertTrue(all(c.artwork.width() == c.artwork.height() for c in self.cards()))

    def test_drop_swaps_exact_entries_and_persists_without_touching_queue(self):
        self.window._append_tracks([self.entries[1].tracks[0]])
        self.window.current_id = self.entries[1].tracks[0].id
        queue = self.window._tracks()
        source, target = self.cards()[0], self.cards()[3]
        mime = self.mime(source)
        enter = InternalDragEnter(source, mime)
        target.dragEnterEvent(enter)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(target.property("dropTarget"))
        drop = InternalDrop(source, mime)
        with patch.object(self.window.player, "stop") as stop, patch.object(self.window.player, "setSource") as set_source:
            target.dropEvent(drop)
            self.assertTrue(drop.isAccepted())
            self.app.processEvents()
            stop.assert_not_called()
            set_source.assert_not_called()
        expected = [self.entries[3].id, self.entries[1].id, self.entries[2].id, self.entries[0].id]
        self.assertEqual([entry.id for entry in self.window.entries], expected)
        self.assertEqual([entry.id for entry in Store(self.path).load()[0]], expected)
        self.assertEqual(self.window._tracks(), queue)
        self.window._quitting = True
        self.window.close()
        reopened = MainWindow(self.path)
        try:
            self.assertEqual([entry.id for entry in reopened.entries], expected)
        finally:
            reopened._quitting = True
            reopened.close()
            reopened.deleteLater()

    def test_filtered_swap_changes_only_two_global_positions(self):
        self.window.tabs.setCurrentIndex(1)
        self.window.swap_library_entries(self.entries[0].id, self.entries[2].id)
        self.assertEqual([e.id for e in self.window.entries], [self.entries[i].id for i in (2, 1, 0, 3)])
        self.window.tabs.setCurrentIndex(0)
        self.window.search.setText("single")
        self.window.swap_library_entries(self.entries[1].id, self.entries[3].id)
        self.assertEqual([e.id for e in self.window.entries], [self.entries[i].id for i in (2, 3, 0, 1)])
        self.assertEqual(len(self.cards()), 2)

    def test_invalid_external_same_and_deleted_drop_cannot_change_library(self):
        source, target = self.cards()[:2]
        external = LibraryCard(source.entry_id, "another window", "Drag")
        try:
            for origin, mime in ((target, self.mime(target)), (external, self.mime(external)), (source, QMimeData())):
                event = InternalDrop(origin, mime)
                target.dropEvent(event)
                self.assertFalse(event.isAccepted())
            original = [e.id for e in self.window.entries]
            self.window.swap_library_entries("deleted entry", target.entry_id)
            self.assertEqual([e.id for e in self.window.entries], original)
        finally:
            external.deleteLater()

    def test_cover_click_still_opens_album_but_drag_suppresses_click(self):
        card = self.cards()[0]
        art = next(w for w in card.findChildren(QPushButton) if hasattr(w, "pixmap"))
        with patch.object(self.window, "open_entry") as open_album:
            QTest.mouseClick(art, Qt.MouseButton.LeftButton)
            open_album.assert_called_once()
        with patch.object(card, "_start_drag") as start_drag, patch.object(self.window, "open_entry") as open_album:
            QTest.mousePress(art, Qt.MouseButton.LeftButton, pos=QPoint(15, 15))
            moved = QPointF(15 + QApplication.startDragDistance() + 10, 15)
            global_position = QPointF(art.mapToGlobal(moved.toPoint()))
            event = QMouseEvent(QEvent.Type.MouseMove, moved, global_position,
                                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(art, event)
            QTest.mouseRelease(art, Qt.MouseButton.LeftButton, pos=moved.toPoint())
            start_drag.assert_called_once()
            open_album.assert_not_called()

    def test_hover_reveals_play_and_leaving_hides_controls(self):
        card = self.cards()[1]
        self.window.search.setFocus()
        card._set_hovered(False)
        self.assertTrue(card.play_button.isHidden())
        enter = QEnterEvent(QPointF(30, 30), QPointF(30, 30), QPointF(card.mapToGlobal(QPoint(30, 30))))
        QApplication.sendEvent(card, enter)
        self.assertFalse(card.play_button.isHidden())
        self.assertFalse(hasattr(card, "grip"))
        with patch.object(self.window, "resolve_entry") as resolve, patch.object(self.window, "open_entry") as details:
            QTest.mouseClick(card.play_button, Qt.MouseButton.LeftButton)
            resolve.assert_called_once_with(self.entries[1], True)
            details.assert_not_called()
        self.window.search.setFocus()
        QApplication.sendEvent(card, QEvent(QEvent.Type.Leave))
        self.assertTrue(card.play_button.isHidden())

    def test_play_buttons_recheck_pointer_when_leave_event_is_missing(self):
        first, second = self.cards()[:2]
        self.window.search.setFocus()
        first._set_hovered(True)
        second._set_hovered(True)
        # The old buttons received no leave event. Only the second card is hit now.
        with patch.object(QApplication, "widgetAt", return_value=second.play_button):
            QTest.qWait(250)
            self.assertTrue(first.play_button.isHidden())
            self.assertFalse(first._hover_timer.isActive())
            self.assertFalse(second.play_button.isHidden())
        # The pointer can remain in the card's rectangle while another widget covers it.
        with patch.object(QApplication, "widgetAt", return_value=self.window.search):
            QTest.qWait(250)
            self.assertTrue(second.play_button.isHidden())
            self.assertFalse(second._hover_timer.isActive())

    def test_single_cover_opens_details_without_queueing_or_playing(self):
        card = self.cards()[1]
        with patch.object(self.window, "open_entry") as details, patch.object(self.window, "resolve_entry") as resolve:
            QTest.mouseClick(card.artwork, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
            details.assert_called_once_with(self.entries[1])
            resolve.assert_not_called()
        self.assertEqual(self.window.queue.count(), 0)

    def test_right_click_menu_available_on_cover_title_play_and_card(self):
        card = self.cards()[1]
        card._set_hovered(True)
        for widget in (card, card.artwork, card.title_label, card.subtitle_label, card.play_button):
            with self.subTest(widget=widget.objectName()), patch.object(self.window, "_entry_menu") as menu:
                point = QPoint(5, 5)
                global_point = widget.mapToGlobal(point)
                event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, global_point)
                QApplication.sendEvent(widget, event)
                menu.assert_called_once_with(self.entries[1], global_point)

    def test_right_click_play_and_queue_actions_are_distinct_and_delete_persists(self):
        entry = self.entries[1]
        menu = self.window._build_entry_menu(entry)
        actions = {a.data(): a for a in menu.actions() if not a.isSeparator()}
        self.assertTrue({"play", "queue", "details", "edit", "add_album", "source", "delete"}.issubset(actions))
        with patch.object(self.window, "resolve_entry") as resolve:
            actions["play"].trigger()
            resolve.assert_called_once_with(entry, True)
            resolve.reset_mock()
            actions["queue"].trigger()
            resolve.assert_called_once_with(entry, False)
        self.window.resolve_entry(entry, False)
        track_ids = [t.id for t in self.window._tracks()]
        actions["delete"].trigger()
        self.assertIsNone(self.window._find(entry.id))
        self.assertNotIn(entry.id, [e.id for e in Store(self.path).load()[0]])
        self.assertEqual([t.id for t in self.window._tracks()], track_ids)
        menu.deleteLater()

    def test_background_library_update_waits_until_native_drag_finishes(self):
        source = self.cards()[0]
        extra = LibraryEntry("Arrived during drag", "", "album", resolved=True)

        def native_drag(_action):
            self.assertTrue(self.window._library_drag_active)
            self.window.entries.append(extra)
            self.window._render_library()
            self.app.processEvents()
            self.assertTrue(self.window._library_render_pending)
            self.assertFalse(sip.isdeleted(source))
            self.assertEqual(self.window.grid.count(), 4)

        with patch("bili_player.cards.QDrag.exec", side_effect=native_drag):
            source._start_drag()
        QTest.qWait(20)
        self.assertFalse(self.window._library_drag_active)
        self.assertFalse(self.window._library_render_pending)
        self.assertEqual(len(self.cards()), 5)

    def test_drag_cleanup_and_deferred_drop_tolerate_deleted_widgets(self):
        source, target = self.cards()[:2]
        mime = self.mime(source)
        target.dropEvent(InternalDrop(source, mime))
        sip.delete(target)
        # The target's queued signal must not access the deleted wrapper.
        self.app.processEvents()
        with patch("bili_player.cards.QDrag.exec", side_effect=lambda _action: sip.delete(source)):
            source._start_drag()

    def wheel(self, target, delta=120, control=True):
        local = QPointF(25, 25)
        global_pos = QPointF(target.mapToGlobal(local.toPoint()))
        event = QWheelEvent(local, global_pos, QPoint(0, 0), QPoint(0, delta),
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.ControlModifier if control else Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(target, event)
        QTest.qWait(10)

    def test_theme_play_color_follows_light_and_dark_without_green(self):
        from bili_player.theme import PALETTES
        for theme in ("light", "dark"):
            self.window.set_theme(theme)
            card = self.cards()[0]
            card._set_hovered(True)
            self.window.search.setFocus()
            image = card.play_button.grab().toImage()
            actual = image.pixelColor(12, 30)
            expected = QColor(PALETTES[theme]["accent"])
            self.assertEqual(actual.name(), expected.name())
            self.assertFalse(hasattr(card, "grip"))
            self.assertFalse(card.findChildren(QPushButton, "cardGrip"))

    def test_ctrl_wheel_scales_over_cover_without_affecting_order_or_queue(self):
        card = self.cards()[0]
        original_width = card.width()
        self.window._append_tracks([self.entries[1].tracks[0]])
        original_ids = [e.id for e in self.window.entries]
        with patch.object(self.window.player, "stop") as stop:
            self.wheel(card.artwork, 120)
            self.assertEqual(self.window.card_scale, 1.1)
            self.assertGreater(card.width(), original_width)
            self.assertEqual(card.artwork.width(), card.artwork.height())
            self.wheel(card.play_button, -120)
            self.assertEqual(self.window.card_scale, 1.0)
            self.wheel(card.subtitle_label, 240)
            self.assertEqual(self.window.card_scale, 1.2)
            stop.assert_not_called()
        self.assertEqual([e.id for e in self.window.entries], original_ids)
        self.assertEqual(len(self.window._tracks()), 1)
        self.assertFalse(hasattr(self.window, "zoom_button"))
        self.window._save()
        restored = Store(self.path)
        restored.load()
        self.assertEqual(restored.settings["card_scale"], 1.2)
        reopened = MainWindow(self.path)
        try:
            self.assertEqual(reopened.card_scale, 1.2)
            self.assertEqual(reopened.cards.scale, 1.2)
        finally:
            reopened._quitting = True
            reopened.close()
            reopened.deleteLater()

    def test_zoom_limits_partial_wheel_and_regular_scroll(self):
        card = self.cards()[0]
        self.wheel(card, 60)
        self.assertEqual(self.window.card_scale, 1.0)
        self.wheel(card, 60)
        self.assertEqual(self.window.card_scale, 1.1)
        self.wheel(self.window.cards, 12000)
        self.assertEqual(self.window.card_scale, 1.6)
        self.wheel(self.window.cards, -12000)
        self.assertEqual(self.window.card_scale, 0.7)
        self.assertEqual(card.artwork.width(), card.artwork.height())
        self.assertTrue(card.artwork.rect().contains(card.play_button.geometry()))
        self.wheel(card.artwork, 120, control=False)
        self.assertEqual(self.window.card_scale, 0.7)
        self.window.set_card_scale(1.0)
        self.window.search.setText("nothing matches")
        self.wheel(self.window.cards, 120)
        self.assertEqual(self.window.card_scale, 1.1)

    def test_mini_queue_is_borderless_pin_controls_sync_and_preserve_playback(self):
        self.window.set_mini_mode(True)
        self.assertTrue(self.window.mini_queue_button.isFlat())
        self.assertTrue(self.window.mini_queue_button.property("flatIcon"))
        self.assertIn('QPushButton[flatIcon="true"] { border: none;', self.app.styleSheet())
        track = self.entries[1].tracks[0]
        self.window._append_tracks([track])
        self.window.current_id = track.id
        generation = self.window._request
        geometry = self.window.geometry()
        with patch.object(self.window.player, "stop") as stop, patch.object(self.window.player, "setSource") as set_source:
            QTest.mouseClick(self.window.mini_pin_button, Qt.MouseButton.LeftButton)
            self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertTrue(self.window.pin_button.isChecked())
            self.assertTrue(self.window.mini_pin_button.isChecked())
            self.assertTrue(self.window.isVisible())
            self.assertEqual(self.window.geometry(), geometry)
            self.window.set_language("en_US")
            self.assertEqual(self.window.mini_pin_button.toolTip(), "Unpin player")
            self.window.set_theme("dark")
            self.window.set_mini_mode(False)
            self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            QTest.mouseClick(self.window.pin_button, Qt.MouseButton.LeftButton)
            self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertFalse(self.window.mini_pin_button.isChecked())
            stop.assert_not_called()
            set_source.assert_not_called()
        self.assertEqual(self.window.current_id, track.id)
        self.assertEqual(self.window._request, generation)
        self.assertEqual(self.window._tracks(), [track])

    def test_pin_does_not_show_a_hidden_player_and_queue_button_still_toggles(self):
        self.window.hide()
        self.window.set_always_on_top(True)
        self.assertFalse(self.window.isVisible())
        self.window.show()
        self.window.set_mini_mode(True)
        QTest.mouseClick(self.window.mini_queue_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window._mini_queue_open)
        self.assertTrue(self.window.mini_queue_button.isChecked())
        QTest.mouseClick(self.window.mini_queue_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window._mini_queue_open)
        self.assertFalse(self.window.mini_queue_button.isChecked())


if __name__ == "__main__":
    unittest.main()

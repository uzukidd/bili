"""Light and dark palettes shared by the player and its dialogs."""
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

PALETTES = {
    "light": {
        "bg": "#f5f6fa", "panel": "#ffffff", "text": "#273249", "muted": "#7e899e",
        "line": "#e4e8f1", "accent": "#ed7099", "accent_hover": "#dc5b87",
        "soft": "#fcecf2", "disabled": "#b5becd", "art": "#f5e2eb",
    },
    "dark": {
        "bg": "#151923", "panel": "#202635", "text": "#edf0f7", "muted": "#a3aec3",
        "line": "#354057", "accent": "#ee86ad", "accent_hover": "#e36e9b",
        "soft": "#3b293d", "disabled": "#677289", "art": "#342838",
    },
}


def stylesheet(theme: str = "light") -> str:
    p = PALETTES[theme]
    return """
QWidget { font-family: 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif;
    font-size: 13px; color: TEXT; }
QMainWindow, QDialog, QWidget#root, QWidget#cards { background: BG; }
QLabel#brand { font-size: 24px; font-weight: 800; color: ACCENT; }
QLabel#heading { font-size: 26px; font-weight: 800; }
QLabel#muted { color: MUTED; }
QLabel#section { font-size: 16px; font-weight: 700; }
QLabel#cardTitle { font-size: 16px; font-weight: 600; background: transparent; }
QLabel#cardSubtitle { font-size: 13px; color: MUTED; background: transparent; }
QFrame#queuePanel, QFrame#player, QFrame#queueOverlay { background: PANEL; border: 1px solid LINE;
    border-radius: 14px; }
QFrame#card { background: transparent; border: 1px solid transparent; border-radius: 9px; }
QFrame#card:hover { background: PANEL; }
QFrame#card[dropTarget="true"], QFrame#card[selected="true"] { background: SOFT; border-color: ACCENT; }
QCheckBox { spacing: 4px; }
QCheckBox::indicator { width: 18px; height: 18px; }
QPushButton#cardArtwork, QPushButton#hoverPlay { border: none; background: transparent; padding: 0; }
QPushButton[flatIcon="true"] { border: none; background: transparent; padding: 4px; }
QPushButton[flatIcon="true"]:hover, QPushButton[flatIcon="true"]:checked {
    border: none; background: SOFT; }
QPushButton[flatIcon="true"]:checked { color: ACCENT; }
QPushButton { border: 1px solid LINE; background: PANEL; border-radius: 8px; padding: 8px 12px; }
QPushButton:hover { background: SOFT; border-color: ACCENT; }
QPushButton:disabled { color: DISABLED; }
QPushButton#primary { background: ACCENT; color: #ffffff; border: none; font-weight: 700; }
QPushButton#primary:hover { background: ACCENT_HOVER; }
QPushButton#primary:disabled { background: LINE; color: DISABLED; }
QLineEdit, QComboBox, QSpinBox { background: PANEL; border: 1px solid LINE;
    border-radius: 8px; padding: 8px; selection-background-color: ACCENT; }
QComboBox QAbstractItemView { background: PANEL; color: TEXT; selection-background-color: SOFT; }
QTabBar::tab { padding: 9px 20px; color: MUTED; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: ACCENT; border-bottom-color: ACCENT; font-weight: 700; }
QScrollArea { border: none; background: transparent; }
QWidget#detailContent { background: BG; }
QFrame#detailSticky { background: PANEL; border-bottom: 1px solid LINE; }
QFrame#detailTrack { background: transparent; border-bottom: 1px solid LINE; }
QFrame#detailTrack QPushButton { padding: 4px 12px; }
QListWidget#detailTracks { background: transparent; }
QListWidget#detailTracks::item { padding: 0; margin: 0; border-radius: 0; }
QListWidget#detailTracks::item:selected, QListWidget#detailTracks::item:hover { background: SOFT; }
QListWidget { background: PANEL; border: none; border-radius: 8px; outline: none; }
QListWidget::item { padding: 10px 6px; margin: 2px 0; border-radius: 7px; }
QListWidget::item:selected { background: SOFT; color: TEXT; }
QListWidget::item:hover { background: SOFT; }
QListWidget::indicator { width: 16px; height: 16px; }
QSlider::groove:horizontal { height: 4px; background: LINE; border-radius: 2px; }
QSlider::sub-page:horizontal { background: ACCENT; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -4px 0; background: ACCENT; border-radius: 6px; }
QMenu { background: PANEL; border: 1px solid LINE; padding: 5px; }
QMenu::item { padding: 8px 20px; }
QMenu::item:selected { background: SOFT; }
QMenu::item:disabled { color: DISABLED; }
QToolTip { color: TEXT; background: PANEL; border: 1px solid LINE; }
QScrollBar:vertical { background: BG; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: LINE; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
""".replace("ACCENT_HOVER", p["accent_hover"]).replace("DISABLED", p["disabled"]).replace("ACCENT", p["accent"]).replace("PANEL", p["panel"]).replace("MUTED", p["muted"]).replace("TEXT", p["text"]).replace("LINE", p["line"]).replace("SOFT", p["soft"]).replace("BG", p["bg"])


def apply_theme(app: QApplication, theme: str) -> None:
    p = PALETTES[theme]
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, p["bg"]), (QPalette.ColorRole.WindowText, p["text"]),
        (QPalette.ColorRole.Base, p["panel"]), (QPalette.ColorRole.AlternateBase, p["bg"]),
        (QPalette.ColorRole.Text, p["text"]), (QPalette.ColorRole.Button, p["panel"]),
        (QPalette.ColorRole.ButtonText, p["text"]), (QPalette.ColorRole.Highlight, p["accent"]),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, p["panel"]),
        (QPalette.ColorRole.ToolTipText, p["text"]), (QPalette.ColorRole.PlaceholderText, p["muted"]),
        (QPalette.ColorRole.Light, p["line"]), (QPalette.ColorRole.Mid, p["line"]),
        (QPalette.ColorRole.Dark, p["line"]),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(p["disabled"]))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(theme))

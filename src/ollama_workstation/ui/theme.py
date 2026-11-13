"""11vatedTech Intelligence visual system.

The theme is intentionally centralized: pages consume colour roles, type helpers
and the single Qt stylesheet rather than embedding visual values. Luminous
treatment is reserved for live state, focus and primary action.
"""

from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

COLORS = {
    "void": "#04070c",
    "bg0": "#060a11",
    "bg1": "#09111c",
    "bg2": "#0d1724",
    "surface": "#0e1825",
    "surface_alt": "#101f30",
    "surface_high": "#14263a",
    "border": "#1a2b3f",
    "border_soft": "#132235",
    "border_bright": "#2b4b68",
    "text": "#e8f2fb",
    "text_secondary": "#a7bbcf",
    "muted": "#7d93aa",
    "faint": "#526a82",
    "ice": "#c6f5ff",
    "cyan": "#37d8f4",
    "cyan_dim": "#148aa4",
    "blue": "#4a8dff",
    "violet": "#a78bfa",
    "green": "#58e3b1",
    "green_dim": "#1b7c61",
    "amber": "#ffc46b",
    "amber_dim": "#8c6327",
    "red": "#ff7185",
    "red_dim": "#8b3341",
}

TONE_COLORS = {
    "cyan": COLORS["cyan"],
    "blue": COLORS["blue"],
    "violet": COLORS["violet"],
    "green": COLORS["green"],
    "amber": COLORS["amber"],
    "red": COLORS["red"],
    "muted": COLORS["muted"],
    "ice": COLORS["ice"],
}

DISPLAY_FAMILY = "Segoe UI Variable Display"
TEXT_FAMILY = "Segoe UI Variable Text"
MONO_FAMILY = "Cascadia Code"


def _family(candidates: tuple[str, ...], fallback: str) -> str:
    installed = set(QFontDatabase.families())
    for candidate in candidates:
        if candidate in installed:
            return candidate
    return fallback


def display_family() -> str:
    return _family((DISPLAY_FAMILY, "Bahnschrift", "Segoe UI"), "Segoe UI")


def text_family() -> str:
    return _family((TEXT_FAMILY, "Segoe UI"), "Segoe UI")


def mono_family() -> str:
    return _family((MONO_FAMILY, "Cascadia Mono", "Consolas"), "Consolas")


def font(size: int = 10, weight: QFont.Weight = QFont.Weight.Normal, *, mono: bool = False) -> QFont:
    value = QFont(mono_family() if mono else text_family())
    value.setPixelSize(size)
    value.setWeight(weight)
    return value


def display_font(size: int = 28, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    value = QFont(display_family())
    value.setPixelSize(size)
    value.setWeight(weight)
    return value


def color(name: str, alpha: int | None = None) -> QColor:
    value = QColor(TONE_COLORS.get(name, COLORS.get(name, name)))
    if alpha is not None:
        value.setAlpha(max(0, min(255, alpha)))
    return value


def with_alpha(hex_color: str, alpha: int) -> QColor:
    value = QColor(hex_color)
    value.setAlpha(max(0, min(255, alpha)))
    return value


STYLESHEET = Template(
    """
* {
    outline: none;
}
QWidget {
    color: $text;
    font-size: 13px;
    selection-background-color: #1e5b73;
    selection-color: #f3fbff;
}
QWidget#AppRoot, QWidget#Page, QWidget#PageBody, QScrollArea, QScrollArea > QWidget > QWidget {
    background: transparent;
}
QMainWindow {
    background: $bg0;
}
QFrame#Sidebar {
    background: $bg0;
    border-right: 1px solid $border_soft;
}
QFrame#TopRail {
    background: rgba(6, 10, 17, 0.82);
    border-bottom: 1px solid $border_soft;
}
QFrame#Panel {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(16, 29, 44, 0.96), stop:1 rgba(9, 17, 28, 0.96));
    border: 1px solid $border;
    border-radius: 15px;
}
QFrame#PanelRaised {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(21, 39, 58, 0.98), stop:1 rgba(11, 20, 31, 0.98));
    border: 1px solid $border_bright;
    border-radius: 17px;
}
QFrame#Inset {
    background: rgba(5, 11, 19, 0.55);
    border: 1px solid $border_soft;
    border-radius: 11px;
}
QFrame#Hero {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(10, 24, 39, 0.96), stop:0.55 rgba(8, 18, 30, 0.97),
        stop:1 rgba(17, 17, 42, 0.92));
    border: 1px solid $border_bright;
    border-radius: 20px;
}
QFrame#Divider {
    background: $border_soft;
    max-height: 1px;
    border: 0;
}
QLabel {
    background: transparent;
}
QLabel#BrandWord {
    color: $ice;
    font-family: "$display_family";
    font-size: 17px;
    font-weight: 700;
}
QLabel#BrandMeta {
    color: $faint;
    font-size: 9px;
    font-weight: 600;
}
QLabel#PageTitle {
    color: $ice;
    font-family: "$display_family";
    font-size: 31px;
    font-weight: 650;
}
QLabel#PageSubtitle {
    color: $muted;
    font-size: 13px;
}
QLabel#Eyebrow {
    color: $cyan;
    font-size: 10px;
    font-weight: 700;
}
QLabel#SectionTitle {
    color: $text;
    font-family: "$display_family";
    font-size: 17px;
    font-weight: 600;
}
QLabel#SectionMeta {
    color: $muted;
    font-size: 11px;
}
QLabel#CardTitle {
    color: $text;
    font-family: "$display_family";
    font-size: 14px;
    font-weight: 600;
}
QLabel#MetricValue {
    color: $ice;
    font-family: "$display_family";
    font-size: 27px;
    font-weight: 600;
}
QLabel#MetricValueSmall {
    color: $ice;
    font-family: "$display_family";
    font-size: 20px;
    font-weight: 600;
}
QLabel#MetricLabel {
    color: $muted;
    font-size: 10px;
    font-weight: 600;
}
QLabel#Body {
    color: $text_secondary;
}
QLabel#Muted {
    color: $muted;
}
QLabel#Faint {
    color: $faint;
    font-size: 11px;
}
QLabel#Mono {
    color: $text_secondary;
    font-family: "$mono_family";
    font-size: 11px;
}
QLabel#Code {
    color: $cyan;
    background: rgba(36, 98, 120, 0.18);
    border: 1px solid rgba(55, 216, 244, 0.16);
    border-radius: 5px;
    padding: 2px 6px;
    font-family: "$mono_family";
    font-size: 11px;
}
QLabel#HeroTitle {
    color: #f4fbff;
    font-family: "$display_family";
    font-size: 27px;
    font-weight: 600;
}
QLabel#HeroCopy {
    color: $text_secondary;
    font-size: 13px;
}
QLabel#Pill {
    border-radius: 9px;
    padding: 4px 9px;
    font-size: 10px;
    font-weight: 700;
    background: rgba(55, 216, 244, 0.08);
    color: $cyan;
    border: 1px solid rgba(55, 216, 244, 0.18);
}
QLabel#PillGreen {
    border-radius: 9px;
    padding: 4px 9px;
    font-size: 10px;
    font-weight: 700;
    background: rgba(88, 227, 177, 0.08);
    color: $green;
    border: 1px solid rgba(88, 227, 177, 0.18);
}
QLabel#PillAmber {
    border-radius: 9px;
    padding: 4px 9px;
    font-size: 10px;
    font-weight: 700;
    background: rgba(255, 196, 107, 0.08);
    color: $amber;
    border: 1px solid rgba(255, 196, 107, 0.20);
}
QLabel#PillRed {
    border-radius: 9px;
    padding: 4px 9px;
    font-size: 10px;
    font-weight: 700;
    background: rgba(255, 113, 133, 0.08);
    color: $red;
    border: 1px solid rgba(255, 113, 133, 0.20);
}
QLabel#PillViolet {
    border-radius: 9px;
    padding: 4px 9px;
    font-size: 10px;
    font-weight: 700;
    background: rgba(167, 139, 250, 0.09);
    color: $violet;
    border: 1px solid rgba(167, 139, 250, 0.20);
}
QLabel#PillMuted {
    border-radius: 9px;
    padding: 4px 9px;
    font-size: 10px;
    font-weight: 700;
    background: rgba(125, 147, 170, 0.07);
    color: $muted;
    border: 1px solid rgba(125, 147, 170, 0.15);
}
QPushButton {
    background: rgba(19, 35, 52, 0.76);
    border: 1px solid $border;
    border-radius: 9px;
    color: $text_secondary;
    padding: 8px 13px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    background: $surface_high;
    border-color: $border_bright;
    color: $text;
}
QPushButton:focus {
    border-color: $cyan;
    color: $ice;
}
QPushButton:pressed {
    background: $bg2;
}
QPushButton:disabled {
    color: $faint;
    background: rgba(13, 23, 36, 0.5);
    border-color: $border_soft;
}
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #47dcf4, stop:1 #3f8bff);
    border: 1px solid #60e7ff;
    color: #03131c;
    font-weight: 700;
}
QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #76eaff, stop:1 #609dff);
}
QPushButton#Primary:disabled {
    background: #173345;
    border-color: #1c4b61;
    color: #52778a;
}
QPushButton#Secondary {
    background: rgba(55, 216, 244, 0.08);
    border: 1px solid rgba(55, 216, 244, 0.22);
    color: $cyan;
}
QPushButton#Secondary:hover {
    background: rgba(55, 216, 244, 0.15);
    border-color: rgba(55, 216, 244, 0.40);
}
QPushButton#Secondary:disabled {
    background: rgba(14, 27, 41, 0.4);
    border-color: $border_soft;
    color: $faint;
}
QPushButton#Ghost {
    background: transparent;
    border: 1px solid transparent;
    color: $muted;
    padding: 7px 10px;
}
QPushButton#Ghost:hover {
    background: rgba(55, 216, 244, 0.06);
    border-color: $border_soft;
    color: $text;
}
QPushButton#Ghost:disabled {
    background: transparent;
    border-color: transparent;
    color: $faint;
}
QPushButton#Danger {
    background: rgba(255, 113, 133, 0.07);
    border: 1px solid rgba(255, 113, 133, 0.22);
    color: $red;
}
QPushButton#Danger:hover {
    background: rgba(255, 113, 133, 0.14);
}
QPushButton#Danger:disabled {
    background: rgba(17, 26, 38, 0.55);
    border-color: $border_soft;
    color: $faint;
}
QToolButton#Nav {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    color: $muted;
    text-align: left;
    padding: 8px 11px;
    font-size: 12px;
    font-weight: 600;
}
QToolButton#Nav:hover {
    background: rgba(55, 216, 244, 0.05);
    color: $text_secondary;
}
QToolButton#Nav:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(55, 216, 244, 0.14), stop:1 rgba(74, 141, 255, 0.04));
    border: 1px solid rgba(55, 216, 244, 0.18);
    color: $ice;
}
QToolButton#IconButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    color: $muted;
    padding: 5px;
}
QToolButton#IconButton:hover {
    background: rgba(55, 216, 244, 0.08);
    color: $ice;
}
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser {
    background: rgba(6, 13, 22, 0.62);
    border: 1px solid $border;
    border-radius: 9px;
    color: $text;
    padding: 8px 10px;
    selection-background-color: #1c6179;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus {
    border-color: $border_bright;
    background: rgba(7, 16, 27, 0.86);
}
QLineEdit#Search {
    border-radius: 9px;
    padding-left: 12px;
}
QPlainTextEdit#Composer {
    background: transparent;
    border: 0;
    padding: 6px;
    font-size: 13px;
}
QTextBrowser {
    border: 0;
    background: transparent;
    padding: 0;
}
QComboBox {
    background: rgba(11, 23, 36, 0.88);
    border: 1px solid $border;
    border-radius: 8px;
    padding: 7px 28px 7px 10px;
    color: $text_secondary;
    min-height: 16px;
}
QComboBox:hover {
    border-color: $border_bright;
}
QComboBox::drop-down {
    border: 0;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid $muted;
    margin-right: 9px;
}
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $border_bright;
    border-radius: 7px;
    color: $text;
    selection-background-color: #183b50;
    padding: 4px;
    outline: none;
}
QSpinBox, QDoubleSpinBox {
    background: rgba(11, 23, 36, 0.88);
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px 10px;
    color: $text;
}
QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: $border_bright;
}
QCheckBox {
    color: $text_secondary;
    spacing: 9px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid $border_bright;
    background: $bg1;
}
QCheckBox::indicator:checked {
    background: $cyan;
    border-color: $cyan;
}
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    margin: 2px 1px;
}
QScrollBar::handle:vertical {
    background: #263b50;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #36536e;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 9px;
}
QScrollBar::handle:horizontal {
    background: #263b50;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QListWidget, QTreeWidget, QTableWidget {
    background: transparent;
    border: 0;
    color: $text_secondary;
    alternate-background-color: rgba(14, 26, 40, 0.5);
}
QListWidget:focus {
    border: 1px solid rgba(55, 216, 244, 0.16);
    border-radius: 8px;
}
QListWidget::item {
    border-radius: 8px;
    padding: 6px 8px;
    margin: 1px 2px;
}
QListWidget::item:hover {
    background: rgba(55, 216, 244, 0.05);
}
QListWidget::item:selected {
    background: rgba(55, 216, 244, 0.11);
    color: $ice;
    border: 1px solid rgba(55, 216, 244, 0.14);
}
QTreeWidget::item, QTableWidget::item {
    padding: 7px;
    border-bottom: 1px solid $border_soft;
}
QTreeWidget::item:selected, QTableWidget::item:selected {
    background: rgba(55, 216, 244, 0.10);
    color: $ice;
}
QHeaderView::section {
    background: rgba(8, 18, 29, 0.8);
    color: $muted;
    border: 0;
    border-bottom: 1px solid $border;
    padding: 8px;
    font-size: 10px;
    font-weight: 700;
}
QSplitter::handle {
    background: transparent;
    width: 9px;
    height: 9px;
}
QSplitter::handle:hover {
    background: rgba(55, 216, 244, 0.08);
}
QTabWidget::pane {
    border: 0;
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: $muted;
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 8px 13px;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:selected {
    color: $ice;
    border-bottom-color: $cyan;
}
QTabBar::tab:hover {
    color: $text_secondary;
}
QProgressBar {
    background: rgba(29, 47, 65, 0.6);
    border: 0;
    border-radius: 3px;
    height: 5px;
    text-align: center;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $cyan, stop:1 $blue);
    border-radius: 3px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #1a2c3f;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: $cyan;
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 6px;
}
QMenu {
    background: $surface;
    border: 1px solid $border_bright;
    padding: 5px;
    color: $text;
}
QMenu::item {
    padding: 7px 24px 7px 12px;
    border-radius: 5px;
}
QMenu::item:selected {
    background: #183b50;
}
QToolTip {
    background: #17293b;
    color: $text;
    border: 1px solid $border_bright;
    padding: 6px;
}
QStatusBar {
    background: $bg0;
    color: $muted;
    border-top: 1px solid $border_soft;
}
QMessageBox, QFileDialog, QInputDialog {
    background: $bg1;
}
"""
)


def apply_theme(app: QApplication) -> None:
    """Apply the Fusion base, palette and generated stylesheet once."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg0"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["bg1"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["cyan"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLORS["void"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["text"]))
    app.setPalette(palette)
    app.setFont(font(13))
    app.setStyleSheet(
        STYLESHEET.substitute(
            **COLORS,
            display_family=display_family(),
            mono_family=mono_family(),
        )
    )

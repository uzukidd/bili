from PyQt6.QtCore import QSize, QSignalBlocker, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
)

from .mini_cover import mini_cover_preview
from .models import MINI_COVER_STYLES


class SettingsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(10)
        self.theme_label, self.language_label, self.output_label = QLabel(), QLabel(), QLabel()
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("", "light")
        self.theme_combo.addItem("", "dark")
        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", "zh_CN")
        self.language_combo.addItem("English", "en_US")
        self.output_combo = QComboBox()
        self.output_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.output_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form.addRow(self.theme_label, self.theme_combo)
        form.addRow(self.language_label, self.language_combo)
        form.addRow(self.output_label, self.output_combo)
        layout.addLayout(form)
        self.output_hint = self._muted_label()
        layout.addWidget(self.output_hint)

        self.mini_cover_label = QLabel()
        self.mini_cover_label.setObjectName("section")
        layout.addWidget(self.mini_cover_label)
        previews = QHBoxLayout()
        self.mini_cover_group = QButtonGroup(self)
        self.mini_cover_buttons = {}
        for style in MINI_COVER_STYLES:
            preview = QToolButton()
            preview.setObjectName("miniCoverOption")
            preview.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            preview.setIconSize(QSize(128, 112))
            preview.setCheckable(True)
            preview.setCursor(Qt.CursorShape.PointingHandCursor)
            preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            preview.clicked.connect(lambda checked, style=style: owner.set_mini_cover_style(style))
            self.mini_cover_group.addButton(preview)
            self.mini_cover_buttons[style] = preview
            previews.addWidget(preview, 1)
        layout.addLayout(previews)

        cache_panel = QFrame()
        cache_panel.setObjectName("queuePanel")
        cache_layout = QVBoxLayout(cache_panel)
        cache_layout.setContentsMargins(14, 12, 14, 12)
        self.cache_summary = QLabel()
        self.cache_summary.setObjectName("section")
        self.cache_summary.setWordWrap(True)
        self.cache_policy = self._muted_label()
        self.cache_pending = self._muted_label()
        self.clear_cache_button = QPushButton()
        self.clear_cache_button.clicked.connect(owner.clear_audio_cache)
        for widget in (self.cache_summary, self.cache_policy, self.cache_pending):
            cache_layout.addWidget(widget)
        cache_layout.addWidget(self.clear_cache_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(cache_panel)

        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = QPushButton()
        self.close_button.clicked.connect(self.accept)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

        self.theme_combo.activated.connect(lambda: owner.set_theme(self.theme_combo.currentData()))
        self.language_combo.activated.connect(lambda: owner.set_language(self.language_combo.currentData()))
        self.output_combo.activated.connect(lambda: owner.set_audio_output(self.output_combo.currentData()))

    @staticmethod
    def _muted_label():
        label = QLabel()
        label.setObjectName("muted")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def refresh_preferences(self):
        tr = self.owner.tr
        self.setWindowTitle(tr("设置"))
        self.theme_label.setText(tr("主题"))
        self.language_label.setText(tr("语言"))
        self.output_label.setText(tr("音频输出设备"))
        self.theme_combo.setAccessibleName(self.theme_label.text())
        self.language_combo.setAccessibleName(self.language_label.text())
        self.output_combo.setAccessibleName(self.output_label.text())
        self.theme_combo.setItemText(0, tr("白天模式"))
        self.theme_combo.setItemText(1, tr("夜晚模式"))
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.owner.theme))
        self.language_combo.setCurrentIndex(self.language_combo.findData(self.owner.language))
        self.close_button.setText(tr("关闭"))
        self.refresh_mini_cover()

    def refresh_mini_cover(self):
        self.mini_cover_label.setText(self.owner.tr("迷你播放器样式"))
        for style, title in zip(MINI_COVER_STYLES, ("经典封面", "圆形唱片")):
            button = self.mini_cover_buttons[style]
            button.setText(self.owner.tr(title))
            button.setIcon(QIcon(mini_cover_preview(style, self.owner.theme, button.devicePixelRatioF())))
            button.setChecked(style == self.owner.mini_cover_style)

    def refresh_outputs(self, devices, default, selected):
        tr = self.owner.tr
        combo = self.output_combo
        with QSignalBlocker(combo):
            combo.clear()
            description = default.description() if not default.isNull() else ""
            combo.addItem(tr("系统默认 · {device}", device=description) if description else tr("系统默认"), "")
            for device in devices:
                combo.addItem(device.description(), bytes(device.id()).hex())
            index = combo.findData(selected)
            missing = index < 0
            if missing:
                combo.addItem(tr("已保存设备（当前未连接）"), selected)
                index = combo.count() - 1
            combo.setCurrentIndex(index)
        if not description and not devices:
            hint = tr("未检测到音频输出设备")
        elif missing:
            hint = tr("已保存的设备不可用，当前使用系统默认设备。")
        else:
            hint = ""
        self.output_hint.setText(hint)
        self.output_hint.setVisible(bool(hint))

    def refresh_cache(self, info, busy):
        tr = self.owner.tr
        if info is None:
            self.cache_summary.setText(tr("音频缓存用量暂不可用"))
            pending = 0
        else:
            self.cache_summary.setText(tr("音频缓存：{size} MiB · {count} 首",
                                          size=f"{info['bytes'] / 1024 ** 2:.1f}", count=info['entries']))
            pending = info.get("pending_bytes", 0)
        self.cache_pending.setVisible(bool(pending))
        self.cache_pending.setText(tr("待释放 {size} MiB", size=f"{pending / 1024 ** 2:.1f}"))
        self.cache_policy.setText(tr("上限 1 GiB，超限优先清理最久未使用的缓存"))
        self.clear_cache_button.setText(tr("正在清除音频缓存…" if busy else "清除音频缓存…"))
        self.clear_cache_button.setEnabled(not busy)

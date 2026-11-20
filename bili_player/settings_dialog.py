from PyQt6.QtCore import QSize, QSignalBlocker, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .mini_cover import mini_cover_preview
from .models import MINI_COVER_STYLES


class SettingsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setMinimumWidth(480)
        screen = self.screen()
        self.resize(600, min(610, max(240, screen.availableGeometry().height() - 80)) if screen else 610)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(22, 20, 22, 20)
        shell.setSpacing(14)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(14)
        self.scroll_area.setWidget(content)
        shell.addWidget(self.scroll_area, 1)

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

        self.proxy_label = QLabel()
        self.proxy_combo = QComboBox()
        for mode in ("system", "direct", "manual"):
            self.proxy_combo.addItem("", mode)
        self.proxy_combo.setCurrentIndex(self.proxy_combo.findData(owner.proxy_mode))
        self.proxy_address = QLineEdit(owner.proxy_url)
        self.proxy_address.setPlaceholderText("http://127.0.0.1:7890")
        self.proxy_apply = QPushButton()
        self.proxy_apply.setAutoDefault(False)
        self.proxy_apply.clicked.connect(self._apply_proxy)
        self.proxy_address.returnPressed.connect(self._apply_proxy)
        proxy_row = QHBoxLayout()
        proxy_row.addWidget(self.proxy_address, 1)
        proxy_row.addWidget(self.proxy_apply)
        form.addRow(self.proxy_label, self.proxy_combo)
        form.addRow("", proxy_row)
        self.youtube_session_label = QLabel()
        self.youtube_session_combo = QComboBox()
        self.youtube_session_combo.addItem("", True)
        self.youtube_session_combo.addItem("", False)
        self.youtube_session_combo.addItem("", "file")
        self.youtube_session_combo.activated.connect(self._apply_youtube_session)
        form.addRow(self.youtube_session_label, self.youtube_session_combo)

        self.youtube_cookie_panel = QFrame()
        self.youtube_cookie_panel.setObjectName("queuePanel")
        cookie_layout = QVBoxLayout(self.youtube_cookie_panel)
        cookie_layout.setContentsMargins(14, 10, 14, 10)
        self.youtube_cookie_heading = QLabel()
        self.youtube_cookie_heading.setObjectName("section")
        self.youtube_cookie_hint = self._muted_label()
        self.youtube_cookie_guide = QPushButton()
        self.youtube_cookie_guide.setAutoDefault(False)
        self.youtube_cookie_guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            "https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies")))
        self.youtube_cookie_path = QLineEdit()
        self.youtube_cookie_path.setReadOnly(True)
        self.youtube_cookie_choose = QPushButton()
        self.youtube_cookie_choose.setAutoDefault(False)
        self.youtube_cookie_choose.clicked.connect(self._choose_youtube_cookie_file)
        self.youtube_cookie_clear = QPushButton()
        self.youtube_cookie_clear.setAutoDefault(False)
        self.youtube_cookie_clear.clicked.connect(lambda: self._apply_youtube_cookie_file(""))
        cookie_row = QHBoxLayout()
        cookie_row.addWidget(self.youtube_cookie_path, 1)
        cookie_row.addWidget(self.youtube_cookie_choose)
        cookie_row.addWidget(self.youtube_cookie_clear)
        self.youtube_cookie_status = self._muted_label()
        self._youtube_cookie_error = ""
        cookie_layout.addWidget(self.youtube_cookie_heading)
        cookie_layout.addWidget(self.youtube_cookie_hint)
        cookie_layout.addWidget(self.youtube_cookie_guide, alignment=Qt.AlignmentFlag.AlignLeft)
        cookie_layout.addLayout(cookie_row)
        cookie_layout.addWidget(self.youtube_cookie_status)
        form.addRow(self.youtube_cookie_panel)
        self.youtube_cookie_panel.hide()

        self.proxy_status = self._muted_label()
        layout.addWidget(self.proxy_status)
        self._proxy_result = ""
        self.proxy_combo.currentIndexChanged.connect(self._proxy_inputs_changed)
        self.proxy_address.textEdited.connect(self._proxy_inputs_changed)
        self._proxy_inputs_changed()

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
        shell.addLayout(footer)

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

    def _proxy_inputs_changed(self):
        self._proxy_result = ""
        self.proxy_address.setEnabled(self.proxy_combo.currentData() == "manual")
        self.proxy_status.clear()
        self.proxy_status.hide()

    def _apply_proxy(self):
        result = self.owner.set_proxy_settings(self.proxy_combo.currentData(), self.proxy_address.text())
        if result is None:
            self.proxy_address.setText(self.owner.proxy_url)
            self._proxy_result = "代理设置已应用"
        else:
            self._proxy_result = result
        self._refresh_proxy_status()

    def _refresh_proxy_status(self):
        self.proxy_status.setText(self.owner.tr(self._proxy_result) if self._proxy_result else "")
        self.proxy_status.setVisible(bool(self._proxy_result))

    def _apply_youtube_session(self):
        self._youtube_cookie_error = ""
        self.owner.set_youtube_session(self.youtube_session_combo.currentData())
        self.refresh_youtube_session()

    def _choose_youtube_cookie_file(self):
        tr = self.owner.tr
        filename, _ = QFileDialog.getOpenFileName(
            self, tr("选择 YouTube Cookie 文件"), self.owner.youtube_cookie_file,
            tr("Cookie 文本文件 (*.txt);;所有文件 (*)"),
        )
        if filename:
            self._apply_youtube_cookie_file(filename)

    def _apply_youtube_cookie_file(self, filename):
        self._youtube_cookie_error = self.owner.set_youtube_cookie_file(filename) or ""
        self.refresh_youtube_session()

    def refresh_youtube_session(self):
        tr = self.owner.tr
        manual = self.owner.youtube_use_cookie_file
        filename = self.owner.youtube_cookie_file
        self.youtube_session_label.setText(tr("YouTube 登录会话"))
        self.youtube_session_combo.setAccessibleName(self.youtube_session_label.text())
        for index, text in enumerate(("使用 Chrome 登录会话", "匿名访问", "使用 Cookie 文件")):
            self.youtube_session_combo.setItemText(index, tr(text))
        self.youtube_session_combo.setCurrentIndex(2 if manual else 0 if self.owner.youtube_use_chrome else 1)
        self.youtube_cookie_panel.setVisible(manual)
        self.youtube_cookie_heading.setText(tr("如何导出 YouTube Cookie"))
        self.youtube_cookie_hint.setText("\n".join((
            tr("1. 安装 Get cookies.txt LOCALLY，并允许它在无痕模式下运行。"),
            tr("2. 在无痕窗口登录 YouTube，在同一标签页打开 www.youtube.com/robots.txt。"),
            tr("3. 只导出 youtube.com 的 cookies.txt，关闭无痕窗口，然后在下方选择文件。"),
            tr("Cookie 等同于登录凭据，请勿分享。播放器只保存文件路径。"),
        )))
        self.youtube_cookie_guide.setText(tr("查看 yt-dlp 官方教程"))
        self.youtube_cookie_path.setText(filename)
        self.youtube_cookie_path.setAccessibleName(tr("YouTube Cookie 文件路径"))
        self.youtube_cookie_path.setPlaceholderText(tr("尚未选择 Cookie 文件"))
        self.youtube_cookie_choose.setText(tr("选择 Cookie 文件…"))
        self.youtube_cookie_clear.setText(tr("清除文件"))
        self.youtube_cookie_clear.setEnabled(bool(filename))
        self.youtube_cookie_status.setText(tr(self._youtube_cookie_error) if self._youtube_cookie_error else "")
        self.youtube_cookie_status.setVisible(bool(self._youtube_cookie_error))

    def refresh_preferences(self):
        tr = self.owner.tr
        self.setWindowTitle(tr("设置"))
        self.theme_label.setText(tr("主题"))
        self.language_label.setText(tr("语言"))
        self.output_label.setText(tr("音频输出设备"))
        self.proxy_label.setText(tr("网络代理"))
        self.proxy_combo.setAccessibleName(self.proxy_label.text())
        self.proxy_address.setAccessibleName(tr("代理地址"))
        for index, text in enumerate(("系统代理", "直连", "手动代理")):
            self.proxy_combo.setItemText(index, tr(text))
        self.proxy_apply.setText(tr("应用"))
        self.refresh_youtube_session()
        self._refresh_proxy_status()
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

"""System tray icon with menu for danmuFishpi.

Uses PyQt6 QSystemTrayIcon for native system tray integration.
"""

import logging
from typing import Callable

from PyQt6.QtGui import QIcon, QAction, QActionGroup, QPixmap, QPainter, QColor
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QApplication

logger = logging.getLogger("danmuFishpi.tray")


class Tray:
    """System tray with menu items for settings, mode, theme, and quit."""

    def __init__(self, app: QApplication, icon: QIcon,
                 callbacks: dict):
        """
        Args:
            app: The QApplication instance.
            icon: The tray icon.
            callbacks: dict with keys:
                - on_show_settings: callable
                - on_show_input: callable
                - on_toggle_danmu: callable
                - on_switch_theme: callable(theme: str)
                - on_force_topmost: callable
                - on_quit: callable
        """
        self.callbacks = callbacks
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip("弹幕鱼排")
        self.visible = True

        self._build_menu()
        self.tray.show()

    def _build_menu(self) -> None:
        menu = QMenu()

        # Open settings
        act_settings = menu.addAction("打开设置")
        act_settings.triggered.connect(
            lambda: self.callbacks.get("on_show_settings", lambda: None)())

        menu.addSeparator()

        # Show input
        act_input = menu.addAction("发送消息")
        act_input.triggered.connect(
            lambda: self.callbacks.get("on_show_input", lambda: None)())

        menu.addSeparator()

        # Toggle danmu visibility
        self.act_toggle = menu.addAction("隐藏弹幕")
        self.act_toggle.triggered.connect(self._on_toggle)

        # Force topmost (useful when overlay is hidden by fullscreen game)
        act_topmost = menu.addAction("强制置顶")
        act_topmost.triggered.connect(
            lambda: self.callbacks.get("on_force_topmost", lambda: None)())

        menu.addSeparator()

        # Theme submenu
        theme_menu = menu.addMenu("主题")
        self.theme_group = QActionGroup(theme_menu)
        self.theme_actions = {}
        for theme, label in [("dark", "黑夜模式"),
                             ("light", "白天模式")]:
            act = QAction(label, theme_menu, checkable=True)
            act.setActionGroup(self.theme_group)
            act.triggered.connect(
                lambda checked, t=theme: self._on_switch_theme(t))
            theme_menu.addAction(act)
            self.theme_actions[theme] = act

        # Mode submenu
        mode_menu = menu.addMenu("模式")
        self.mode_group = QActionGroup(mode_menu)
        self.mode_actions = {}
        for mode, label in [("scrolling", "滚动模式"),
                            ("floating", "浮动模式")]:
            act = QAction(label, mode_menu, checkable=True)
            act.setActionGroup(self.mode_group)
            act.triggered.connect(
                lambda checked, m=mode: self._on_switch_mode(m))
            mode_menu.addAction(act)
            self.mode_actions[mode] = act

        menu.addSeparator()

        # Quit
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(
            lambda: self.callbacks.get("on_quit", lambda: None)())

        self.tray.setContextMenu(menu)

    def _on_toggle(self) -> None:
        self.visible = not self.visible
        self.act_toggle.setText("显示弹幕" if not self.visible else "隐藏弹幕")
        self.callbacks.get("on_toggle_danmu", lambda: None)()

    def _on_switch_theme(self, theme: str) -> None:
        self.callbacks.get("on_switch_theme", lambda t: None)(theme)

    def set_theme_checked(self, theme: str) -> None:
        """Update the checked state of theme menu items."""
        if theme in self.theme_actions:
            self.theme_actions[theme].setChecked(True)

    def _on_switch_mode(self, mode: str) -> None:
        self.callbacks.get("on_switch_mode", lambda m: None)(mode)

    def set_mode_checked(self, mode: str) -> None:
        """Update the checked state of mode menu items."""
        if mode in self.mode_actions:
            self.mode_actions[mode].setChecked(True)

    def show_message(self, title: str, message: str) -> None:
        """Show a balloon notification from the tray."""
        self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3000)

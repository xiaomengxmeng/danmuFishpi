"""Main entry point for danmuFishpi Python edition.

Creates a transparent PyQt6 overlay for danmu, a system tray,
global hotkey, and connects to the fishpi chatroom.
"""

import logging
import os
import sys
import threading

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt6.QtWidgets import QApplication

import config as cfg_module
from auth import login as auth_login
from chatroom import Connection as ChatroomConnection
from danmu_engine import DanmuEngine
from overlay import DanmuOverlay
import overlay as overlay_module
from tray import Tray
from hotkey import HotkeyManager
from settings_window import SettingsDialog
from input_box import InputBox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("danmuFishpi")


class MessageBridge(QObject):
    """Bridge signals from background threads to the Qt main thread."""
    new_message = pyqtSignal(dict)
    login_result = pyqtSignal(bool, str, str)  # success, api_key, error
    chatroom_error = pyqtSignal(str)


class App:
    """Main application controller."""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)  # Tray keeps app alive

        # Load config
        self.config = cfg_module.load()
        self.config_path = cfg_module.default_config_path()

        # API key (not persisted directly, obtained from login)
        self.api_key = ""
        self.conn = None
        self.conn_lock = threading.Lock()

        # Create danmu engine and overlay
        self.engine = DanmuEngine()
        self.engine.update_config({
            "danmuMode": self.config.display.danmu_mode,
            "showAvatar": self.config.display.show_avatar,
            "showNickname": self.config.display.show_nickname,
            "showImage": self.config.display.show_image,
            "danmuSpeed": self.config.display.danmu_speed,
            "danmuArea": self.config.display.danmu_area,
            "danmuWidth": self.config.display.danmu_width,
            "danmuHeight": self.config.display.danmu_height,
            "danmuOpacity": self.config.display.danmu_opacity,
            "fontSize": self.config.display.font_size,
        })

        self.overlay = DanmuOverlay(self.engine)
        # Set theme on overlay
        self.overlay.theme = overlay_module.THEME_LIGHT if self.config.theme == "light" else overlay_module.THEME_DARK

        # Set overlay to fullscreen
        screen = self.app.primaryScreen().geometry()
        self.overlay.setGeometry(screen)
        self.engine.set_container_size(
            float(screen.width()), float(screen.height()))

        # Create thread-safe signal bridge
        self.bridge = MessageBridge()
        self.bridge.new_message.connect(self._on_new_message)
        self.bridge.login_result.connect(self._on_login_result)
        self.bridge.chatroom_error.connect(self._on_chatroom_error)

        # Create system tray
        self.tray = Tray(self.app, self._create_icon(), {
            "on_show_settings": self.show_settings,
            "on_toggle_danmu": self.toggle_danmu,
            "on_switch_mode": self.switch_mode,
            "on_switch_theme": self.switch_theme,
            "on_quit": self.quit,
        })
        self.tray.set_mode_checked(self.config.display.danmu_mode)
        self.tray.set_theme_checked(self.config.theme)

        # Create input box (created lazily)
        self.input_box = None

        # Create hotkey manager
        self.hotkey_mgr = HotkeyManager()
        if self.config.hotkey:
            self.hotkey_mgr.register(self.config.hotkey, self.show_input_box)

        # Settings dialog (created lazily)
        self.settings_dialog = None

        # Show overlay and start render loop
        self.overlay.show()
        self.overlay.start_render_loop()

        # Auto-login if credentials saved
        QTimer.singleShot(500, self.auto_login)

    def _create_icon(self) -> QIcon:
        """Create a simple app icon programmatically."""
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Draw a simple fish-like shape
        p.setBrush(QColor(88, 166, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(16, 20, 36, 24)
        # Tail
        tail = __import__("PyQt6.QtGui", fromlist=["QPolygonF"])
        from PyQt6.QtGui import QPolygonF
        from PyQt6.QtCore import QPointF
        poly = QPolygonF([QPointF(50, 32), QPointF(60, 22), QPointF(60, 42)])
        p.drawPolygon(poly)
        # Eye
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(26, 26, 6, 6)
        p.setBrush(QColor(0, 0, 0))
        p.drawEllipse(28, 28, 3, 3)
        p.end()
        return QIcon(pm)

    # ── Chatroom message handling ──────────────────────────────

    def _on_new_message(self, msg: dict) -> None:
        """Handle a new chatroom message (called on main thread)."""
        self.overlay.add_message(msg)

    def _on_login_result(self, success: bool, api_key: str, error: str) -> None:
        """Handle login result (called on main thread)."""
        if success:
            self.api_key = api_key
            self.start_chatroom(api_key)
            if self.settings_dialog:
                self.settings_dialog.on_login_success(self.config.account.username)
            self.tray.show_message("弹幕鱼排", "登录成功，聊天室已连接")
        else:
            if self.settings_dialog:
                self.settings_dialog.on_login_failed(error or "未知错误")
            logger.error(f"Login failed: {error}")

    def _on_chatroom_error(self, error: str) -> None:
        """Handle chatroom errors (called on main thread)."""
        logger.warning(f"Chatroom error: {error}")

    # ── Login / Logout ─────────────────────────────────────────

    def do_login(self, username: str, password: str) -> None:
        """Perform login in a background thread."""
        def _login_thread():
            result = auth_login(username, password)
            if result["success"]:
                # Save credentials
                self.config.account.username = username
                try:
                    self.config.account.password_enc = cfg_module.dpapi_encrypt(password)
                except Exception as e:
                    logger.error(f"Password encryption failed: {e}")
                cfg_module.save(self.config, self.config_path)
                self.bridge.login_result.emit(
                    True, result["api_key"], "")
            else:
                self.bridge.login_result.emit(
                    False, "", result.get("error", "登录失败"))

        threading.Thread(target=_login_thread, daemon=True).start()

    def do_logout(self) -> None:
        """Logout and disconnect."""
        with self.conn_lock:
            if self.conn:
                self.conn.stop()
                self.conn = None
        self.api_key = ""
        self.config.account.username = ""
        self.config.account.password_enc = ""
        cfg_module.save(self.config, self.config_path)
        if self.settings_dialog:
            self.settings_dialog.on_logout()
        self.tray.show_message("弹幕鱼排", "已退出登录")

    def auto_login(self) -> None:
        """Attempt auto-login with saved credentials."""
        if not self.config.account.username or not self.config.account.password_enc:
            return

        try:
            password = cfg_module.dpapi_decrypt(self.config.account.password_enc)
        except Exception as e:
            logger.error(f"Password decryption failed: {e}")
            return

        logger.info(f"Auto-login as {self.config.account.username}...")
        self.do_login(self.config.account.username, password)

    # ── Chatroom ───────────────────────────────────────────────

    def start_chatroom(self, api_key: str) -> None:
        """Start chatroom connection."""
        with self.conn_lock:
            if self.conn:
                self.conn.stop()

            self.conn = ChatroomConnection(
                api_key=api_key,
                on_message=lambda msg: self.bridge.new_message.emit(msg),
                on_error=lambda err: self.bridge.chatroom_error.emit(err),
            )
            self.conn.start()

    def send_message(self, content: str) -> None:
        """Send a message to the chatroom."""
        with self.conn_lock:
            if not self.conn:
                return
        def _send():
            success, error = self.conn.send_message(content)
            if not success:
                logger.error(f"Send failed: {error}")
        threading.Thread(target=_send, daemon=True).start()

    # ── Input Box ──────────────────────────────────────────────

    def show_input_box(self) -> None:
        """Show the floating message input box (bound to global hotkey)."""
        if self.input_box is None:
            self.input_box = InputBox(theme=self.config.theme)
            self.input_box.message_sent.connect(self.send_message)

        # Make sure the overlay doesn't swallow focus/keystrokes
        self.overlay.set_click_through(False)
        self.input_box.set_theme(self.config.theme)
        self.input_box.show_at_bottom()
        self.input_box.activateWindow()

    def hide_input_box(self) -> None:
        """Hide the floating input box and restore overlay click-through."""
        if self.input_box:
            self.input_box.hide()
        self.overlay.set_click_through(True)

    # ── Settings ───────────────────────────────────────────────

    def show_settings(self) -> None:
        """Show the settings dialog."""
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.config)
            self.settings_dialog.login_requested.connect(self.do_login)
            self.settings_dialog.logout_requested.connect(self.do_logout)
            self.settings_dialog.config_saved.connect(self._on_config_saved)
            self.settings_dialog.send_message_requested.connect(self.send_message)

        # Disable click-through while settings are open
        self.overlay.set_click_through(False)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

        # Re-enable click-through when settings dialog closes
        self.settings_dialog.finished.connect(
            lambda: self.overlay.set_click_through(True))

    def _on_config_saved(self, display_config: dict) -> None:
        """Handle config save from settings dialog."""
        self.config.display.danmu_mode = display_config.get("danmuMode", self.config.display.danmu_mode)
        self.config.display.show_avatar = display_config.get("showAvatar", self.config.display.show_avatar)
        self.config.display.show_nickname = display_config.get("showNickname", self.config.display.show_nickname)
        self.config.display.show_image = display_config.get("showImage", self.config.display.show_image)
        self.config.display.danmu_speed = display_config.get("danmuSpeed", self.config.display.danmu_speed)
        self.config.display.danmu_area = display_config.get("danmuArea", self.config.display.danmu_area)
        self.config.display.danmu_width = display_config.get("danmuWidth", self.config.display.danmu_width)
        self.config.display.danmu_height = display_config.get("danmuHeight", self.config.display.danmu_height)
        self.config.display.danmu_opacity = display_config.get("danmuOpacity", self.config.display.danmu_opacity)
        self.config.display.font_size = display_config.get("fontSize", self.config.display.font_size)

        cfg_module.save(self.config, self.config_path)

        # Update overlay
        self.overlay.update_config(display_config, self.config.theme)

        # Update tray menu
        self.tray.set_mode_checked(self.config.display.danmu_mode)

        # Update input box theme
        if self.input_box:
            self.input_box.set_theme(self.config.theme)

        # Re-register hotkey if it changed
        new_hotkey = self.config.hotkey
        if new_hotkey and new_hotkey != self.hotkey_mgr._current_hotkey:
            self.hotkey_mgr.register(new_hotkey, self.show_input_box)

    # ── Tray callbacks ─────────────────────────────────────────

    def toggle_danmu(self) -> None:
        """Toggle danmu visibility."""
        self.overlay.toggle_visibility()

    def switch_mode(self, mode: str) -> None:
        """Switch danmu mode."""
        self.config.display.danmu_mode = mode
        cfg_module.save(self.config, self.config_path)
        self.overlay.update_config({
            "danmuMode": mode,
            "showAvatar": self.config.display.show_avatar,
            "showNickname": self.config.display.show_nickname,
            "showImage": self.config.display.show_image,
            "danmuSpeed": self.config.display.danmu_speed,
            "danmuArea": self.config.display.danmu_area,
            "danmuWidth": self.config.display.danmu_width,
            "danmuHeight": self.config.display.danmu_height,
            "danmuOpacity": self.config.display.danmu_opacity,
            "fontSize": self.config.display.font_size,
        }, self.config.theme)
        self.tray.set_mode_checked(mode)
        if self.settings_dialog:
            self.settings_dialog.update_config(self.config)

    def switch_theme(self, theme: str) -> None:
        """Switch theme."""
        self.config.theme = theme
        cfg_module.save(self.config, self.config_path)
        self.overlay.update_config({
            "danmuMode": self.config.display.danmu_mode,
            "showAvatar": self.config.display.show_avatar,
            "showNickname": self.config.display.show_nickname,
            "showImage": self.config.display.show_image,
            "danmuSpeed": self.config.display.danmu_speed,
            "danmuArea": self.config.display.danmu_area,
            "danmuWidth": self.config.display.danmu_width,
            "danmuHeight": self.config.display.danmu_height,
            "danmuOpacity": self.config.display.danmu_opacity,
            "fontSize": self.config.display.font_size,
        }, theme)
        self.tray.set_theme_checked(theme)
        if self.settings_dialog:
            self.settings_dialog.update_config(self.config)

    # ── Lifecycle ──────────────────────────────────────────────

    def quit(self) -> None:
        """Clean shutdown."""
        with self.conn_lock:
            if self.conn:
                self.conn.stop()
                self.conn = None
        self.hotkey_mgr.unregister()
        self.overlay.stop_render_loop()
        self.app.quit()

    def run(self) -> int:
        """Run the application event loop."""
        return self.app.exec()


def main():
    app = App()
    sys.exit(app.run())


if __name__ == "__main__":
    main()

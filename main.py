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
from tray import Tray
from hotkey import HotkeyManager
from settings_window import SettingsDialog
from input_box import InputBox
from notification import NotificationManager

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
            "danmuMode": "scrolling",
            "showAvatar": self.config.display.show_avatar,
            "showNickname": self.config.display.show_nickname,
            "showImage": self.config.display.show_image,
            "danmuSpeed": self.config.display.danmu_speed,
            "danmuArea": self.config.display.danmu_area,
            "danmuWidth": self.config.display.danmu_width,
            "danmuHeight": self.config.display.danmu_height,
            "danmuOpacity": self.config.display.danmu_opacity,
            "fontSize": self.config.display.font_size,
            "fontFamily": self.config.display.font_family,
        })

        self.overlay = DanmuOverlay(self.engine)
        # Set theme and outline config on overlay
        self.overlay.update_config({
            "danmuMode": "scrolling",
            "showAvatar": self.config.display.show_avatar,
            "showNickname": self.config.display.show_nickname,
            "showImage": self.config.display.show_image,
            "showRedPacket": self.config.display.show_red_packet,
            "showOutline": self.config.display.show_outline,
            "topMargin": self.config.display.top_margin,
            "danmuSpeed": self.config.display.danmu_speed,
            "danmuArea": self.config.display.danmu_area,
            "danmuWidth": self.config.display.danmu_width,
            "danmuHeight": self.config.display.danmu_height,
            "danmuOpacity": self.config.display.danmu_opacity,
            "fontSize": self.config.display.font_size,
            "fontFamily": self.config.display.font_family,
        }, self.config.theme)

        # Show overlay on the available screen area (excluding taskbar)
        screen = self.app.primaryScreen().availableGeometry()
        self.overlay.setGeometry(screen)
        self.engine.set_container_size(
            float(screen.width()), float(screen.height()))

        # Create thread-safe signal bridge
        self.bridge = MessageBridge()
        self.bridge.new_message.connect(self._on_new_message)
        self.bridge.login_result.connect(self._on_login_result)
        self.bridge.chatroom_error.connect(self._on_chatroom_error)

        # Create notification manager (silent, no Windows sound)
        self.notification_manager = NotificationManager(theme=self.config.theme)

        # Create system tray
        self.tray = Tray(self.app, self._create_icon(), {
            "on_show_settings": self.show_settings,
            "on_show_input": self.show_input_box,
            "on_toggle_danmu": self.toggle_danmu,
            "on_switch_theme": self.switch_theme,
            "on_quit": self.quit,
        })
        self.tray.set_theme_checked(self.config.theme)

        # Create input box (created lazily)
        self.input_box = None

        # Settings dialog (created lazily)
        self.settings_dialog = None

        # Create hotkey manager
        self.hotkey_mgr = HotkeyManager()
        self.hotkey_mgr.install_filter()
        self._register_hotkey_with_fallback()
        active_hotkey = self.hotkey_mgr._current_hotkey or "未注册"
        logger.info(f"Current global hotkey: {active_hotkey}")
        if self.config.display.notify_startup:
            self.notification_manager.show(
                "热键已就绪",
                f"按 {active_hotkey} 打开输入框\n也可右键托盘 → 发送消息",
            )

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
        user_id = msg.get("user_id", "")
        nickname = msg.get("nickname", user_id)
        content = msg.get("content", "")[:40]

        # Block list filter
        if user_id and user_id in self.config.display.blocked_user_ids:
            logger.info(f"Blocked message from {nickname}: {content}")
            return

        # Red packet filter
        if msg.get("is_red_packet") and not self.config.display.show_red_packet:
            logger.info(f"Filtered red packet from {nickname}: {content}")
            return

        # Special follow notification
        if user_id and user_id in self.config.display.followed_user_ids:
            logger.info(f"Special follow message from {nickname}: {content}")
            if self.config.display.notify_follow:
                self.notification_manager.show(
                    "特别关注",
                    f"{msg.get('nickname', user_id)}: {msg.get('content', '')[:60]}",
                )

        self.overlay.add_message(msg)
        logger.info(f"Displayed message from {nickname}: {content}")

    def _on_login_result(self, success: bool, api_key: str, error: str) -> None:
        """Handle login result (called on main thread)."""
        if success:
            self.api_key = api_key
            self.start_chatroom(api_key)
            if self.settings_dialog:
                self.settings_dialog.on_login_success(self.config.account.username)
            if self.config.display.notify_login:
                self.notification_manager.show("弹幕鱼排", "登录成功，聊天室已连接")
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
        if self.config.display.notify_login:
            self.notification_manager.show("弹幕鱼排", "已退出登录")

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
                on_status=self._on_chatroom_status,
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

    def _on_chatroom_status(self, connected: bool) -> None:
        """Update settings dialog connection indicator."""
        if self.settings_dialog is not None:
            self.settings_dialog.set_connected(connected)

    # ── Input Box ──────────────────────────────────────────────

    def show_input_box(self) -> None:
        """Show the floating message input box (bound to global hotkey)."""
        active_hotkey = self.hotkey_mgr._current_hotkey or "未注册"
        print(f"[danmuFishpi] 热键 {active_hotkey} 被按下，准备显示输入框", flush=True)
        if self.input_box is None:
            self.input_box = InputBox(theme=self.config.theme)
            self.input_box.message_sent.connect(self.send_message)
            self.input_box.closed.connect(lambda: self.overlay.set_click_through(True))

        # Make sure the overlay doesn't swallow focus/keystrokes
        self.overlay.set_click_through(False)
        self.input_box.set_theme(self.config.theme)
        self.input_box.show_at_bottom()
        self.input_box.raise_()
        self.input_box.activateWindow()
        print("[danmuFishpi] 输入框已显示", flush=True)

    def hide_input_box(self) -> None:
        """Hide the floating input box and restore overlay click-through."""
        if self.input_box:
            self.input_box.hide()
        self.overlay.set_click_through(True)

    def _register_hotkey_with_fallback(self) -> None:
        """Register the configured global hotkey, trying fallbacks if taken."""
        configured = (self.config.hotkey or "alt+space").strip()
        fallbacks = ["alt+space", "ctrl+shift+enter", "ctrl+alt+enter", "ctrl+shift+m", "ctrl+alt+shift+enter"]

        # Build ordered list of candidates without duplicates
        candidates = []
        for hk in [configured] + fallbacks:
            if hk and hk not in candidates:
                candidates.append(hk)

        def _hotkey_wrapper():
            hk = self.hotkey_mgr._current_hotkey or "未知"
            print(f"[danmuFishpi] 全局热键 {hk} 触发", flush=True)
            self.notification_manager.show("热键触发", f"{hk} 已触发，正在打开输入框")
            self.show_input_box()

        for hotkey in candidates:
            if self.hotkey_mgr.register(hotkey, _hotkey_wrapper):
                if hotkey != configured:
                    self.config.hotkey = hotkey
                    cfg_module.save(self.config, self.config_path)
                    self.notification_manager.show(
                        "弹幕鱼排",
                        f"快捷键 '{configured}' 被占用，已自动切换为 '{hotkey}'。",
                    )
                if self.settings_dialog:
                    self.settings_dialog.set_active_hotkey(hotkey)
                return

        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(
            None,
            "弹幕鱼排",
            f"所有候选快捷键（{', '.join(candidates)}）均注册失败，\n"
            "可能已有其他程序占用了这些组合键。请在设置中手动更换。",
        )

    # ── Settings ────────────────────────────────

    def show_settings(self) -> None:
        """Show the settings dialog docked to the right edge of the screen."""
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.config)
            self.settings_dialog.login_requested.connect(self.do_login)
            self.settings_dialog.logout_requested.connect(self.do_logout)
            self.settings_dialog.config_saved.connect(self._on_config_saved)
            self.settings_dialog.send_message_requested.connect(self.send_message)
            # Sync current connection state
            is_connected = self.conn.is_connected if self.conn else False
            self.settings_dialog.set_connected(is_connected)
            self.settings_dialog.set_active_hotkey(self.hotkey_mgr._current_hotkey)

        # Disable click-through while settings are open
        self.overlay.set_click_through(False)
        self.settings_dialog.show()
        self.settings_dialog.set_active_hotkey(self.hotkey_mgr._current_hotkey)
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

        # Position at the right edge of the primary screen
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + screen.width() - self.settings_dialog.width()
        y = screen.y()
        self.settings_dialog.setGeometry(x, y, self.settings_dialog.width(), screen.height())

        # Re-enable click-through when settings dialog closes
        self.settings_dialog.finished.connect(
            lambda: self.overlay.set_click_through(True))

    def _on_config_saved(self, display_config: dict) -> None:
        """Handle config save from settings dialog."""
        self.config.display.danmu_mode = "scrolling"
        self.config.display.show_avatar = display_config.get("showAvatar", self.config.display.show_avatar)
        self.config.display.show_nickname = display_config.get("showNickname", self.config.display.show_nickname)
        self.config.display.show_image = display_config.get("showImage", self.config.display.show_image)
        self.config.display.show_red_packet = display_config.get("showRedPacket", self.config.display.show_red_packet)
        self.config.display.play_sound = display_config.get("playSound", self.config.display.play_sound)
        self.config.display.show_outline = display_config.get("showOutline", self.config.display.show_outline)
        self.config.display.top_margin = display_config.get("topMargin", self.config.display.top_margin)
        self.config.display.notify_startup = display_config.get("notifyStartup", self.config.display.notify_startup)
        self.config.display.notify_login = display_config.get("notifyLogin", self.config.display.notify_login)
        self.config.display.notify_follow = display_config.get("notifyFollow", self.config.display.notify_follow)
        self.config.display.blocked_user_ids = display_config.get("blockedUserIds", self.config.display.blocked_user_ids)
        self.config.display.followed_user_ids = display_config.get("followedUserIds", self.config.display.followed_user_ids)
        self.config.display.danmu_speed = display_config.get("danmuSpeed", self.config.display.danmu_speed)
        self.config.display.danmu_area = display_config.get("danmuArea", self.config.display.danmu_area)
        self.config.display.danmu_width = display_config.get("danmuWidth", self.config.display.danmu_width)
        self.config.display.danmu_height = display_config.get("danmuHeight", self.config.display.danmu_height)
        self.config.display.danmu_opacity = display_config.get("danmuOpacity", self.config.display.danmu_opacity)
        self.config.display.font_size = display_config.get("fontSize", self.config.display.font_size)
        self.config.display.font_family = display_config.get("fontFamily", self.config.display.font_family)

        cfg_module.save(self.config, self.config_path)

        # Update overlay
        self.overlay.update_config(display_config, self.config.theme)

        # Update input box theme
        if self.input_box:
            self.input_box.set_theme(self.config.theme)

        # Re-register hotkey if it changed
        new_hotkey = self.config.hotkey
        if new_hotkey and new_hotkey != self.hotkey_mgr._current_hotkey:
            self._register_hotkey_with_fallback()

    # ── Tray callbacks ─────────────────────────────────────────

    def toggle_danmu(self) -> None:
        """Toggle danmu visibility."""
        self.overlay.toggle_visibility()

    def switch_theme(self, theme: str) -> None:
        """Switch theme."""
        self.config.theme = theme
        cfg_module.save(self.config, self.config_path)
        self.overlay.update_config({
            "danmuMode": "scrolling",
            "showAvatar": self.config.display.show_avatar,
            "showNickname": self.config.display.show_nickname,
            "showImage": self.config.display.show_image,
            "showRedPacket": self.config.display.show_red_packet,
            "showOutline": self.config.display.show_outline,
            "topMargin": self.config.display.top_margin,
            "danmuSpeed": self.config.display.danmu_speed,
            "danmuArea": self.config.display.danmu_area,
            "danmuWidth": self.config.display.danmu_width,
            "danmuHeight": self.config.display.danmu_height,
            "danmuOpacity": self.config.display.danmu_opacity,
            "fontSize": self.config.display.font_size,
            "fontFamily": self.config.display.font_family,
        }, theme)
        self.notification_manager.set_theme(theme)
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

"""Main entry point for danmuFishpi Python edition.

Creates a transparent PyQt6 overlay for danmu, a system tray,
global hotkey, and connects to the fishpi chatroom.
"""

import logging
import os
import sys
import threading
import traceback

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt6.QtWidgets import QApplication

import config as cfg_module
import screen_utils
from auth import login as auth_login
from chatroom import Connection as ChatroomConnection
from danmu_engine import DanmuEngine
from overlay import DanmuOverlay
from tray import Tray
from hotkey import HotkeyManager
from settings_window import SettingsDialog
from input_box import InputBox
from notification import NotificationManager


_LOG_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "DanmuFishpi", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, "app.log")

_handlers: list[logging.Handler] = [logging.FileHandler(_LOG_FILE, encoding="utf-8", mode="a")]

# In a windowed (--noconsole) PyInstaller build, sys.stdout/stderr are None.
# Only attach a stream handler when a real console is available, and force UTF-8
# on Windows to avoid 'gbk' codec errors for Unicode log messages.
if getattr(sys, "stdout", None) is not None:
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass
    _handlers.append(logging.StreamHandler(sys.stdout))
else:
    # Windowed PyInstaller build: replace None stdout/stderr with a dummy so
    # print() and any library that writes to stdout does not crash.
    import io
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger("danmuFishpi")


def _log_unhandled_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.error("Unhandled exception: %s", exc_value)
    logger.error("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))


sys.excepthook = _log_unhandled_exception


def _log_thread_exception(args):
    logger.error("Unhandled exception in thread %s: %s", args.thread, args.exc_value)
    if args.exc_traceback:
        logger.error("".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


if hasattr(threading, "excepthook"):
    threading.excepthook = _log_thread_exception


class MessageBridge(QObject):
    """Bridge signals from background threads to the Qt main thread."""
    new_message = pyqtSignal(dict)
    login_result = pyqtSignal(bool, str, str)  # success, api_key, error
    chatroom_error = pyqtSignal(str)


def _clamp_scale(v) -> float:
    """将相对系数限制在 [0.5, 2.0] 的合法范围内。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 1.0
    return max(0.5, min(2.0, v))


class App:
    """Main application controller."""

    def __init__(self):
        self.app = QApplication(sys.argv)
        from app_icon import render_fish
        self.app.setWindowIcon(QIcon(render_fish(256)))
        self.app.setQuitOnLastWindowClosed(False)  # Tray keeps app alive

        # Load config
        self.config = cfg_module.load()
        self.config_path = cfg_module.default_config_path()

        # 旧版绝对像素 -> 相对系数迁移（需要主屏几何，必须在 QApplication 之后）
        self._migrate_legacy_pixels()

        # API key (not persisted directly, obtained from login)
        self.api_key = ""
        self.conn = None
        self.conn_lock = threading.Lock()

        # 计算目标屏几何（位置/尺寸/基准像素都依赖它，必须先于创建后确定）
        cfg_module.set_target_screen(self.config.display.display_screen)
        self._last_screen_idx = self.config.display.display_screen
        screen = cfg_module.target_screen_geometry(self.app)

        # Create danmu engine and overlay
        self.engine = DanmuEngine()
        # 字号/边距/卡片宽度由「目标屏基准 × 相对系数」推导，切屏自动适配
        self.engine.update_config(self._absolute_display(screen))

        self.overlay = DanmuOverlay(self.engine)
        # Set theme and outline config on overlay
        self.overlay.update_config(self._absolute_display(screen), self.config.theme)

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
            "on_switch_mode": self.switch_mode,
            "on_force_topmost": self.force_overlay_topmost,
            "on_quit": self.quit,
        })
        self.tray.set_theme_checked(self.config.theme)
        self.tray.set_mode_checked(self.config.display.danmu_mode)

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

        # Boss key to toggle danmu visibility
        self.boss_key_mgr = HotkeyManager()
        self.boss_key_mgr.install_filter()
        boss_key = (self.config.boss_key or "f10").strip()
        if self.boss_key_mgr.register(boss_key, self.toggle_danmu):
            logger.info(f"Boss key {boss_key} registered")
            print(f"[danmuFishpi] 老板键 {boss_key} 已注册", flush=True)
        else:
            logger.warning(f"Failed to register boss key {boss_key}")
            print(f"[danmuFishpi] 老板键 {boss_key} 注册失败", flush=True)
        if self.config.display.notify_startup:
            self.notification_manager.show(
                "热键已就绪",
                f"按 {active_hotkey} 打开输入框\n也可右键托盘 → 发送消息",
            )

        # Show overlay and start render loop
        self.overlay.show()
        self.overlay.start_render_loop()

        # React to monitor hot-plug (plug in / unplug a display).
        self.app.screenAdded.connect(self._on_screens_changed)
        self.app.screenRemoved.connect(self._on_screens_changed)

        # Auto-login if credentials saved
        QTimer.singleShot(500, self.auto_login)

    def _create_icon(self) -> QIcon:
        """Create the fish tray icon (shared artwork in app_icon.py)."""
        from app_icon import render_fish
        return QIcon(render_fish(64))

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
            conn = self.conn
            if not conn:
                logger.warning("Cannot send message: not connected")
                return

        def _send():
            try:
                success, error = conn.send_message(content)
                if not success:
                    logger.error(f"Send failed: {error}")
            except Exception as e:
                logger.exception(f"Exception while sending message: {e}")

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
        configured = (self.config.hotkey or "f9").strip()
        fallbacks = ["f9", "ctrl+shift+enter", "ctrl+alt+enter", "ctrl+shift+m", "ctrl+alt+shift+enter"]

        # Build ordered list of candidates without duplicates
        candidates = []
        for hk in [configured] + fallbacks:
            if hk and hk not in candidates:
                candidates.append(hk)

        def _hotkey_wrapper():
            hk = self.hotkey_mgr._current_hotkey or "未知"
            print(f"[danmuFishpi] 全局热键 {hk} 触发", flush=True)
            if self.input_box is not None and self.input_box.isVisible():
                self.hide_input_box()
                self.notification_manager.show("热键触发", f"{hk} 已触发，已关闭输入框")
            else:
                self.show_input_box()
                self.notification_manager.show("热键触发", f"{hk} 已触发，正在打开输入框")

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

        # Position at the right edge of the target screen (跟随弹幕所在屏幕)
        screen = cfg_module.target_screen_geometry(self.app)
        x = screen.x() + screen.width() - self.settings_dialog.width()
        y = screen.y()
        self.settings_dialog.setGeometry(x, y, self.settings_dialog.width(), screen.height())

        # Re-enable click-through when settings dialog closes
        self.settings_dialog.finished.connect(
            lambda: self.overlay.set_click_through(True))

    def _absolute_display(self, screen) -> dict:
        """把 config 中的相对系数解析为「当前目标屏」下的绝对像素 dict。

        切屏时传入不同 screen，基准像素随之变化，从而自动适配目标屏。
        底层 overlay/engine 只认绝对像素，本方法负责把系数换算成绝对像素。
        """
        base = screen_utils.baseline_sizes(screen)
        d = self.config.display
        return {
            "danmuMode": d.danmu_mode,
            "showAvatar": d.show_avatar,
            "showNickname": d.show_nickname,
            "showImage": d.show_image,
            "showRedPacket": d.show_red_packet,
            "showOutline": d.show_outline,
            "simpleMode": d.simple_mode,
            "topMargin": int(round(d.top_margin)),
            "danmuSpeed": d.danmu_speed,
            "danmuArea": d.danmu_area,
            "danmuWidth": 100,
            "danmuHeight": 100,
            "danmuOpacity": d.danmu_opacity,
            "fontSize": max(8, int(round(base["font_size"] * d.font_scale / 100.0))),
            "fontFamily": d.font_family,
            "floatingCorner": d.floating_corner,
            "floatingDwellSeconds": d.floating_dwell_seconds,
            "floatingMaxItems": d.floating_max_items,
            "floatingCardWidth": max(80, int(round(base["floating_card_width"] * d.floating_card_scale))),
            "floatingFontSize": max(8, int(round(base["floating_font_size"] * d.floating_font_scale / 100.0))),
        }

    def _migrate_legacy_pixels(self) -> None:
        """将旧版绝对像素配置迁移为相对系数（基于主屏基准）。

        旧 config.json 存的是 fontSize/topMargin/floatingCardWidth/floatingFontSize 等
        绝对磅值；启动时用主屏基准反推出系数，使主屏视觉与升级前完全一致，
        副屏则自动按比例适配。

        旧值为磅值，新基准为像素值，换算系数 96/72。
        """
        legacy = self.config.display.legacy_pixels
        if not legacy:
            return
        try:
            primary = self.app.primaryScreen().availableGeometry()
            base = screen_utils.baseline_sizes(primary)
        except Exception:
            base = {"font_size": 32, "top_margin": 21,
                    "floating_card_width": 240, "floating_font_size": 21}
        _PT2PX = 96.0 / 72.0  # 旧磅值 → 新像素
        d = self.config.display
        if "font_size" in legacy:
            px = legacy["font_size"] * _PT2PX
            d.font_scale = max(20, min(100, int(round(px / base["font_size"] * 100))))
        if "top_margin" in legacy:
            ratio = legacy["top_margin"] / (base["top_margin"] or 1)
            d.top_margin = max(0, min(100, int(round((ratio - 0.5) * 20))))
        if "floating_card_width" in legacy:
            d.floating_card_scale = _clamp_scale(legacy["floating_card_width"] / (base["floating_card_width"] or 1))
        if "floating_font_size" in legacy:
            px = legacy["floating_font_size"] * _PT2PX
            d.floating_font_scale = max(20, min(100, int(round(px / base["floating_font_size"] * 100))))
        d.legacy_pixels = {}
        try:
            cfg_module.save(self.config, self.config_path)
        except Exception as e:
            logger.error(f"迁移配置保存失败: {e}")

    def _apply_screen_geometry(self) -> None:
        """Move the overlay + danmu container to the configured screen."""
        cfg_module.set_target_screen(self.config.display.display_screen)
        screen = cfg_module.target_screen_geometry(self.app)
        self.overlay.setGeometry(screen)
        self.engine.set_container_size(
            float(screen.width()), float(screen.height()))
        # 切屏后基准像素变化，重新下发绝对像素配置（系数 × 新基准），实现自动适配
        self.overlay.update_config(self._absolute_display(screen), self.config.theme)
        # Clear in-flight danmu so positions recompute on the new geometry.
        try:
            self.overlay.engine.clear_all()
        except Exception:
            pass

    def _on_screens_changed(self, *args) -> None:
        """Handle monitor hot-plug (plug in / unplug a display).

        - Refresh the settings screen list if the dialog is open.
        - Re-apply the overlay geometry to the configured screen. If the screen
          that was removed is the one we were using, target_screen_geometry()
          falls back to the primary screen automatically.
        """
        logger.info("显示器配置发生变化，重新应用屏幕几何")
        if self.settings_dialog is not None:
            try:
                self.settings_dialog._refresh_screen_list()
            except Exception as e:
                logger.error(f"刷新设置窗口显示器列表失败: {e}")
        try:
            self._apply_screen_geometry()
        except Exception as e:
            logger.error(f"重新应用屏幕几何失败: {e}")

    def _on_config_saved(self, display_config: dict) -> None:
        """Handle config save from settings dialog."""
        self.config.display.danmu_mode = display_config.get("danmuMode", self.config.display.danmu_mode)
        self.config.display.show_avatar = display_config.get("showAvatar", self.config.display.show_avatar)
        self.config.display.show_nickname = display_config.get("showNickname", self.config.display.show_nickname)
        self.config.display.show_image = display_config.get("showImage", self.config.display.show_image)
        self.config.display.show_red_packet = display_config.get("showRedPacket", self.config.display.show_red_packet)
        self.config.display.play_sound = display_config.get("playSound", self.config.display.play_sound)
        self.config.display.show_outline = display_config.get("showOutline", self.config.display.show_outline)
        self.config.display.simple_mode = display_config.get("simpleMode", self.config.display.simple_mode)
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
        self.config.display.font_scale = display_config.get("fontScale", self.config.display.font_scale)
        self.config.display.font_family = display_config.get("fontFamily", self.config.display.font_family)
        self.config.display.floating_corner = display_config.get("floatingCorner", self.config.display.floating_corner)
        self.config.display.floating_dwell_seconds = display_config.get("floatingDwellSeconds", self.config.display.floating_dwell_seconds)
        self.config.display.floating_max_items = display_config.get("floatingMaxItems", self.config.display.floating_max_items)
        self.config.display.floating_card_scale = display_config.get("floatingCardScale", self.config.display.floating_card_scale)
        self.config.display.floating_font_scale = display_config.get("floatingFontScale", self.config.display.floating_font_scale)
        self.config.display.display_screen = display_config.get("displayScreen", self.config.display.display_screen)

        cfg_module.save(self.config, self.config_path)

        # Update overlay（用「目标屏基准 × 相对系数」重新下发绝对像素配置）
        cfg_module.set_target_screen(self.config.display.display_screen)
        screen = cfg_module.target_screen_geometry(self.app)
        self.overlay.update_config(self._absolute_display(screen), self.config.theme)

        # Update input box theme
        if self.input_box:
            self.input_box.set_theme(self.config.theme)

        # Re-register hotkeys if they changed
        new_hotkey = self.config.hotkey
        if new_hotkey and new_hotkey != self.hotkey_mgr._current_hotkey:
            self._register_hotkey_with_fallback()

        new_boss_key = self.config.boss_key
        if new_boss_key and new_boss_key != self.boss_key_mgr._current_hotkey:
            self.boss_key_mgr.unregister()
            if self.boss_key_mgr.register(new_boss_key, self.toggle_danmu):
                logger.info(f"Boss key changed to {new_boss_key}")
            else:
                logger.warning(f"Failed to change boss key to {new_boss_key}")

        # Re-apply screen selection (e.g. secondary monitor) if it changed
        if self.config.display.display_screen != self._last_screen_idx:
            self._apply_screen_geometry()
            self._last_screen_idx = self.config.display.display_screen

    # ── Tray callbacks ─────────────────────────────────────────

    def toggle_danmu(self) -> None:
        """Toggle danmu visibility."""
        self.overlay.toggle_visibility()

    def force_overlay_topmost(self) -> None:
        """Force overlay back to topmost via tray action."""
        self.overlay.force_topmost()
        risk = self.overlay._probe_fullscreen_risk()
        if not risk:
            self.notification_manager.show("弹幕鱼排", "已尝试强制置顶弹幕")

    def switch_theme(self, theme: str) -> None:
        """Switch theme."""
        self.config.theme = theme
        cfg_module.save(self.config, self.config_path)
        screen = cfg_module.target_screen_geometry(self.app)
        self.overlay.update_config(self._absolute_display(screen), theme)
        self.overlay.engine.clear_all()
        self.notification_manager.set_theme(theme)
        self.tray.set_theme_checked(theme)
        if self.settings_dialog:
            self.settings_dialog.update_config(self.config)

    def switch_mode(self, mode: str) -> None:
        """Switch danmu display mode from tray."""
        if mode not in ("scrolling", "floating"):
            return
        self.config.display.danmu_mode = mode
        cfg_module.save(self.config, self.config_path)

        screen = cfg_module.target_screen_geometry(self.app)
        self.overlay.update_config(self._absolute_display(screen), self.config.theme)
        self.overlay.engine.clear_all()
        self.tray.set_mode_checked(mode)
        self.notification_manager.show("弹幕鱼排", f"已切换到{'滚动' if mode == 'scrolling' else '浮动'}模式")
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
        self.boss_key_mgr.unregister()
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

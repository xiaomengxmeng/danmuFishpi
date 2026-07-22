"""Chatroom WebSocket connection with auto-reconnect.

Uses websocket-client library for maximum compatibility with fishpi server.
Sends messages via HTTP POST.
"""

import json
import logging
import ssl
import threading
import time
from typing import Callable, Optional

import httpx
import websocket  # websocket-client library

from message import process_message

logger = logging.getLogger("danmuFishpi.chatroom")

BASE_URL = "https://fishpi.cn"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko)"
)
CLIENT_VERSION = "Python/v1.0.0"


class Connection:
    """Manages WebSocket connection to fishpi chatroom with auto-reconnect."""

    def __init__(self, api_key: str, on_message: Callable[[dict], None],
                 on_error: Optional[Callable[[str], None]] = None):
        self.api_key = api_key
        self.on_message = on_message
        self.on_error = on_error
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_delay = 3.0
        self._max_reconnect_delay = 60.0
        self._ws_url: Optional[str] = None

    def start(self) -> None:
        """Start the connection in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the connection."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        """Main connection loop with exponential backoff reconnect."""
        while self._running:
            try:
                self._connect_and_listen()
            except Exception as e:
                logger.warning(f"Connection error: {e}")
                if self.on_error:
                    self.on_error(str(e))

            if not self._running:
                break

            logger.info(f"Reconnecting in {self._reconnect_delay:.0f}s...")
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, self._max_reconnect_delay)

    def _connect_and_listen(self) -> None:
        """Connect to WebSocket and listen for messages."""
        # Get WS node URL
        ws_url = self._get_ws_url()
        if not ws_url:
            raise RuntimeError("Failed to get WebSocket URL")
        self._ws_url = ws_url

        logger.info(f"Connecting to chatroom: {ws_url}")

        # Create WebSocket app with callbacks.
        # on_ping is required because WebSocketApp returns ping frames to the
        # application layer and does not auto-reply with pongs.
        self._ws = websocket.WebSocketApp(
            ws_url,
            header={"User-Agent": USER_AGENT},
            on_open=self._on_ws_open,
            on_message=self._on_ws_message,
            on_error=self._on_ws_error,
            on_close=self._on_ws_close,
            on_ping=self._on_ws_ping,
        )

        # run_forever blocks until the connection closes.
        # ping_interval=None: client won't send pings, but it still auto-replies
        # to server pings with pongs at the WebSocket protocol level.
        # ping_timeout=None: disable pong-wait timeout.
        # sslopt uses ssl.CERT_NONE to accept the chatroom's self-signed/custom cert.
        self._ws.run_forever(
            ping_interval=None,
            ping_timeout=None,
            sslopt={"cert_reqs": ssl.CERT_NONE},
        )

    def _on_ws_open(self, ws) -> None:
        """Called when WebSocket connection is established."""
        self._reconnect_delay = 3.0  # Reset backoff
        logger.info("Chatroom connected")

    def _on_ws_message(self, ws, raw_data: str) -> None:
        """Called when a message is received."""
        try:
            msg = json.loads(raw_data)
            processed = process_message(msg)
            if processed:
                self.on_message(processed)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON: {raw_data[:100]}")
        except Exception as e:
            logger.error(f"Message processing error: {e}")

    def _on_ws_error(self, ws, error) -> None:
        """Called on WebSocket errors."""
        logger.warning(f"WebSocket error: {error}")
        if self.on_error:
            self.on_error(str(error))

    def _on_ws_close(self, ws, code, msg) -> None:
        """Called when WebSocket connection is closed."""
        logger.info(f"WebSocket closed: code={code}, msg={msg}")

    def _on_ws_ping(self, ws, data) -> None:
        """Reply to server ping with a pong.

        WebSocketApp forwards ping frames to the application layer and does not
        automatically respond. The fishpi server drops the connection if pong
        replies are missing.
        """
        try:
            payload = data if data is not None else b""
            ws.pong(payload)
        except Exception:
            pass

    def _get_ws_url(self) -> Optional[str]:
        """Get WebSocket URL from fishpi API (synchronous)."""
        try:
            resp = httpx.get(
                f"{BASE_URL}/chat-room/node/get",
                params={"apiKey": self.api_key},
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                logger.error(f"Failed to get WS node: {data.get('msg')}")
                return None

            return data.get("data")
        except Exception as e:
            logger.error(f"Error getting WS URL: {e}")
            return None

    def send_message(self, content: str) -> tuple[bool, str]:
        """Send a message to the chatroom via HTTP POST.

        Returns (success, error_message).
        """
        try:
            resp = httpx.post(
                f"{BASE_URL}/chat-room/send",
                json={
                    "content": content,
                    "client": CLIENT_VERSION,
                    "apiKey": self.api_key,
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
                params={"apiKey": self.api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                return False, data.get("msg", "发送失败")
            return True, ""
        except Exception as e:
            return False, str(e)

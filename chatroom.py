"""Chatroom WebSocket connection with auto-reconnect.

Connects to fishpi chatroom via WebSocket, receives messages,
and sends messages via HTTP POST.
"""

import asyncio
import json
import logging
import threading
from typing import Callable, Optional

import httpx
import websockets

from message import process_message

logger = logging.getLogger("danmuFishpi.chatroom")

BASE_URL = "https://fishpi.cn"
WS_BASE_URL = "wss://fishpi.cn"
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
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_delay = 3.0
        self._max_reconnect_delay = 60.0

    def start(self) -> None:
        """Start the connection in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the connection."""
        self._running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)
        if self._thread:
            self._thread.join(timeout=5)

    async def _close(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    def _run_loop(self) -> None:
        """Run the asyncio event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            logger.error(f"Chatroom loop error: {e}")
            if self.on_error:
                self.on_error(str(e))
        finally:
            self._loop.close()

    async def _connect_loop(self) -> None:
        """Main connection loop with exponential backoff reconnect."""
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.warning(f"WebSocket disconnected: {e}")
                if self.on_error:
                    self.on_error(str(e))

            if not self._running:
                break

            logger.info(f"Reconnecting in {self._reconnect_delay:.0f}s...")
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, self._max_reconnect_delay)

    async def _connect_and_listen(self) -> None:
        """Connect to WebSocket and listen for messages."""
        # Get WS node URL
        ws_url = await self._get_ws_url()
        if not ws_url:
            raise RuntimeError("Failed to get WebSocket URL")

        logger.info(f"Connecting to chatroom: {ws_url}")

        extra_headers = {"User-Agent": USER_AGENT}

        async with websockets.connect(
            ws_url,
            additional_headers=extra_headers,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._reconnect_delay = 3.0  # Reset backoff on successful connect
            logger.info("Chatroom connected")

            async for raw_data in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw_data)
                    # Process message in the event loop
                    processed = process_message(msg)
                    if processed:
                        # Schedule callback on main thread via ThreadPoolExecutor
                        self.on_message(processed)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON: {raw_data[:100]}")
                except Exception as e:
                    logger.error(f"Message processing error: {e}")

    async def _get_ws_url(self) -> Optional[str]:
        """Get WebSocket URL from fishpi API."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
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

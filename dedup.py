"""Message de-duplication by oId using a bounded ring buffer.

The fishpi chatroom server replays recent history after a WebSocket
reconnect. Without de-dup, replayed messages (same oId) are treated as
new and displayed again. This module keeps a bounded FIFO of recently
seen oIds so replayed duplicates can be dropped before they reach the UI.

State lifetime: a MessageDeduper is owned by a chatroom.Connection and
survives across reconnects (the connection loop reuses the same
instance), which is exactly what makes reconnect-replay de-dup work.
It is reset implicitly only when a new Connection is created (re-login).
"""

import logging
import threading
from collections import OrderedDict

logger = logging.getLogger("danmuFishpi.dedup")

# Default ring-buffer capacity. Covers the typical replay burst after a
# reconnect; memory is negligible (~tens of KB for 512 short strings).
DEFAULT_CAPACITY = 512


class MessageDeduper:
    """Bounded FIFO de-dup keyed by message oId.

    check_and_record(oId) returns True the first time an oId is seen
    (and records it), False on subsequent occurrences. When capacity is
    reached the oldest entry is evicted (pure FIFO). A very long
    disconnection that exceeds the buffer window may let an old oId be
    re-seen as new; raise capacity to mitigate.

    Thread-safety: an internal lock guards all mutations. Under the
    current single-threaded WebSocket callback model the lock is not
    strictly required, but is cheap insurance against future cross-thread
    access (e.g. stats/reset from the UI thread).
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._lock = threading.Lock()

    def check_and_record(self, oId) -> bool:
        """Return True if oId is new (and record it), False if duplicate.

        None / empty oId is treated as "no de-dup possible": returns True
        without recording, so messages lacking an oId are never dropped.
        oId is normalized via str() to tolerate int/str variants.
        """
        if oId is None:
            return True
        key = str(oId)
        if key == "":
            return True
        with self._lock:
            if key in self._seen:
                return False
            self._seen[key] = None
            if len(self._seen) > self._capacity:
                self._seen.popitem(last=False)  # evict oldest (FIFO)
            return True

    def reset(self) -> None:
        """Clear all remembered oIds (e.g. on explicit re-login)."""
        with self._lock:
            self._seen.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)

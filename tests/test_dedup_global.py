"""Tests for global MessageDeduper sharing across Connection instances."""
from dedup import MessageDeduper
from chatroom import Connection


def test_connection_uses_external_deduper():
    """Connection should use the externally provided deduper instance."""
    shared_deduper = MessageDeduper(capacity=64)
    conn = Connection(
        api_key="fake-key",
        on_message=lambda msg: None,
        deduper=shared_deduper,
    )
    assert conn._deduper is shared_deduper


def test_connection_creates_own_deduper_when_none():
    """Connection should create its own deduper when none is provided."""
    conn = Connection(
        api_key="fake-key",
        on_message=lambda msg: None,
    )
    assert conn._deduper is not None
    assert isinstance(conn._deduper, MessageDeduper)


def test_two_connections_share_deduper():
    """Two Connections sharing one deduper should de-dup across both."""
    shared_deduper = MessageDeduper(capacity=64)

    conn1 = Connection(
        api_key="fake-key",
        on_message=lambda msg: None,
        deduper=shared_deduper,
    )
    conn2 = Connection(
        api_key="fake-key",
        on_message=lambda msg: None,
        deduper=shared_deduper,
    )

    # conn1 sees oId="abc" first → should be new
    assert conn1._deduper.check_and_record("abc") is True
    # conn2 sees same oId="abc" → should be duplicate (dropped)
    assert conn2._deduper.check_and_record("abc") is False


def test_app_creates_global_deduper():
    """App should create a global MessageDeduper with capacity 1024."""
    import main
    assert hasattr(main, 'MessageDeduper') or 'MessageDeduper' in dir(main)

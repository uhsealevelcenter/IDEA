"""Checkpointer lifecycle for local tests and production PostgreSQL."""

from __future__ import annotations

import atexit
from contextlib import AbstractContextManager
from threading import Lock
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from idea_config import LANGGRAPH_AES_KEY, LANGGRAPH_DATABASE_URL

_lock = Lock()
_checkpointer: Any = None
_context: AbstractContextManager | None = None


def _serializer() -> Any:
    """Build the optional encrypted serializer from the configured raw AES key."""
    if not LANGGRAPH_AES_KEY:
        return None
    key = LANGGRAPH_AES_KEY.encode("utf-8")
    if len(key) not in {16, 24, 32}:
        raise ValueError("LANGGRAPH_AES_KEY must be exactly 16, 24, or 32 UTF-8 bytes")
    from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

    return EncryptedSerializer.from_pycryptodome_aes(key=key)


def get_checkpointer() -> Any:
    global _checkpointer, _context
    with _lock:
        if _checkpointer is not None:
            return _checkpointer
        serde = _serializer()
        if not LANGGRAPH_DATABASE_URL:
            _checkpointer = InMemorySaver(serde=serde)
            return _checkpointer
        from langgraph.checkpoint.postgres import PostgresSaver

        _context = PostgresSaver.from_conn_string(LANGGRAPH_DATABASE_URL)
        _checkpointer = _context.__enter__()
        if serde is not None:
            _checkpointer.serde = serde
        return _checkpointer


def setup_checkpointer() -> None:
    saver = get_checkpointer()
    setup = getattr(saver, "setup", None)
    if setup:
        setup()


def close_checkpointer() -> None:
    global _checkpointer, _context
    with _lock:
        if _context is not None:
            _context.__exit__(None, None, None)
        _context = None
        _checkpointer = None


atexit.register(close_checkpointer)

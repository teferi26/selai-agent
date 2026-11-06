"""Session handling for work that outlives the request (item 173).

The channel webhooks authenticate and durably enqueue the message, commit
that receipt, and only then answer the provider. A BackgroundTask is the
low-latency processing path and a periodic worker is the recovery path. Both
must open their *own* session: the request transaction has already completed
and its connection may be back in the pool by the time processing starts.

A named function, not an inline `async with session_scope()`, so the tests
of each channel can swap the whole thing for a fake session.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """A fresh session, committed on exit and rolled back on failure."""
    async with session_scope() as db:
        yield db

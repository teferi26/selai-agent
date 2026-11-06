"""Run one database-backed release phase before a new web image goes live.

Railway executes this module as its pre-deploy command.  Keeping migrations
and seeds here (instead of in the web process CMD) means a restart cannot
mutate the schema, call Stripe, or race another replica while serving traffic.

Every release runner in this repository honours the same PostgreSQL advisory
lock.  Railway normally starts one pre-deploy container, but the lock also
protects manual retries and accidentally duplicated services.
"""
from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg

from app.core.settings import settings, verify_production_secrets

BACKEND_ROOT = Path(__file__).resolve().parents[2]
RELEASE_LOCK_ID = 7_238_411_905_202_609
RELEASE_LOCK_TIMEOUT_SECONDS = 60.0
RELEASE_STEPS: tuple[tuple[str, ...], ...] = (
    (sys.executable, "-m", "alembic", "upgrade", "head"),
    (sys.executable, "-m", "app.scripts.seed_plans"),
    (sys.executable, "-m", "app.scripts.sync_stripe_plans"),
    (sys.executable, "-m", "app.scripts.seed_demo_public"),
)


def _psycopg_dsn() -> str:
    """Convert the SQLAlchemy sync URL into a DSN psycopg accepts."""
    return str(settings.database_url_sync).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


@contextmanager
def release_lock(
    *,
    connect: Callable[..., Any] = psycopg.connect,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    timeout_seconds: float = RELEASE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Hold the process-wide release lock, or fail before mutating anything."""
    deadline = monotonic() + timeout_seconds
    with connect(_psycopg_dsn(), autocommit=True, connect_timeout=10) as connection:
        acquired = False
        while not acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", (RELEASE_LOCK_ID,))
                row = cursor.fetchone()
            acquired = bool(row and row[0])
            if acquired:
                break
            if monotonic() >= deadline:
                raise TimeoutError(
                    "another release job still holds the PostgreSQL advisory lock"
                )
            sleep(1.0)
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (RELEASE_LOCK_ID,))


def run_steps(
    steps: Sequence[Sequence[str]] = RELEASE_STEPS,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Run release steps sequentially and stop on the first non-zero exit."""
    for step in steps:
        runner(list(step), cwd=BACKEND_ROOT, check=True)


def main() -> None:
    # Fail before a migration or Stripe call if the target cannot safely boot.
    verify_production_secrets(settings)
    with release_lock():
        run_steps()


if __name__ == "__main__":
    main()

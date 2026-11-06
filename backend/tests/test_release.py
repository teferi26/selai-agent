"""Production release and Railway promotion contracts."""
from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.scripts import release


class _Cursor:
    def __init__(self, rows: list[bool], statements: list[str]):
        self.rows = rows
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, statement, _params):
        self.statements.append(statement)

    def fetchone(self):
        return (self.rows.pop(0),)


class _Connection:
    def __init__(self, rows: list[bool]):
        self.rows = rows
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return _Cursor(self.rows, self.statements)


def test_release_steps_are_ordered_and_fail_fast():
    calls: list[list[str]] = []
    steps = (("first",), ("second",), ("never",))

    def _run(command, **kwargs):
        assert kwargs == {"cwd": release.BACKEND_ROOT, "check": True}
        calls.append(command)
        if command == ["second"]:
            raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        release.run_steps(steps, runner=_run)

    assert calls == [["first"], ["second"]]


def test_release_validates_configuration_before_locking_or_mutating(monkeypatch):
    events: list[str] = []

    monkeypatch.setattr(
        release,
        "verify_production_secrets",
        lambda _settings: events.append("validate"),
    )

    @contextmanager
    def _lock():
        events.append("lock")
        yield

    monkeypatch.setattr(release, "release_lock", _lock)
    monkeypatch.setattr(release, "run_steps", lambda: events.append("steps"))

    release.main()

    assert events == ["validate", "lock", "steps"]


def test_release_lock_serialises_retries_and_always_unlocks(monkeypatch):
    connection = _Connection([False, True])
    sleeps: list[float] = []
    monkeypatch.setattr(release.settings, "database_url", "postgresql+asyncpg://u:p@db/x")

    def _connect(dsn, **kwargs):
        assert dsn == "postgresql://u:p@db/x"
        assert kwargs == {"autocommit": True, "connect_timeout": 10}
        return connection

    with release.release_lock(
        connect=_connect,
        monotonic=lambda: 0.0,
        sleep=sleeps.append,
        timeout_seconds=5,
    ):
        assert connection.statements.count("SELECT pg_try_advisory_lock(%s)") == 2

    assert sleeps == [1.0]
    assert connection.statements[-1] == "SELECT pg_advisory_unlock(%s)"


def test_release_lock_times_out_before_any_release_step(monkeypatch):
    connection = _Connection([False])
    times = iter([0.0, 2.0])
    monkeypatch.setattr(release.settings, "database_url", "postgresql+asyncpg://u:p@db/x")

    with pytest.raises(TimeoutError, match="another release job"), release.release_lock(
        connect=lambda *_args, **_kwargs: connection,
        monotonic=lambda: next(times),
        sleep=lambda _seconds: None,
        timeout_seconds=1,
    ):
        pytest.fail("a timed-out release must not enter the mutation phase")

    assert "SELECT pg_advisory_unlock(%s)" not in connection.statements


def test_railway_promotes_only_a_ready_release():
    backend_root = Path(__file__).resolve().parents[1]
    config = json.loads((backend_root / "railway.json").read_text(encoding="utf-8"))

    assert config["deploy"]["preDeployCommand"] == ["python -m app.scripts.release"]
    assert config["deploy"]["healthcheckPath"] == "/readyz"

    dockerfile = (backend_root / "Dockerfile").read_text(encoding="utf-8")
    assert "alembic upgrade head" not in dockerfile
    assert "app.scripts.sync_stripe_plans" not in dockerfile

"""Lifecycle tasks — registration + beat schedule wiring."""
from __future__ import annotations

from celery.schedules import crontab

from app.workers import tasks
from app.workers.celery_app import celery_app


def test_lifecycle_tasks_are_registered():
    assert tasks.trial_ending_sweep.name == "lifecycle.trial_ending_sweep"
    assert tasks.weekly_digest.name == "lifecycle.weekly_digest"
    assert "lifecycle.trial_ending_sweep" in celery_app.tasks
    assert "lifecycle.weekly_digest" in celery_app.tasks


def test_trial_sweep_is_in_beat_schedule_daily():
    entry = celery_app.conf.beat_schedule["lifecycle-trial-ending-sweep"]
    assert entry["task"] == "lifecycle.trial_ending_sweep"
    assert entry["schedule"] == crontab(minute=0, hour=8)


def test_weekly_digest_runs_monday_morning():
    entry = celery_app.conf.beat_schedule["lifecycle-weekly-digest"]
    assert entry["task"] == "lifecycle.weekly_digest"
    # 06:00 UTC on Monday ≈ 08:00 Europe/Madrid (CEST).
    assert entry["schedule"] == crontab(minute=0, hour=6, day_of_week=1)


def test_new_lifecycle_tasks_are_registered():
    assert tasks.voice_usage_alert.name == "lifecycle.voice_usage_alert"
    assert tasks.setup_rescue.name == "lifecycle.setup_rescue"
    assert tasks.first_week_report.name == "lifecycle.first_week_report"
    for name in (
        "lifecycle.voice_usage_alert",
        "lifecycle.setup_rescue",
        "lifecycle.first_week_report",
    ):
        assert name in celery_app.tasks


def test_new_lifecycle_tasks_run_daily():
    entry = celery_app.conf.beat_schedule["lifecycle-voice-usage-alert"]
    assert entry["task"] == "lifecycle.voice_usage_alert"
    assert entry["schedule"] == crontab(minute=0, hour=9)

    entry = celery_app.conf.beat_schedule["lifecycle-setup-rescue"]
    assert entry["task"] == "lifecycle.setup_rescue"
    assert entry["schedule"] == crontab(minute=30, hour=8)

    entry = celery_app.conf.beat_schedule["lifecycle-first-week-report"]
    assert entry["task"] == "lifecycle.first_week_report"
    assert entry["schedule"] == crontab(minute=0, hour=7)

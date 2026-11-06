"""Celery app for background jobs.

Workloads that belong here:
- Pre-visit briefing generation (LLM) — scheduled before a visit
- Property embedding refresh when a listing is created/updated
- Lead score recomputation
- Delayed WhatsApp follow-ups (24h after no reply, etc.)
"""
from celery import Celery
from celery.schedules import crontab

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

celery_app = Celery(
    "realtor",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Recycle each prefork child often: Python hands very little heap back to
    # the OS, so a long-lived child keeps the high-water mark of the heaviest
    # task it ever ran (an LLM turn, a PDF render) for the rest of its life.
    # On a memory-capped Railway container that ratchet is what shows up as
    # "Deploy Ran Out of Memory". 50 keeps the ratchet short; the CLI flag in
    # railway.worker.json says the same number and wins if they ever disagree.
    worker_max_tasks_per_child=50,
    # --- Hardening (item 223) -------------------------------------------
    # A hung LLM call used to hold a worker slot forever; with concurrency=2
    # two of them stop the whole queue. Soft limit first (SoftTimeLimitExceeded
    # is catchable and lets the task clean up), hard limit a minute later.
    task_soft_time_limit=240,
    task_time_limit=300,
    # Ack after the task finishes, so a worker killed mid-task (deploy, OOM)
    # returns the message to the broker instead of dropping it. Every task
    # must therefore tolerate being run twice — see the `extra` flags in
    # tasks.py that make reminders and proactive calls idempotent.
    task_acks_late=True,
    # With acks_late, prefetching queues work behind a slow task on one
    # worker while another sits idle. One at a time.
    worker_prefetch_multiplier=1,
)

def init_sentry() -> bool:
    """Wire Sentry into the worker/beat process (item 224).

    Until now every background failure — reminders, briefings, webhook
    deliveries, lifecycle sweeps — died in a Railway log with nobody
    watching, while the web process reported everything. Same conditional
    pattern as main.py, plus `CeleryIntegration` so each task shows up as its
    own transaction with its args scrubbed.

    Returns whether Sentry was actually initialized (no DSN, no Sentry).
    """
    if not settings.sentry_dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
        send_default_pii=False,  # never ship lead PII to Sentry
        integrations=[CeleryIntegration()],
    )
    log.info("sentry.enabled.worker", env=settings.app_env)
    return True


init_sentry()

# Beat schedule: cheap polling that triggers per-record tasks.
# Granularity > 1 min is fine because the windows in tasks tolerate ±15m.
celery_app.conf.beat_schedule = {
    "drain-inbound-outbox": {
        # Web requests normally process receipts immediately, but PostgreSQL
        # keeps them recoverable across restarts and Redis outages.
        "task": "channels.drain_inbound_outbox",
        "schedule": 5.0,
    },
    "drain-webhook-outbox": {
        # PostgreSQL is the durable queue. If Redis/Beat is unavailable the
        # rows simply wait here and this sweep catches up after recovery.
        "task": "webhooks.drain_outbox",
        "schedule": 10.0,
    },
    "embed-missing-properties": {
        "task": "properties.embed_missing",
        "schedule": crontab(minute="*/10"),
    },
    "send-visit-reminders": {
        "task": "visits.send_reminders",
        "schedule": crontab(minute="*/15"),
    },
    "schedule-pre-visit-briefings": {
        "task": "visits.schedule_briefings",
        "schedule": crontab(minute="*/15"),
    },
    "sweep-price-drops": {
        "task": "properties.sweep_price_drops",
        "schedule": crontab(minute="*/30"),
    },
    "sweep-won-leads-for-review": {
        "task": "leads.sweep_won_for_review",
        "schedule": crontab(minute=0),  # top of every hour
    },
    "detect-no-shows": {
        "task": "visits.detect_no_shows",
        "schedule": crontab(minute="*/15"),
    },
    "proactive-outbound-voice": {
        # Once per day at 17:00 UTC (~ 19:00 Madrid CEST). Civilized hour.
        "task": "leads.proactive_outbound_voice",
        "schedule": crontab(minute=0, hour=17),
    },
    "refresh-agency-few-shot": {
        # Daily at 04:30 UTC — quiet hours, before EU office opens.
        "task": "agencies.refresh_few_shot",
        "schedule": crontab(minute=30, hour=4),
    },
    "lifecycle-trial-ending-sweep": {
        # Daily at 08:00 UTC (~10:00 Madrid CEST) — inbox-friendly hour.
        "task": "lifecycle.trial_ending_sweep",
        "schedule": crontab(minute=0, hour=8),
    },
    "lifecycle-weekly-digest": {
        # Mondays 06:00 UTC ≈ 08:00 Europe/Madrid (CEST).
        "task": "lifecycle.weekly_digest",
        "schedule": crontab(minute=0, hour=6, day_of_week=1),
    },
    "lifecycle-voice-usage-alert": {
        # Daily at 09:00 UTC (~11:00 Madrid CEST) — after the morning rush.
        "task": "lifecycle.voice_usage_alert",
        "schedule": crontab(minute=0, hour=9),
    },
    "lifecycle-setup-rescue": {
        # Daily at 08:30 UTC (~10:30 Madrid CEST) — inbox-friendly hour.
        "task": "lifecycle.setup_rescue",
        "schedule": crontab(minute=30, hour=8),
    },
    "lifecycle-first-week-report": {
        # Daily at 07:00 UTC (~09:00 Madrid CEST) — start-of-day recap.
        "task": "lifecycle.first_week_report",
        "schedule": crontab(minute=0, hour=7),
    },
    "billing-reset-usage-counters": {
        # Monthly, day 1 at 02:00 UTC — cycle rollover for voice minutes.
        "task": "billing.reset_usage_counters",
        "schedule": crontab(minute=0, hour=2, day_of_month=1),
    },
    "lifecycle-trial-expiry-sweep": {
        # Daily at 05:00 UTC — pause trials that ran out without a card.
        # Runs before the 08:00 "te quedan N días" reminder so a just-paused
        # agency never gets both emails on the same morning.
        "task": "lifecycle.trial_expiry_sweep",
        "schedule": crontab(minute=0, hour=5),
    },
    "billing-dunning-sweep": {
        # Daily at 09:30 UTC (~11:30 Madrid CEST) — day 3 / day 7 reminders
        # for subscriptions still PAST_DUE.
        "task": "billing.dunning_sweep",
        "schedule": crontab(minute=30, hour=9),
    },
    "lifecycle-followup-sweep": {
        # Every 30 min: the 24h/72h follow-ups to leads that went quiet.
        # The window logic lives in app/services/followups.py; this entry only
        # decides how often it is asked.
        "task": "lifecycle.followup_sweep",
        "schedule": crontab(minute="*/30"),
    },
    "lifecycle-winback-sweep": {
        # Daily at 10:00 UTC (~12:00 Madrid CEST) — 21-25 days after a
        # cancellation, "esto ha pasado desde que te fuiste".
        "task": "lifecycle.winback_sweep",
        "schedule": crontab(minute=0, hour=10),
    },
    "lifecycle-terminated-data-purge-sweep": {
        # Daily at 03:15 UTC. Eligibility starts on day 29 so normal daily
        # scheduling completes the operational-data purge before day 30.
        "task": "lifecycle.terminated_data_purge_sweep",
        "schedule": crontab(minute=15, hour=3),
    },
    "lifecycle-resource-usage-alert": {
        # Daily at 09:15 UTC — properties/agents at 90% of the plan limit.
        # Quarter past so it never lands in the same minute as the voice
        # alert (09:00) and one agency can't get two emails at once.
        "task": "lifecycle.resource_usage_alert",
        "schedule": crontab(minute=15, hour=9),
    },
}


# Register DB event listeners in the worker process too.
from app.db import events as _db_events  # noqa: E402, F401

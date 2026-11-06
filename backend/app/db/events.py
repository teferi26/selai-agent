"""Domain event listeners.

Track price changes on Property and queue a price-drop alert task when a
property drops in price (configurable threshold).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Lead, LeadStatus, Property

log = get_logger(__name__)

# A price drop must be at least this fraction below the previous price to
# trigger an alert. Avoids notifying for cosmetic changes (e.g., 320,000 → 319,500).
MIN_DROP_PCT = 1.5


@event.listens_for(Property, "before_update", propagate=True)
def _record_price_change(_mapper, _connection, target: Property) -> None:
    state = target.__dict__
    history = list(state.get("price_history") or [])

    # SQLAlchemy attribute history — find the previous value of `price`.
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(target)
    price_hist = insp.attrs.price.history
    if not price_hist.has_changes() or not price_hist.deleted:
        return

    old_price = price_hist.deleted[0]
    new_price = target.price
    if old_price is None or new_price is None or old_price == new_price:
        return

    history.append(
        {"at": datetime.now(UTC).isoformat(), "from": int(old_price), "to": int(new_price)}
    )
    target.price_history = history

    if new_price < old_price:
        drop_pct = (old_price - new_price) / float(old_price) * 100.0
        if drop_pct >= MIN_DROP_PCT:
            # Defer to async dispatcher — can't run a Celery .delay() here cleanly
            # because we're inside a sync flush. We tag the property to be picked
            # up by a periodic sweep instead.
            extra = dict(target.extra or {})
            extra["pending_price_drop_alert"] = True
            target.extra = extra


@event.listens_for(Lead, "before_update", propagate=True)
def _detect_lead_won(_mapper, _connection, target: Lead) -> None:
    """When a lead transitions into WON, mark it for a delayed review request
    sweep (picked up by `leads.request_google_review` task)."""
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(target)
    status_hist = insp.attrs.status.history
    if not status_hist.has_changes():
        return
    new_status = target.status
    old_status = status_hist.deleted[0] if status_hist.deleted else None
    if new_status != LeadStatus.WON or old_status == LeadStatus.WON:
        return

    extra = dict(target.extra or {})
    # Mark with target time; the sweep task reads this to fire at the right time.
    extra["won_at"] = datetime.now(UTC).isoformat()
    target.extra = extra


def attach_listeners(_session: Session | None = None) -> None:
    """No-op kept as the canonical install point — importing this module
    is what registers the listener via the @event decorator above."""
    log.debug("db.events.attached")

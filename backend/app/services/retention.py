"""Post-termination data retention and tenant-content deletion.

Account, billing and B2B acceptance records are retained in a blocked state
for the legal limitation periods described in the privacy policy. Operational
content processed for the agency (leads, conversations, messages, listings,
offers, files and integration secrets) is purged automatically.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentRun,
    ConsentLog,
    Conversation,
    DeviceToken,
    InboundOutbox,
    Invitation,
    Lead,
    LeadOffer,
    Message,
    MessageTemplate,
    Offer,
    Office,
    Property,
    Subscription,
    User,
    Visit,
    WebhookOutbox,
    WebhookSubscription,
)
from app.db.models.agency import Agency
from app.services.gdpr import queue_agency_external_erasures
from app.services.gdpr_erasure import PURGED_AT_KEY
from app.services.storage import delete_prefix


async def purge_terminated_agency_data(
    db: AsyncSession,
    *,
    agency: Agency,
    subscription: Subscription,
    purged_at: datetime,
) -> bool:
    """Atomically purge operational tenant data and block every local user.

    External-provider locators are staged and flushed first. Storage deletion
    is then idempotent; if the database transaction subsequently fails,
    replaying the sweep safely rebuilds the still-visible outbox inventory and
    repeats an empty prefix deletion before retrying the database work.
    """
    if (agency.settings or {}).get(PURGED_AT_KEY):
        return False

    agency_id = agency.id

    # Provider locators disappear with the rows below. Persist every
    # discoverable external copy as pending compensation work first; a flush
    # failure aborts before local storage or tenant rows are touched.
    await queue_agency_external_erasures(db, agency=agency)

    delete_prefix(f"agencies/{agency_id}")

    # Direct children first. Database cascades are still present as a second
    # line of defence, but explicit tenant predicates make the deletion scope
    # visible and testable without relying on ORM relationship loading.
    for statement in (
        # Keep the provider idempotency tombstone, but remove everything that
        # could identify a lead and make all pending receipts unclaimable. A
        # late provider retry conflicts with this tombstone instead of
        # recreating the lead after the tenant purge.
        update(InboundOutbox)
        .where(InboundOutbox.agency_id == agency_id)
        .values(
            payload={},
            context={},
            processed_at=purged_at,
            available_at=purged_at,
            last_error=None,
        ),
        delete(Message).where(Message.agency_id == agency_id),
        delete(AgentRun).where(AgentRun.agency_id == agency_id),
        delete(Offer).where(Offer.agency_id == agency_id),
        delete(LeadOffer).where(LeadOffer.seller_agency_id == agency_id),
        update(LeadOffer)
        .where(LeadOffer.buyer_agency_id == agency_id)
        .values(buyer_agency_id=None, transferred_lead_id=None),
        delete(Visit).where(Visit.agency_id == agency_id),
        delete(Conversation).where(Conversation.agency_id == agency_id),
        # Lead consent is part of the operational record. Owner acceptance
        # evidence has user_id and is retained for contract/legal defence.
        delete(ConsentLog).where(
            ConsentLog.agency_id == agency_id,
            ConsentLog.lead_id.is_not(None),
        ),
        delete(Lead).where(Lead.agency_id == agency_id),
        delete(Property).where(Property.agency_id == agency_id),
        delete(WebhookOutbox).where(WebhookOutbox.agency_id == agency_id),
        delete(WebhookSubscription).where(WebhookSubscription.agency_id == agency_id),
        delete(DeviceToken).where(DeviceToken.agency_id == agency_id),
        delete(Invitation).where(Invitation.agency_id == agency_id),
        delete(MessageTemplate).where(MessageTemplate.agency_id == agency_id),
        delete(Office).where(Office.agency_id == agency_id),
        update(User)
        .where(User.agency_id == agency_id)
        .values(
            is_active=False,
            phone=None,
            google_calendar_id=None,
            google_refresh_token=None,
            home_lat=None,
            home_lng=None,
            office_id=None,
        ),
    ):
        await db.execute(statement)

    # All channel credentials and prompt/configuration content live in this
    # JSON. Keep only the non-PII deletion marker so the sweep is idempotent.
    agency.settings = {PURGED_AT_KEY: purged_at.isoformat()}
    agency.contact_phone = None
    subscription.voice_minutes_used = 0
    subscription.leads_processed = 0
    return True


def is_retention_purge_candidate(
    subscription: Subscription,
    *,
    cutoff: datetime,
) -> bool:
    """Pure predicate used by tests and defensive callers."""
    return bool(
        subscription.canceled_at is not None
        and subscription.canceled_at <= cutoff
        and str(getattr(subscription.status, "value", subscription.status)) == "canceled"
    )

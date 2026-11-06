"""Inbound email webhook.

Each agency has a unique inbox address (eg. piloto-leads@inbound.realtor.ai).
The agency is resolved by the `To` address (we map the local-part to slug).

Three rules that took a lost lead each to learn:

- (173) A provider retries a delivery it thinks failed. The same
  `Message-ID` must never be answered twice, so the authenticated input is
  inserted idempotently in PostgreSQL and committed before the 2xx. A local
  BackgroundTask is the fast path; a periodic worker resumes the same row
  after a restart or Redis outage.
- (193) A portal's `From` is a mailbox that only sends. We never reply
  there, not even when the parse failed and it is the only address we have.
- (193) When a portal email cannot be answered — unreadable, or readable but
  with a phone and no email — the agency is told. Silence there is a lead
  the owner never knew existed.
"""
from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.channels import leads_email_for
from app.channels.background import task_session
from app.channels.dedup import inbound_already_processed
from app.channels.email.alerts import alert_unreadable_portal_email
from app.channels.email.client import ResendClient
from app.channels.email.formatting import email_parts
from app.channels.email.parser import parse_inbound_email
from app.channels.email.receiving import (
    fetch_inbound_content,
    merge_inbound_content,
    resend_inbound_email_id,
)
from app.channels.email.svix import verify as verify_svix
from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import Agency
from app.db.session import get_db
from app.services.conversation_service import InboundPayload, handle_inbound
from app.services.inbound_outbox import (
    complete_receipt,
    defer_receipt,
    deserialize_inbound,
    enqueue_inbound,
    lease_pending_reply,
    lock_receipt,
    pending_reply,
    stage_reply,
)
from app.services.plan_limits import is_subscription_blocked
from app.services.portal_lead_parser import (
    can_reply_to,
    detect_portal,
    merge_portal_lead_into_payload,
    parse_portal_email,
    portal_lead_is_usable,
)

router = APIRouter(prefix="/webhooks/email", tags=["email"])
log = get_logger(__name__)


@router.post("")
async def inbound(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(default=None),
    svix_id: str | None = Header(default=None, alias="svix-id"),
    svix_timestamp: str | None = Header(default=None, alias="svix-timestamp"),
    svix_signature: str | None = Header(default=None, alias="svix-signature"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    raw = await request.body()

    # Two senders, two proofs of identity. Resend Inbound signs through Svix
    # (an HMAC over id.timestamp.body); our own tooling and the tests use the
    # shared header. Either is enough, neither is optional: with both secrets
    # unset the endpoint stays closed, because it is the only thing standing
    # between a stranger and injecting leads into any agency.
    shared = settings.secret_key.get_secret_value()
    signing = settings.resend_webhook_secret.get_secret_value()

    by_shared = bool(
        shared and x_webhook_secret and hmac.compare_digest(x_webhook_secret, shared)
    )
    by_svix = bool(
        signing
        and verify_svix(
            secret=signing,
            svix_id=svix_id,
            timestamp=svix_timestamp,
            signature_header=svix_signature,
            body=raw,
        )
    )
    if not (by_shared or by_svix):
        log.warning(
            "email.webhook.rejected",
            had_shared_header=bool(x_webhook_secret),
            had_svix_headers=bool(svix_id and svix_signature),
            svix_configured=bool(signing),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad secret")

    body = await request.json()

    payload = parse_inbound_email(body)
    if payload is None:
        return {"status": "ignored"}

    # Which agency this belongs to is decided FIRST, from the envelope alone.
    #
    # The inbound address accepts any local part — on Resend's managed domain
    # every `<anything>@<id>.resend.app` is delivered — so most of what arrives
    # at a public lead address is not a lead. Resolving the agency before doing
    # any work means a stray message costs one cheap query and nothing else:
    # no paid API read to fetch a body we would discard, and no 503 that would
    # have Resend retrying spam on a loop. That matters on a plan where
    # received mail eats the same daily allowance as the mail we send.
    to_addr = (payload.raw or {}).get("to") or ""
    agency = await _agency_for_email(db, to_addr)
    if agency is None:
        log.warning("email.no_agency_match", to=to_addr)
        return {"status": "no_agency_match"}
    agency_id, agency_slug = agency.id, agency.slug

    # Only now the body. Resend's `email.received` webhook carries metadata
    # only — no body, no headers (their docs: serverless request-body limits) —
    # and everything that makes a lead a lead lives in the body.
    #
    # A portal notification is the worst case: the subject is boilerplate
    # ("Nuevo contacto para tu inmueble") and the name, phone and message are
    # all in the body. Parsing the webhook alone yields an empty conversation
    # with a noreply robot.
    email_id = resend_inbound_email_id(body)
    if email_id is not None:
        content = await fetch_inbound_content(email_id)
        if content is None:
            # Answer 5xx so Resend retries: an email we could not read is not
            # an email to discard. Swallowing it with a 200 would lose the lead
            # for good, and silently — the agency would never know it existed.
            log.error("email.receiving.unavailable", email_id=email_id)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "inbound content unavailable"
            )
        merge_inbound_content(body, content)
        # Re-parse: the payload above was built from the envelope, so its text
        # is empty and it carries no Message-ID (which the dedup check needs).
        enriched = parse_inbound_email(body)
        if enriched is not None:
            payload = enriched

    # If this is a portal notification (Idealista/Fotocasa/pisos.com…), the
    # `From` is a noreply address and the *real* lead's contact info is in
    # the body. Rewrite the payload identity/text accordingly so the rest
    # of the pipeline treats it as a normal lead.
    # With the Resend Inbound envelope the from address lives in `data`,
    # so fall back to the identity the parser already extracted.
    raw_from = body.get("From") or body.get("from") or payload.identity.email or ""
    portal = detect_portal(raw_from)
    portal_lead = parse_portal_email(payload.text, from_addr_raw=raw_from)
    portal_unreadable = False
    if portal_lead is not None and portal_lead_is_usable(portal_lead):
        merge_portal_lead_into_payload(portal_lead, payload)
        log.info("email.portal_lead.detected", portal=portal)
    elif portal is not None:
        # (193) We know who sent it and we could not read who wrote it. The
        # lead is real; the agency has to be told by a human-readable route.
        portal_unreadable = True
        log.warning("email.portal_lead.unreadable", portal=portal, sender=raw_from)

    payload.agency_id = agency_id

    # Paused/canceled/unpaid accounts are not served — drop with a 200 so the
    # provider does not retry.
    if await is_subscription_blocked(db, agency_id):
        log.info("email.skipped_blocked_subscription", agency=str(agency_id))
        return {"status": "subscription_blocked"}

    # (173) Same Message-ID as something we already stored: the provider is
    # retrying. Answer 200 and do nothing — no agent run, no second reply.
    if await inbound_already_processed(db, agency_id, payload.external_message_id):
        log.info(
            "email.inbound.duplicate",
            agency=str(agency_id),
            message_id=payload.external_message_id,
        )
        return {"status": "duplicate"}

    receipt_id = await enqueue_inbound(
        db,
        channel="email",
        payload=payload,
        context={
            "portal": portal,
            "portal_unreadable": portal_unreadable,
            "provider_email_id": email_id,
        },
        provider_receipt_id=svix_id or email_id,
    )
    # This is the durability boundary. If PostgreSQL is unavailable the
    # provider receives 5xx and retries; no accepted email lives only in RAM.
    await db.commit()
    background_tasks.add_task(
        process_inbound_email_receipt,
        receipt_id=receipt_id,
        fallback_agency_id=agency_id,
        fallback_agency_slug=agency_slug,
        fallback_payload=payload,
        fallback_portal=portal,
        fallback_portal_unreadable=portal_unreadable,
    )
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def process_inbound_email(
    *,
    agency_id: uuid.UUID,
    payload: InboundPayload,
    portal: str | None,
    portal_unreadable: bool,
    agency_slug: str = "",
) -> None:
    """Run the agent and answer, after the response already went out.

    Nothing here may raise: a BackgroundTask that blows up has nobody left
    to tell.
    """
    try:
        reply = await _process_inbound_email(
            agency_id=agency_id,
            agency_slug=agency_slug,
            payload=payload,
            portal=portal,
            portal_unreadable=portal_unreadable,
        )
        if reply is not None:
            await _deliver_pending_reply(reply)
    except Exception:
        log.exception("email.background.failed", agency=str(agency_id))


async def _settle_inbound_email_receipt(
    receipt_id: uuid.UUID,
    *,
    error: BaseException | None = None,
) -> None:
    """Complete or defer a staged delivery in a fresh transaction."""
    async with task_session() as db:
        receipt = await lock_receipt(
            db,
            receipt_id,
            channel="email",
            due_only=False,
        )
        if receipt is None:
            return
        if error is None:
            complete_receipt(receipt)
            return
        defer_receipt(receipt, error)
        log.error(
            "email.inbound_outbox.deferred",
            receipt=str(receipt.id),
            attempt=receipt.attempt_count,
            error=type(error).__name__,
        )


async def _deliver_pending_reply(reply: dict) -> None:
    to = str(reply.get("to") or "").strip()
    subject = str(reply.get("subject") or "").strip()
    text = str(reply.get("text") or "")
    html = str(reply.get("html") or "")
    if not to or not subject or not text:
        raise ValueError("staged email reply is incomplete")
    references = reply.get("references")
    if not isinstance(references, list):
        references = None
    await ResendClient().send(
        to=to,
        subject=subject,
        text=text,
        html=html,
        in_reply_to=reply.get("in_reply_to"),
        references=references,
        reply_to=reply.get("reply_to"),
    )


async def process_inbound_email_receipt(
    *,
    receipt_id: uuid.UUID,
    fallback_agency_id: uuid.UUID | None = None,
    fallback_agency_slug: str = "",
    fallback_payload: InboundPayload | None = None,
    fallback_portal: str | None = None,
    fallback_portal_unreadable: bool = False,
) -> None:
    """Process one receipt and stage its Resend delivery before sending."""
    reply: dict | None = None
    try:
        async with task_session() as db:
            receipt = await lock_receipt(db, receipt_id, channel="email")
            if receipt is None:
                if not isinstance(db, AsyncSession) and fallback_payload is not None:
                    await process_inbound_email(
                        agency_id=fallback_agency_id or fallback_payload.agency_id,
                        agency_slug=fallback_agency_slug,
                        payload=fallback_payload,
                        portal=fallback_portal,
                        portal_unreadable=fallback_portal_unreadable,
                    )
                return

            payload = deserialize_inbound(receipt.payload)
            agency = await db.get(Agency, receipt.agency_id)
            if agency is None:
                complete_receipt(receipt)
                return
            context = receipt.context or {}
            reply = pending_reply(receipt)
            if reply is not None:
                lease_pending_reply(receipt)
            else:
                if await inbound_already_processed(
                    db, receipt.agency_id, payload.external_message_id
                ):
                    complete_receipt(receipt)
                    return
                reply = await _process_inbound_email(
                    agency_id=receipt.agency_id,
                    agency_slug=agency.slug,
                    payload=payload,
                    portal=context.get("portal"),
                    portal_unreadable=bool(context.get("portal_unreadable")),
                    db=db,
                )
                if reply is None:
                    complete_receipt(receipt)
                    return
                stage_reply(receipt, reply)
    except Exception as exc:
        await _settle_inbound_email_receipt(receipt_id, error=exc)
        return

    try:
        await _deliver_pending_reply(reply)
    except Exception as exc:
        await _settle_inbound_email_receipt(receipt_id, error=exc)
        return
    await _settle_inbound_email_receipt(receipt_id)


async def _process_inbound_email(
    *,
    agency_id: uuid.UUID,
    payload: InboundPayload,
    portal: str | None,
    portal_unreadable: bool,
    agency_slug: str = "",
    db: AsyncSession | None = None,
) -> dict | None:
    if db is None:
        async with task_session() as processing_db:
            result = await handle_inbound(processing_db, payload)
    else:
        result = await handle_inbound(db, payload)

    if portal_unreadable:
        # No reply: the only address we have is the portal's noreply. Tell
        # the agency instead so a human answers from their own inbox.
        try:
            async with task_session() as db:
                await alert_unreadable_portal_email(
                    db, agency_id, portal, result.conversation_id
                )
        except Exception:
            log.exception("email.portal_unreadable.alert_failed", agency=str(agency_id))
        return

    if not result.reply_text or not payload.identity.email:
        return

    if not can_reply_to(payload.identity.email):
        # (193) Belt and braces: even a parse that "worked" can leave a
        # noreply address behind. Writing there looks like an answer and is
        # not one.
        #
        # A portal lead lands here when the body gave us a phone but no
        # email: "usable" enough to create the lead, not enough to answer
        # it. Suppressing the reply and saying nothing would be the silence
        # this item exists to remove, so the agency is told — with the phone
        # number, because the action is a call, not an inbox.
        log.warning(
            "email.reply_suppressed.noreply",
            agency=str(agency_id),
            to=payload.identity.email,
        )
        if portal is not None:
            try:
                async with task_session() as db:
                    await alert_unreadable_portal_email(
                        db,
                        agency_id,
                        portal,
                        result.conversation_id,
                        contact_phone=payload.identity.phone_e164,
                    )
            except Exception:
                log.exception(
                    "email.portal_no_reply_address.alert_failed", agency=str(agency_id)
                )
        return

    headers = (payload.raw or {}).get("headers") or {}
    in_reply_to = (payload.external_message_id or "").strip() or None
    references = []
    if existing := headers.get("References"):
        references = existing.split()
    if in_reply_to and in_reply_to not in references:
        references.append(in_reply_to)

    subject = (payload.raw or {}).get("subject") or "Tu consulta"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    # Reply-To: the agency's leads address, NOT the shared sender. The From is
    # hola@selai.es (the verified domain), so without this every "responder"
    # the client presses lands in the platform mailbox and the conversation
    # dies there: the agency never sees the answer, the AI never gets its next
    # turn. With it, the client's reply re-enters through the leads pipeline
    # and lands in the same conversation.
    # The model writes light markdown; a text-only email shows the asterisks
    # literally. Two parts: clean text + minimal personal-looking HTML.
    reply_text, reply_html = email_parts(result.reply_text)
    return {
        "to": payload.identity.email,
        "subject": subject,
        "text": reply_text,
        "html": reply_html,
        "in_reply_to": in_reply_to,
        "references": references or None,
        "reply_to": leads_email_for(agency_slug) if agency_slug else None,
    }


async def _agency_for_email(db: AsyncSession, to_addr: str) -> Agency | None:
    """Resolve agency by `To` address local-part → slug match.

    Returns the row (not just the id): the reply path needs the slug to build
    the Reply-To, and this lookup already paid for it.
    """
    if not to_addr or "@" not in to_addr:
        return None
    local = to_addr.split("@", 1)[0].lower()
    # Convention: <slug>-leads@... or <slug>@...
    candidate_slug = local.removesuffix("-leads")
    stmt = select(Agency).where(Agency.slug == candidate_slug)
    return (await db.execute(stmt)).scalar_one_or_none()

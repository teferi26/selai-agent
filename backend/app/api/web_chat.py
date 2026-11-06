"""Web chat widget endpoints.

Anonymous visitors get a session cookie / token and a synthetic identity.
We persist a Lead per session — without phone/email at first; if the
agent's flow asks for contact info we save it as the conversation evolves.

Endpoints:
  GET  /webhooks/web/widget.js   — legacy widget, see the caveat below.
  POST /webhooks/web/{agency_slug}/message — append a message, get reply.

Caveat on `widget.js`: it POSTs straight to this API from the page that
embeds it, and CORS (app/main.py) only allows selai.es and previews — so on
an agency's own domain the browser blocks the call and nothing works. The
widget the dashboard actually hands out (dashboard/public/widget.js, the
snippet in Ajustes → Canales) opens `selai.es/chat/{slug}` in an iframe
instead, which is same-origin with this endpoint's allowed list. Keep this
one for same-origin embeds and demos; do not advertise it as drop-in.
"""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Agency, ConversationChannel, Message, MessageDirection
from app.db.session import get_db
from app.services.conversation_service import InboundPayload, handle_inbound
from app.services.lead_resolver import InboundIdentity
from app.services.plan_limits import is_subscription_blocked

router = APIRouter(prefix="/webhooks/web", tags=["web"])
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Per-session rate limit
# ---------------------------------------------------------------------------
# The per-IP limit lives in RateLimitMiddleware; this is the second axis: one
# browser session cannot burn the LLM budget on its own, even from rotating
# IPs. Deliberately in-memory and per-process — it is NOT shared between
# workers or across restarts, so the real ceiling is
# `SESSION_MAX_PER_WINDOW × workers`. Good enough to stop a runaway widget or
# a casual script; a distributed limiter (Redis) is the eventual fix.
SESSION_WINDOW_SECONDS = 60
SESSION_MAX_PER_WINDOW = 12
# Hard cap on tracked sessions so the dict can't grow without bound. The
# session id comes from the browser, so it is not a scarce resource: whoever
# wants to can mint a new one per request. Hence an LRU — sweeping the whole
# dict on every call once it filled up would turn that into extra work per
# request, which is exactly the wrong direction. Evicting the
# least-recently-seen session costs nothing and only ever forgives requests
# that already stopped coming.
_MAX_TRACKED_SESSIONS = 10_000

_session_hits: OrderedDict[str, deque[float]] = OrderedDict()


def _session_rate_limited(session_id: str, *, now: float | None = None) -> bool:
    """Record a hit for `session_id`; True when it just exceeded its budget."""
    now = time.time() if now is None else now
    cutoff = now - SESSION_WINDOW_SECONDS

    window = _session_hits.get(session_id)
    if window is None:
        window = _session_hits[session_id] = deque()
    _session_hits.move_to_end(session_id)
    while len(_session_hits) > _MAX_TRACKED_SESSIONS:
        _session_hits.popitem(last=False)
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= SESSION_MAX_PER_WINDOW:
        return True
    window.append(now)
    return False


class WebMessage(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    message_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    text: str = Field(min_length=1, max_length=4000)
    page_url: str | None = None
    user_agent: str | None = None


async def _replay_web_reply(
    db: AsyncSession,
    agency_id: uuid.UUID,
    external_message_id: str,
) -> str | None:
    """Return the answer already produced for an idempotent web message.

    The browser may lose the HTTP response after the message was committed.
    Replaying with the same client id must return the stored answer instead of
    running the agent again. ``None`` means the inbound id is not stored.
    """
    inbound = (
        await db.execute(
            select(Message.conversation_id, Message.created_at)
            .where(
                Message.agency_id == agency_id,
                Message.external_message_id == external_message_id,
                Message.direction == MessageDirection.INBOUND,
            )
            .limit(1)
        )
    ).first()
    if inbound is None:
        return None

    reply = (
        await db.execute(
            select(Message.content)
            .where(
                Message.agency_id == agency_id,
                Message.conversation_id == inbound.conversation_id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.created_at >= inbound.created_at,
            )
            .order_by(Message.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    # Empty is a valid result when the conversation was handed to a human.
    return reply or ""


@router.post("/{agency_slug}/message")
async def web_message(
    agency_slug: str,
    body: WebMessage,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    agency = (
        await db.execute(select(Agency).where(Agency.slug == agency_slug))
    ).scalar_one_or_none()
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")
    agency_id = agency.id

    external_message_id = (
        f"web:{body.session_id}:{body.message_id}"
        if body.message_id
        else str(uuid.uuid4())
    )
    if body.message_id:
        replayed = await _replay_web_reply(db, agency_id, external_message_id)
        if replayed is not None:
            return {"reply": replayed}

    # A genuine retry with the same message id was handled above and does not
    # consume another rate-limit slot or another model call.
    if _session_rate_limited(body.session_id):
        log.warning("web.rate_limited", agency_slug=agency_slug)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited")

    # A paused/canceled account must not burn LLM budget nor collect leads.
    if await is_subscription_blocked(db, agency_id):
        log.info("web.skipped_blocked_subscription", agency=str(agency_id))
        return {
            "reply": "Este chat no está disponible en este momento. "
            "Puedes contactar con la agencia directamente."
        }

    identity = InboundIdentity(
        channel=ConversationChannel.WEB,
        external_thread_id=body.session_id,
        # No phone or email yet — will be enriched as the conversation flows.
        display_name="Visitante web",
    )
    payload = InboundPayload(
        identity=identity,
        agency_id=agency_id,
        text=body.text,
        external_message_id=external_message_id,
        media=[],
        raw={
            "page_url": body.page_url,
            "user_agent": body.user_agent or request.headers.get("user-agent"),
            "ip": request.client.host if request.client else None,
            "client_message_id": body.message_id,
        },
    )
    try:
        result = await handle_inbound(db, payload)
        await db.commit()
    except IntegrityError:
        # The partial unique index is the final race barrier: if two workers
        # received the same client id simultaneously, only the winner reaches
        # the model. Once its transaction commits, replay that stored answer.
        await db.rollback()
        if body.message_id:
            replayed = await _replay_web_reply(db, agency_id, external_message_id)
            if replayed is not None:
                return {"reply": replayed}
        raise
    except Exception:
        await db.rollback()
        log.exception("web.handle_inbound.failed")
        raise

    return {"reply": result.reply_text or ""}


@router.get("/widget.js")
async def widget_js() -> Response:
    """Same-origin widget (see the module docstring: CORS keeps it from
    working on an agency's own domain — that is what dashboard/public/
    widget.js is for). Embedded as:
        <script src="https://YOUR_DOMAIN/webhooks/web/widget.js"
                data-slug="piloto"></script>
    """
    js = """
(function () {
  var s = document.currentScript;
  var slug = s && s.dataset && s.dataset.slug;
  if (!slug) { console.warn('[realtor-ai] missing data-slug'); return; }
  var origin = new URL(s.src).origin;
  var sessionKey = 'realtor_ai_sess_' + slug;
  var session = localStorage.getItem(sessionKey);
  if (!session) {
    session = (crypto && crypto.randomUUID) ? crypto.randomUUID()
              : Math.random().toString(36).slice(2) + Date.now();
    localStorage.setItem(sessionKey, session);
  }

  var btn = document.createElement('button');
  btn.innerHTML = '\\u{1F4AC}';
  btn.setAttribute('aria-label', 'Chat');
  btn.style.cssText =
    'position:fixed;bottom:20px;right:20px;width:56px;height:56px;border-radius:50%;' +
    'border:none;background:#2563eb;color:#fff;font-size:24px;cursor:pointer;' +
    'box-shadow:0 8px 24px rgba(0,0,0,.18);z-index:2147483646;';

  var panel = document.createElement('div');
  panel.style.cssText =
    'position:fixed;bottom:90px;right:20px;width:340px;max-height:520px;display:none;' +
    'background:#fff;border-radius:12px;box-shadow:0 12px 32px rgba(0,0,0,.18);' +
    'overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;' +
    'z-index:2147483647;flex-direction:column;';

  var header = document.createElement('div');
  header.style.cssText = 'background:#2563eb;color:#fff;padding:12px 16px;font-weight:600;';
  header.textContent = 'Atención inmediata';

  var log = document.createElement('div');
  log.style.cssText = 'flex:1;padding:12px;overflow-y:auto;font-size:14px;height:340px;';

  var form = document.createElement('form');
  form.style.cssText = 'border-top:1px solid #e5e7eb;display:flex;padding:8px;';
  var input = document.createElement('input');
  input.placeholder = 'Escribe tu mensaje...';
  input.style.cssText = 'flex:1;border:1px solid #cbd5e1;border-radius:8px;padding:8px;font-size:14px;outline:none;';
  var send = document.createElement('button');
  send.textContent = 'Enviar';
  send.style.cssText = 'margin-left:6px;border:none;background:#2563eb;color:#fff;padding:8px 12px;border-radius:8px;cursor:pointer;';
  form.appendChild(input);
  form.appendChild(send);

  panel.appendChild(header);
  panel.appendChild(log);
  panel.appendChild(form);
  document.body.appendChild(btn);
  document.body.appendChild(panel);

  function bubble(text, who) {
    var d = document.createElement('div');
    d.textContent = text;
    d.style.cssText =
      'max-width:80%;margin:6px 0;padding:8px 12px;border-radius:12px;line-height:1.35;' +
      (who === 'me'
        ? 'margin-left:auto;background:#2563eb;color:#fff;border-bottom-right-radius:2px;'
        : 'background:#f1f5f9;color:#0f172a;border-bottom-left-radius:2px;');
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
  }

  btn.addEventListener('click', function () {
    panel.style.display = panel.style.display === 'flex' ? 'none' : 'flex';
    if (panel.style.display === 'flex') input.focus();
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    bubble(text, 'me');
    input.value = '';
    fetch(origin + '/webhooks/web/' + slug + '/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: session,
        text: text,
        page_url: location.href,
        user_agent: navigator.userAgent,
      }),
    })
    .then(function (r) {
      if (r.status === 429) {
        bubble('Estás enviando mensajes muy seguidos. Espera un minuto y sigue.', 'ai');
        return null;
      }
      return r.json();
    })
    .then(function (j) { if (j && j.reply) bubble(j.reply, 'ai'); })
    .catch(function () { bubble('Disculpa, fallo de conexión.', 'ai'); });
  });
})();
"""
    return Response(content=js, media_type="application/javascript")

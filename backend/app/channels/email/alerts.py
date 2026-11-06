"""Alerts the email channel raises on its own (item 193).

One case, and it is the expensive one: an email arrives from a portal we
recognise (Idealista, Fotocasa, pisos.com…) and we cannot pull a phone or
an email out of it. The portal changed its template, or the lead only left
a name. Today that ends in silence — the agency never learns a buyer wrote.

So the conversation is still created (it is a real lead, and the raw email
is worth reading), the automatic reply is suppressed (the sender is a
noreply nobody reads), and the agency gets a push and an email pointing at
the conversation so a human can pick it up.

The HTML builder lives here rather than in `app/services/emails.py` on
purpose: that file is the agency-lifecycle mailer, this is one alert owned
by the channel that raises it.
"""
from __future__ import annotations

import uuid
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Agency

log = get_logger(__name__)

CONVERSATIONS_URL = "https://www.selai.es/conversations"
BRAND_COLOR = "#5B61F0"

PORTAL_LABELS = {
    "idealista": "Idealista",
    "fotocasa": "Fotocasa",
    "habitaclia": "Habitaclia",
    "pisos.com": "pisos.com",
    "yaencontre": "yaencontre",
    "milanuncios": "Milanuncios",
}


def portal_label(portal: str | None) -> str:
    key = (portal or "").strip().lower()
    return PORTAL_LABELS.get(key, key or "un portal")


def unreadable_portal_subject(portal: str | None, contact_phone: str | None = None) -> str:
    if contact_phone:
        return f"Un lead de {portal_label(portal)} espera que le llames tú"
    return f"Ha llegado un lead de {portal_label(portal)} que no hemos podido leer"


def build_unreadable_portal_email_html(
    portal: str | None,
    conversation_id: uuid.UUID | None,
    contact_phone: str | None = None,
) -> str:
    """Plain, inline-styled, and honest about what happened and what to do.

    Two versions of the same situation, because they call for two different
    actions: we could not read anything ("ábrelo tú"), or we read a phone
    but no email ("llámale"). Saying "no hemos conseguido nada" while the
    phone number is sitting in the conversation would be a small lie the
    owner catches immediately, and it costs them the call.
    """
    label = escape(portal_label(portal))
    link = (
        f"{CONVERSATIONS_URL}/{conversation_id}"
        if conversation_id is not None
        else CONVERSATIONS_URL
    )
    if contact_phone:
        headline = f"Un lead de {label} te ha dejado su teléfono"
        explanation = (
            f"Hemos recibido un aviso de {label} con el teléfono de la persona "
            f"interesada (<strong>{escape(contact_phone)}</strong>), pero sin un email "
            "al que contestar: la única dirección del aviso es automática y no la lee "
            "nadie. Tu asistente no le ha escrito."
        )
        next_step = "Llámale o escríbele por WhatsApp. El aviso completo está en la conversación."
    else:
        headline = f"Un lead de {label} necesita que lo mires tú"
        explanation = (
            f"Hemos recibido un aviso de {label}, pero no hemos conseguido sacar de él "
            "ni el teléfono ni el email de la persona interesada, así que tu asistente "
            "no le ha contestado: habríamos escrito a una dirección automática que "
            "nadie lee."
        )
        next_step = (
            "El correo original está guardado en la conversación. Ábrelo, mira los "
            "datos de contacto y responde tú."
        )
    return f"""\
<div style="background:#f4f5fb;padding:32px 16px;font-family:Arial,Helvetica,sans-serif;color:#1f2333;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;">
    <div style="background:{BRAND_COLOR};padding:20px 28px;">
      <span style="color:#ffffff;font-size:20px;font-weight:bold;">selai</span>
    </div>
    <div style="padding:28px;">
      <h1 style="margin:0 0 16px;font-size:20px;">{headline}</h1>
      <p style="margin:0 0 12px;font-size:15px;line-height:1.5;">
        {explanation}
      </p>
      <p style="margin:0 0 20px;font-size:15px;line-height:1.5;">
        {next_step}
      </p>
      <p style="margin:0 0 8px;">
        <a href="{escape(link)}"
           style="display:inline-block;background:{BRAND_COLOR};color:#ffffff;text-decoration:none;padding:10px 18px;border-radius:8px;font-size:15px;">
          Ver la conversación
        </a>
      </p>
    </div>
    <div style="padding:16px 28px;border-top:1px solid #ececf4;color:#8a8fa3;font-size:12px;">
      selai — tu agente inmobiliario con IA · <a href="https://www.selai.es" style="color:{BRAND_COLOR};">selai.es</a>
    </div>
  </div>
</div>"""


async def alert_unreadable_portal_email(
    db: AsyncSession,
    agency_id: uuid.UUID,
    portal: str | None,
    conversation_id: uuid.UUID | None,
    contact_phone: str | None = None,
) -> None:
    """Push + email the agency. Best-effort at every step, never raises."""
    subject = unreadable_portal_subject(portal, contact_phone)
    label = portal_label(portal)
    if contact_phone:
        body = (
            f"El aviso de {label} trae teléfono ({contact_phone}) pero no email. "
            "Llámale tú."
        )
    else:
        body = (
            f"No hemos podido leer los datos del interesado en el aviso de {label}. "
            "Ábrelo y contéstale tú."
        )

    try:
        from app.services.push import send_push

        await send_push(
            db,
            agency_id,
            subject,
            body,
            data=(
                {"conversation_id": str(conversation_id)}
                if conversation_id is not None
                else None
            ),
        )
    except Exception:
        log.exception("email.portal_unreadable.push_failed", agency=str(agency_id))

    try:
        from app.services.emails import send_email

        agency = await db.get(Agency, agency_id)
        if agency is not None and agency.contact_email:
            await send_email(
                to=agency.contact_email,
                subject=subject,
                html=build_unreadable_portal_email_html(
                    portal, conversation_id, contact_phone
                ),
            )
    except Exception:
        log.exception("email.portal_unreadable.email_failed", agency=str(agency_id))

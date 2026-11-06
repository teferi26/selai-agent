"""Product/transactional emails via Resend.

Distinct from `app.channels.email.client.ResendClient` (lead-facing channel
with threading headers): these are *agency-facing* product emails — welcome,
handoff alerts, trial reminders, weekly digests.

`send_email` is deliberately tolerant: without an API key it logs and
returns False; provider errors return False too. It never raises, so any
caller can fire-and-forget without wrapping in try/except (callers still
wrap defensively — cheap belt and braces).
"""
from __future__ import annotations

import uuid
from html import escape as _escape

import httpx

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

_RESEND_URL = "https://api.resend.com/emails"

BRAND_COLOR = "#5B61F0"
SETUP_URL = "https://www.selai.es/setup"
BILLING_URL = "https://www.selai.es/billing"
CONVERSATIONS_URL = "https://www.selai.es/conversations"
# Landing page of a team invitation; the token is appended by the caller.
INVITATION_URL = "https://www.selai.es/invitacion"

# Our own inbox: where the emails that are *for us* land — support tickets,
# manual phone-number provisioning. `app/api/channels.py` names the same
# address `PHONE_REQUESTS_EMAIL`; if it ever changes, both have to move.
INTERNAL_EMAIL = "hola@selai.es"

# ---------------------------------------------------------------------------
# (233) One-click unsubscribe + per-category preferences
# ---------------------------------------------------------------------------

UNSUBSCRIBE_MAILTO = "baja@selai.es"
UNSUBSCRIBE_URL = f"{BILLING_URL}#emails"

# The three switches an agency can flip in /billing#emails, and the value of
# `category` that selects each:
#
#   digest     the Monday activity summary
#   alerts     hot lead, handoff, usage warnings
#   lifecycle  trial reminders, setup rescue, first week, win-back
#
# Everything else is transactional and carries **no** category: dunning, the
# payment confirmation, the "your AI number is live" notice. Those always go
# out and carry no unsubscribe header — nobody can opt out of "we could not
# charge your card". Keeping the header off them is also what stops the
# one-click list-unsubscribe from being read as "this is bulk mail".
EMAIL_CATEGORIES: tuple[str, ...] = ("digest", "alerts", "lifecycle")
DEFAULT_EMAIL_PREFS: dict[str, bool] = dict.fromkeys(EMAIL_CATEGORIES, True)

# Which category each product email belongs to — the single place that says
# so, because the answer is a legal and commercial decision, not a per-caller
# taste. `None` means transactional: it always goes out and carries no
# unsubscribe header.
#
# The senders live in `app/workers/tasks.py`, `app/api/billing.py` and
# `app/api/agency_settings.py`; every one of them should read its category
# from here (`category_for("weekly_digest")`) instead of hard-coding a string,
# so nobody can quietly file a critical notice under an opt-out-able
# category. Two entries in particular are load-bearing:
#
#   payment_failed  dunning. "We could not charge your card" is the opposite
#                   of marketing and there is nothing to unsubscribe from.
#   trial_ended     the account has just been *paused*. It is a service
#                   notice, not a nudge — unlike `trial_ending`, which warns
#                   about something that has not happened yet and is a beat.
EMAIL_CATEGORY_BY_KIND: dict[str, str | None] = {
    # Opt-out-able.
    "weekly_digest": "digest",
    "handoff": "alerts",
    "hot_lead": "alerts",
    "office_hours": "alerts",
    "voice_usage_alert": "alerts",
    "resource_usage_alert": "alerts",
    "trial_ending": "lifecycle",
    "setup_rescue": "lifecycle",
    "first_week": "lifecycle",
    "winback": "lifecycle",
    # Transactional — never filtered, never carries an unsubscribe header.
    "welcome": None,
    "payment_welcome": None,
    "payment_failed": None,
    "trial_ended": None,
    "phone_active": None,
    "phone_number_request": None,
    # The recipient is not a customer yet — they are a person being given
    # access to one. There is no preference of theirs to read and nothing to
    # unsubscribe from: without this email the invitation does not exist.
    "team_invitation": None,
    # Internal: it goes to us, not to the agency. Filtering it by the
    # agency's preferences would silence the ticket the user just wrote, and
    # a List-Unsubscribe header on our own inbox means nothing.
    "support_ticket": None,
}


def category_for(kind: str) -> str | None:
    """Category of a product email by name. Unknown names are transactional —
    a new email nobody classified must not silently become opt-out-able."""
    return EMAIL_CATEGORY_BY_KIND.get(kind)


def email_prefs(blob: object) -> dict[str, bool]:
    """Read the three switches out of an `Agency.settings` blob.

    The one implementation of the rule, shared by `should_send` (which
    decides) and the /settings GET (which reports): if they ever disagreed,
    the panel would show a switch as off while the mail kept going out.

    Defaults are all-on, and anything that is not the literal `False` counts
    as on — a blob written before this existed, or corrupted, means "never
    configured", and the failure mode of guessing wrong here is silencing an
    agency that never asked for silence.
    """
    prefs = dict(DEFAULT_EMAIL_PREFS)
    stored = blob.get("email_prefs") if isinstance(blob, dict) else None
    if isinstance(stored, dict):
        for key in EMAIL_CATEGORIES:
            if key in stored:
                prefs[key] = stored[key] is not False
    return prefs


def unsubscribe_headers() -> dict[str, str]:
    """RFC 8058 one-click unsubscribe headers.

    Gmail and Outlook now demand these of anyone sending bulk mail; without
    them they quietly degrade delivery of *every* message from the domain,
    including the hot-lead alerts that make the customer money.
    """
    return {
        "List-Unsubscribe": f"<mailto:{UNSUBSCRIBE_MAILTO}>, <{UNSUBSCRIBE_URL}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def should_send(agency: object, category: str | None = None) -> bool:
    """Does `agency` still want this category of email?

    Preferences live in `agency.settings["email_prefs"]` (patchable from
    /settings) and default to True, so an agency that never touched them —
    or a category that is not opt-out-able at all — always gets the email.
    `category=None` is the transactional path and is never filtered, which is
    what guarantees dunning and "we have paused your account" still land in
    an inbox that switched everything else off.
    """
    if not category or category not in EMAIL_CATEGORIES:
        return True
    return email_prefs(getattr(agency, "settings", None))[category]


async def send_email(to: str, subject: str, html: str, category: str | None = None) -> bool:
    """Send an HTML email. Returns True on success, False otherwise. Never raises.

    `category` is optional and backwards compatible: without it the email is
    treated as transactional (no unsubscribe headers, no preference check).
    With one of `EMAIL_CATEGORIES` the message gets the one-click unsubscribe
    headers. Filtering by preference is the caller's job via `should_send`,
    which needs the agency this sender does not have.
    """
    api_key = settings.resend_api_key.get_secret_value()
    if not api_key:
        log.warning("emails.no_api_key.skipping", to=to, subject=subject)
        return False

    payload: dict = {
        "from": settings.resend_from,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if category in EMAIL_CATEGORIES:
        payload["headers"] = unsubscribe_headers()

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                _RESEND_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code >= 400:
            log.error("emails.send_failed", to=to, status=resp.status_code, body=resp.text[:200])
            return False
        return True
    except Exception:
        log.exception("emails.send_error", to=to, subject=subject)
        return False


# ---------------------------------------------------------------------------
# HTML builders — pure functions, unit-testable without network.
# ---------------------------------------------------------------------------

def _layout(title: str, body_html: str) -> str:
    """Shared shell: plain, inline-styled, renders everywhere."""
    return f"""\
<div style="background:#f4f5fb;padding:32px 16px;font-family:Arial,Helvetica,sans-serif;color:#1f2333;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;">
    <div style="background:{BRAND_COLOR};padding:20px 28px;">
      <span style="color:#ffffff;font-size:20px;font-weight:bold;">selai</span>
    </div>
    <div style="padding:28px;">
      <h1 style="margin:0 0 16px;font-size:20px;color:#1f2333;">{title}</h1>
      {body_html}
    </div>
    <div style="padding:16px 28px;border-top:1px solid #ececf4;color:#8a8fa3;font-size:12px;">
      selai — tu agente inmobiliario con IA · <a href="https://www.selai.es" style="color:{BRAND_COLOR};">selai.es</a>
    </div>
  </div>
</div>"""


def _button(url: str, label: str) -> str:
    return (
        f'<a href="{url}" style="display:inline-block;background:{BRAND_COLOR};'
        'color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;'
        f'font-weight:bold;">{label}</a>'
    )


# The 7 puesta-en-marcha steps, with rough times. Rendered in the welcome
# email and asserted in tests.
WELCOME_STEPS: list[tuple[str, str]] = [
    ("Importa tu catálogo de propiedades (CSV)", "10 min"),
    ("Conecta tu WhatsApp Business", "15 min"),
    ("Conecta tu email de captación", "5 min"),
    ("Elige la voz de tu agente telefónico", "2 min"),
    ("Conecta tu Google Calendar para visitas", "5 min"),
    ("Ajusta el tono y los datos de tu agencia", "10 min"),
    ("Haz una conversación de prueba con tu agente", "5 min"),
]


def build_welcome_email_html(agency_name: str, owner_name: str | None = None) -> str:
    first = (owner_name or "").split(" ", 1)[0]
    greeting = f"Hola{' ' + first if first else ''}, ¡bienvenido a selai!"
    steps_html = "".join(
        '<tr>'
        f'<td style="padding:8px 12px 8px 0;color:{BRAND_COLOR};font-weight:bold;">{i}.</td>'
        f'<td style="padding:8px 0;">{step}</td>'
        f'<td style="padding:8px 0 8px 12px;color:#8a8fa3;white-space:nowrap;">{mins}</td>'
        "</tr>"
        for i, (step, mins) in enumerate(WELCOME_STEPS, start=1)
    )
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">{greeting}</p>
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{agency_name}</strong> ya tiene su agente de IA listo para atender
  WhatsApp, email y llamadas. En menos de una hora lo tienes trabajando:
</p>
<table style="border-collapse:collapse;margin:0 0 20px;font-size:14px;">{steps_html}</table>
<p style="margin:0 0 24px;">{_button(SETUP_URL, "Empezar la puesta en marcha")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  ¿Dudas? Responde a este email y te ayudamos.
</p>"""
    return _layout("Tu agente de IA está listo", body)


def build_handoff_email_html(
    lead_name: str | None,
    conversation_id: uuid.UUID | str,
    details: list[str] | None = None,
) -> str:
    name = lead_name or "Un lead"
    url = f"{CONVERSATIONS_URL}/{conversation_id}"
    details_html = ""
    if details:
        items = "".join(
            f'<li style="margin:0 0 6px;line-height:1.5;">{d}</li>' for d in details if d
        )
        if items:
            details_html = f'<ul style="margin:0 0 16px;padding-left:20px;">{items}</ul>'
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{name}</strong> está esperando a un agente humano: la IA ha
  escalado la conversación.
</p>
{details_html}<p style="margin:0 0 24px;">{_button(url, "Abrir la conversación")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Responder rápido marca la diferencia: los leads calientes se enfrían en minutos.
</p>"""
    return _layout("Un lead necesita atención humana", body)


def build_trial_ending_email_html(
    agency_name: str,
    days_left: int,
    conversations: int,
    leads: int,
) -> str:
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  A <strong>{agency_name}</strong> le quedan <strong>{days_left} días</strong>
  de prueba en selai.
</p>
<p style="margin:0 0 16px;line-height:1.5;">
  Durante la prueba tu agente ha gestionado
  <strong>{conversations} conversaciones</strong> y
  <strong>{leads} leads</strong>. Para que no pare, activa tu plan:
</p>
<p style="margin:0 0 24px;">{_button(BILLING_URL, "Elegir mi plan")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Si tienes cualquier duda sobre planes o precios, responde a este email.
</p>"""
    return _layout(f"Te quedan {days_left} días de prueba", body)


def _stat_cell(value: int, label: str) -> str:
    return (
        '<td style="padding:12px 16px;text-align:center;background:#f4f5fb;'
        'border-radius:8px;">'
        f'<div style="font-size:24px;font-weight:bold;color:{BRAND_COLOR};">{value}</div>'
        f'<div style="font-size:12px;color:#5a5f73;">{label}</div>'
        "</td>"
    )


def build_trial_ended_email_html(
    agency_name: str,
    leads: int,
    conversations: int,
    visits: int,
) -> str:
    """(152) The 14-day trial ran out without a card: the account is paused.

    Says plainly what has stopped and what the trial produced, so choosing a
    plan is a decision with numbers behind it.

    The list of channels here has to match what `BLOCKING_STATUSES` really
    stops. Today that is the phone: `app/api/voice.py` gates the inbound
    brain and `app/api/voice_outbound.py` the outgoing calls. The WhatsApp
    and email webhooks do not call `ensure_subscription_usable` yet, so this
    email must not claim they stopped — add the gate there first, then the
    channels back here.
    """
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  La prueba de <strong>{agency_name}</strong> ha terminado y hemos pausado la
  cuenta: tu agente de IA ya no atiende las llamadas de teléfono.
</p>
<p style="margin:0 0 16px;line-height:1.5;">
  Esto es lo que consiguió durante la prueba:
</p>
<table style="border-collapse:separate;border-spacing:8px;margin:0 0 20px;width:100%;">
  <tr>
    {_stat_cell(leads, "leads captados")}
    {_stat_cell(conversations, "conversaciones")}
    {_stat_cell(visits, "visitas agendadas")}
  </tr>
</table>
<p style="margin:0 0 16px;line-height:1.5;">
  Tus datos siguen intactos. En cuanto elijas plan, el agente vuelve a
  trabajar donde lo dejó.
</p>
<p style="margin:0 0 24px;">{_button(BILLING_URL, "Elegir mi plan")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  ¿Dudas sobre qué plan te encaja? Responde a este email y te ayudamos.
</p>"""
    return _layout("Tu prueba ha terminado", body)


def build_payment_failed_email_html(
    agency_name: str,
    portal_url: str = BILLING_URL,
    attempt: int = 1,
) -> str:
    """(153) Stripe could not charge the card — dunning.

    `attempt` 1/2/3 maps to the day-0 / day-3 / day-7 reminders; the tone
    gets more concrete each time without ever threatening something the
    code does not actually do.
    """
    if attempt >= 3:
        lead_in = (
            "Seguimos sin poder cobrar tu suscripción de selai. Si no podemos "
            "hacerlo en los próximos días, Stripe cancelará la suscripción y "
            "tu agente dejará de atender."
        )
    elif attempt == 2:
        lead_in = (
            "Lo hemos vuelto a intentar y el cobro de tu suscripción de selai "
            "sigue sin salir. Suele ser una tarjeta caducada o sin fondos."
        )
    else:
        lead_in = (
            "No hemos podido cobrar tu último recibo de selai. Suele ser una "
            "tarjeta caducada, un límite del banco o un pago sin confirmar."
        )
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{agency_name}</strong>: {lead_in}
</p>
<p style="margin:0 0 16px;line-height:1.5;">
  Se arregla en un minuto actualizando el método de pago:
</p>
<p style="margin:0 0 24px;">{_button(portal_url, "Actualizar mi método de pago")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Si crees que es un error del banco, responde a este email y lo miramos contigo.
</p>"""
    return _layout("No hemos podido cobrar tu suscripción", body)


def build_weekly_digest_email_html(
    agency_name: str,
    conversations: int,
    leads: int,
    visits: int,
) -> str:
    stat = _stat_cell

    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  Esto es lo que el agente de <strong>{agency_name}</strong> ha hecho en los
  últimos 7 días:
</p>
<table style="border-collapse:separate;border-spacing:8px;margin:0 0 20px;width:100%;">
  <tr>
    {stat(conversations, "conversaciones")}
    {stat(leads, "leads nuevos")}
    {stat(visits, "visitas agendadas")}
  </tr>
</table>
<p style="margin:0 0 24px;">{_button("https://www.selai.es", "Ver mi dashboard")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Recibirás este resumen cada lunes por la mañana.
</p>"""
    return _layout("Tu resumen semanal de selai", body)


def build_office_hours_email_html(lead_name: str | None, conversation_id: uuid.UUID | str) -> str:
    """(52) ai_mode=out_of_hours: a lead wrote within office hours, so the
    AI stays silent and the team must answer."""
    name = lead_name or "Un lead"
    url = f"{CONVERSATIONS_URL}/{conversation_id}"
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{name}</strong> acaba de escribir. Tienes la IA configurada para
  responder solo fuera de horario, así que esta conversación es para tu equipo.
</p>
<p style="margin:0 0 24px;">{_button(url, "Abrir la conversación")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Fuera de tu horario de oficina la IA volverá a responder automáticamente.
</p>"""
    return _layout("Nuevo mensaje en horario de oficina", body)


def build_hot_lead_email_html(
    lead_name: str | None,
    conversation_id: uuid.UUID | str,
    details: list[str] | None = None,
) -> str:
    """(72) A lead just transitioned to HOT temperature."""
    name = lead_name or "Un lead"
    url = f"{CONVERSATIONS_URL}/{conversation_id}"
    details_html = "".join(
        f'<li style="margin:0 0 6px;">{d}</li>' for d in (details or [])
    )
    details_block = (
        f'<ul style="margin:0 0 16px;padding-left:20px;line-height:1.5;">{details_html}</ul>'
        if details_html
        else ""
    )
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{name}</strong> acaba de pasar a <strong>lead caliente</strong> 🔥.
  Es el mejor momento para llamarle.
</p>
{details_block}
<p style="margin:0 0 24px;">{_button(url, "Ver la conversación")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Los leads calientes se enfrían en horas: cuanto antes contactes, mejor.
</p>"""
    return _layout(f"🔥 Lead caliente: {name}", body)


def build_voice_usage_alert_email_html(agency_name: str, used: int, limit: int) -> str:
    """(66) The agency burned >=80% of its plan's voice minutes.

    The wording describes what the code actually does: `ensure_can_use_voice`
    only gates *outbound* calls, so incoming ones keep being answered.
    """
    pct = int(used / limit * 100) if limit > 0 else 100
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{agency_name}</strong>, este mes has usado
  <strong>{used} de {limit} minutos</strong> de voz de tu plan ({pct}%).
</p>
<p style="margin:0 0 16px;line-height:1.5;">
  Cuando se agoten, tu agente dejará de hacer llamadas salientes hasta el
  próximo ciclo; las llamadas que entren se siguen atendiendo. Si necesitas
  más minutos, amplía tu plan:
</p>
<p style="margin:0 0 24px;">{_button(BILLING_URL, "Ampliar mi plan")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  ¿Dudas sobre qué plan te encaja? Responde a este email y te ayudamos.
</p>"""
    return _layout("Estás cerca del límite de minutos de voz", body)


def build_setup_rescue_email_html(agency_name: str) -> str:
    """(84) Agency created 2-3 days ago with the setup checklist unfinished."""
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  Vimos que <strong>{agency_name}</strong> empezó la puesta en marcha de su
  agente de IA pero aún no la ha terminado. Te quedan <strong>~10 minutos</strong>
  para tenerlo atendiendo WhatsApp, email y llamadas.
</p>
<p style="margin:0 0 24px;">{_button(SETUP_URL, "Terminar la puesta en marcha")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  ¿Te ayudamos a terminar? Escríbenos a
  <a href="mailto:hola@selai.es" style="color:{BRAND_COLOR};">hola@selai.es</a>
  o responde a este email y lo dejamos listo contigo.
</p>"""
    return _layout("¿Te ayudamos a terminar? Te quedan ~10 min", body)


def build_phone_number_request_email_html(
    agency_name: str,
    slug: str,
    contact_email: str | None,
) -> str:
    """Internal email to hola@selai.es: an agency asked for an AI phone
    number but Vapi had none available — provision one manually (<24h)."""
    rows = [
        ("Agencia", agency_name),
        ("Slug", slug),
        ("Email de contacto", contact_email or "(sin email de contacto)"),
    ]
    rows_html = "".join(
        "<tr>"
        f'<td style="padding:6px 12px 6px 0;color:#5a5f73;white-space:nowrap;">{label}</td>'
        f'<td style="padding:6px 0;font-weight:bold;">{value}</td>'
        "</tr>"
        for label, value in rows
    )
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  Una agencia ha pedido su número de teléfono IA desde el panel, pero Vapi
  no tenía números disponibles. Hay que aprovisionarlo a mano y dejarlo
  configurado en menos de 24h (es lo que promete el panel).
</p>
<table style="border-collapse:collapse;margin:0 0 20px;font-size:14px;">{rows_html}</table>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Al terminar, guarda el número en los ajustes de la agencia
  (<code>ai_phone_number</code> + <code>vapi_phone_number_id</code>) para que
  el panel lo muestre como activo.
</p>"""
    return _layout("Solicitud de número de teléfono IA", body)


SUPPORT_SURFACE_LABELS: dict[str, str] = {"web": "Panel web", "mobile": "App móvil"}

# Room for "Soporte selai · " plus a name, well under the ~78 characters a
# mail subject line is expected to keep to and far under anything a provider
# would reject.
_SUBJECT_FRAGMENT_MAX = 80


def _subject_fragment(value: str | None) -> str:
    """One bounded line, safe to drop into a mail header.

    Unlike the body, a subject is a *header*: the agency name is typed by the
    customer and the email claim comes from Clerk, so neither is ours. A stray
    newline has no business splitting a header (Resend builds the MIME itself,
    so this is a second lock, not the only one) and an absurdly long one risks
    the provider rejecting the whole ticket — which is the one email we cannot
    afford to lose.
    """
    collapsed = " ".join((value or "").split())
    if len(collapsed) > _SUBJECT_FRAGMENT_MAX:
        return collapsed[: _SUBJECT_FRAGMENT_MAX - 1] + "…"
    return collapsed


def build_support_ticket_email_html(
    *,
    message: str,
    surface: str,
    user_name: str | None = None,
    user_email: str | None = None,
    contact_email: str | None = None,
    agency_name: str | None = None,
    agency_slug: str | None = None,
    plan_name: str | None = None,
    screen: str | None = None,
    app_version: str | None = None,
    error_ref: str | None = None,
) -> tuple[str, str]:
    """Someone used the in-app support form. Returns `(subject, html)`.

    Internal email to `INTERNAL_EMAIL`, written for whoever picks up the
    ticket: everything the endpoint could resolve in one table, and the
    message underneath exactly as it was typed.

    Returns the subject too, unlike the other internal builder (whose subject
    is a fixed string): here it carries the agency — or the email when there
    is no agency — so a full inbox stays triageable at a glance. Same shape as
    `build_winback_email_html`.

    Every interpolated value is escaped. This is the only builder whose body
    is free text typed by a human, and an unescaped `<` would swallow the rest
    of the ticket precisely when the person is already having a bad day. The
    reply address gets a `mailto:` link because the email arrives *from* our
    own inbox: hitting reply would answer ourselves, not the user.
    """
    who = (
        _subject_fragment(agency_name)
        or _subject_fragment(user_email)
        or "usuario sin identificar"
    )
    subject = f"Soporte selai · {who}"

    person = " · ".join(p for p in (user_name, user_email) if p) or "(sin identificar)"
    if agency_name and agency_slug:
        agency_label = f"{agency_name} ({agency_slug})"
    else:
        agency_label = agency_name or "(sin agencia)"
    reply_to = contact_email or user_email
    reply_html = (
        f'<a href="mailto:{_escape(reply_to)}" style="color:{BRAND_COLOR};">'
        f"{_escape(reply_to)}</a>"
        if reply_to
        else "(sin email de contacto)"
    )

    rows: list[tuple[str, str]] = [
        ("Quién escribe", _escape(person)),
        ("Responder a", reply_html),
        ("Agencia", _escape(agency_label)),
        ("Plan", _escape(plan_name or "(sin plan)")),
        ("Superficie", _escape(SUPPORT_SURFACE_LABELS.get(surface, surface))),
        ("Pantalla", _escape(screen or "(no indicada)")),
        ("Versión", _escape(app_version or "(no indicada)")),
        ("Referencia de error", _escape(error_ref or "(ninguna)")),
    ]
    rows_html = "".join(
        "<tr>"
        f'<td style="padding:6px 12px 6px 0;color:#5a5f73;white-space:nowrap;vertical-align:top;">{label}</td>'
        f'<td style="padding:6px 0;font-weight:bold;">{value}</td>'
        "</tr>"
        for label, value in rows
    )
    # Line endings normalised first: a textarea from Windows sends \r\n, and
    # only the \n would become a <br>, leaving stray carriage returns.
    message_html = _escape(message).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")

    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  Un usuario ha escrito desde el formulario de ayuda de selai.
</p>
<table style="border-collapse:collapse;margin:0 0 20px;font-size:14px;">{rows_html}</table>
<div style="background:#f4f5fb;border-radius:8px;padding:16px;line-height:1.5;font-size:14px;">
  {message_html}
</div>"""
    return subject, _layout("Nuevo mensaje de soporte", body)


def _limits_table(rows: list[tuple[str, str]]) -> str:
    rows_html = "".join(
        "<tr>"
        f'<td style="padding:6px 12px 6px 0;color:#5a5f73;">{label}</td>'
        f'<td style="padding:6px 0;font-weight:bold;white-space:nowrap;">{value}</td>'
        "</tr>"
        for label, value in rows
    )
    return f'<table style="border-collapse:collapse;margin:0 0 20px;font-size:14px;">{rows_html}</table>'


def build_payment_welcome_email_html(
    agency_name: str,
    plan_name: str,
    max_properties: int,
    max_agents: int,
    max_voice_minutes: int,
    setup_pending: bool = True,
    trial_days_left: int = 0,
    portal_url: str = BILLING_URL,
) -> str:
    """(188) Stripe confirmed the checkout — say what was bought and what now.

    Sent once, from the `checkout.session.completed` webhook. The moment of
    maximum commitment (99-399 EUR/month just committed) used to pass in
    complete silence.

    `trial_days_left` keeps the email honest: a checkout that starts a trial
    has taken no money yet, so it must not say "gracias por tu pago". The
    limits printed are the plan's real `max_*` columns, and the "next step"
    only points at the wizard when the checklist is actually unfinished —
    pushing a set-up that is already done reads as spam.
    """
    limits = _limits_table(
        [
            ("Propiedades en catálogo", f"hasta {max_properties}"),
            ("Usuarios de tu equipo", f"hasta {max_agents}"),
            ("Minutos de voz al mes", f"{max_voice_minutes}"),
        ]
    )
    if trial_days_left > 0:
        opening = (
            f"Ya está, <strong>{agency_name}</strong>: tu plan "
            f"<strong>{plan_name}</strong> está activo. Todavía no te hemos "
            f"cobrado nada — el primer cobro será cuando terminen tus "
            f"<strong>{trial_days_left} días</strong> de prueba, y hasta "
            f"entonces puedes cancelar sin pagar."
        )
    else:
        opening = (
            f"Gracias, <strong>{agency_name}</strong>. Tu pago ha entrado y el "
            f"plan <strong>{plan_name}</strong> ya está activo."
        )
    if setup_pending:
        next_step = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  Te queda terminar la puesta en marcha para que tu agente empiece a atender.
  Son unos 10 minutos:
</p>
<p style="margin:0 0 24px;">{_button(SETUP_URL, "Terminar la puesta en marcha")}</p>"""
    else:
        next_step = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  Ya tienes la puesta en marcha terminada, así que tu agente sigue trabajando
  sin que tengas que tocar nada.
</p>
<p style="margin:0 0 24px;">{_button("https://www.selai.es", "Ver mi dashboard")}</p>"""

    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">{opening}</p>
<p style="margin:0 0 8px;line-height:1.5;">Esto es lo que incluye:</p>
{limits}
{next_step}
<p style="margin:0 0 16px;line-height:1.5;color:#5a5f73;font-size:13px;">
  Tus facturas y el método de pago están en
  <a href="{portal_url}" style="color:{BRAND_COLOR};">tu página de facturación</a>:
  ahí puedes descargar cada recibo, cambiar de tarjeta o cancelar cuando quieras.
</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  ¿Necesitas algo? Responde a este email o escríbenos a
  <a href="mailto:hola@selai.es" style="color:{BRAND_COLOR};">hola@selai.es</a>.
</p>"""
    return _layout(f"Tu plan {plan_name} ya está activo", body)


# Call-forwarding codes (GSM standard, funcionan en cualquier operador español).
FORWARDING_CODES: list[tuple[str, str]] = [
    ("Desviar todas las llamadas", "**21*{number}#"),
    ("Desviar solo si no contestas", "**61*{number}#"),
    ("Cancelar el desvío", "##21#"),
]


def build_phone_active_email_html(agency_name: str, phone_number: str) -> str:
    """(190) The team assigned the agency its AI phone number.

    The wizard says literally "te avisaremos por email" when Vapi has no
    number to hand out; this is that email. Without the forwarding codes the
    number is useless — the customer's existing landline has to point at it.
    """
    codes_html = "".join(
        "<tr>"
        f'<td style="padding:6px 12px 6px 0;color:#5a5f73;">{label}</td>'
        '<td style="padding:6px 0;"><code style="background:#f4f5fb;padding:4px 8px;'
        f'border-radius:6px;font-size:14px;">{template.format(number=phone_number)}</code></td>'
        "</tr>"
        for label, template in FORWARDING_CODES
    )
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{agency_name}</strong>, ya tienes número de teléfono para tu agente
  de IA:
</p>
<p style="margin:0 0 20px;font-size:22px;font-weight:bold;color:{BRAND_COLOR};">
  {phone_number}
</p>
<p style="margin:0 0 8px;line-height:1.5;">
  Puedes darlo tal cual, o desviar a él las llamadas del teléfono de la
  agencia. Marca estos códigos desde el teléfono que quieras desviar:
</p>
<table style="border-collapse:collapse;margin:0 0 20px;font-size:14px;">{codes_html}</table>
<p style="margin:0 0 24px;">{_button(SETUP_URL, "Ver el paso del teléfono")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Haz una llamada de prueba antes de desviar nada: así oyes exactamente lo
  que oirán tus clientes. Si algo no suena como quieres, responde a este email.
</p>"""
    return _layout("Tu número de IA ya está activo", body)


# ---------------------------------------------------------------------------
# Lifecycle builders shared with the tasks in app/workers (cross-agent
# contract): these two return `(subject, html)` instead of just the HTML.
# ---------------------------------------------------------------------------

def build_winback_email_html(agency_name: str, stats: dict) -> tuple[str, str]:
    """Win-back for an agency that cancelled. Returns `(subject, html)`.

    `stats` is read defensively — `leads`, `conversations` and `visits` are
    the counters the sender is expected to provide; anything missing renders
    as 0 rather than exploding a background task.
    """

    def _n(key: str) -> int:
        try:
            return int(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    leads, conversations, visits = _n("leads"), _n("conversations"), _n("visits")
    subject = f"{agency_name}: tus datos siguen aquí cuando quieras volver"
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{agency_name}</strong> canceló selai y no vamos a insistir. Solo
  queremos que sepas dos cosas.
</p>
<p style="margin:0 0 16px;line-height:1.5;">
  La primera, lo que tu agente llegó a hacer:
</p>
<table style="border-collapse:separate;border-spacing:8px;margin:0 0 20px;width:100%;">
  <tr>
    {_stat_cell(leads, "leads captados")}
    {_stat_cell(conversations, "conversaciones")}
    {_stat_cell(visits, "visitas agendadas")}
  </tr>
</table>
<p style="margin:0 0 16px;line-height:1.5;">
  La segunda, que tus propiedades, conversaciones y leads siguen guardados.
  Si vuelves, el agente retoma donde lo dejó; no hay que volver a
  configurarlo.
</p>
<p style="margin:0 0 24px;">{_button(BILLING_URL, "Volver a activar mi plan")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  Y si te fuiste por algo concreto, respóndenos y cuéntanoslo: nos sirve más
  que cualquier oferta.
</p>"""
    return subject, _layout("Tus datos te esperan", body)


# Human labels for the resources the plan limits. Unknown keys fall back to
# the raw name — an alert with an odd label still beats no alert.
RESOURCE_LABELS: dict[str, str] = {
    "properties": "propiedades",
    "agents": "usuarios",
    "voice_minutes": "minutos de voz",
    "leads": "leads",
    "conversations": "conversaciones",
}


def build_resource_usage_alert_email_html(
    agency_name: str,
    resource: str,
    used: int,
    limit: int,
) -> tuple[str, str]:
    """Plan resource close to (or past) its limit. Returns `(subject, html)`."""
    label = RESOURCE_LABELS.get(resource, resource)
    pct = int(used / limit * 100) if limit > 0 else 100
    if used >= limit > 0:
        lead_in = (
            f"has llegado al límite de <strong>{label}</strong> de tu plan "
            f"(<strong>{used} de {limit}</strong>)."
        )
    else:
        lead_in = (
            f"llevas <strong>{used} de {limit} {label}</strong> de tu plan "
            f"({pct}%)."
        )
    subject = f"Estás cerca del límite de {label} de tu plan"
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{agency_name}</strong>, {lead_in}
</p>
<p style="margin:0 0 16px;line-height:1.5;">
  Cuando se agota, selai deja de aceptar {label} nuevas hasta que amplíes el
  plan; lo que ya tienes sigue funcionando igual.
</p>
<p style="margin:0 0 24px;">{_button(BILLING_URL, "Ver mi plan")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  ¿No tienes claro qué plan te encaja? Responde a este email y lo miramos contigo.
</p>"""
    return subject, _layout(f"Límite de {label} casi agotado", body)


def build_first_week_email_html(
    agency_name: str,
    conversations: int,
    leads: int,
    visits: int,
) -> str:
    """(64) Sent once, 7 days after the agency's first subscription."""
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  <strong>{agency_name}</strong> lleva una semana con selai. Esto es lo que tu
  agente de IA ha conseguido en estos 7 días:
</p>
<table style="border-collapse:separate;border-spacing:8px;margin:0 0 20px;width:100%;">
  <tr>
    {_stat_cell(conversations, "conversaciones")}
    {_stat_cell(leads, "leads nuevos")}
    {_stat_cell(visits, "visitas agendadas")}
  </tr>
</table>
<p style="margin:0 0 24px;">{_button("https://www.selai.es", "Ver mi dashboard")}</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  ¿Quieres sacarle más partido? Responde a este email y te contamos cómo.
</p>"""
    return _layout("Tu primera semana con selai", body)


ROLE_LABELS: dict[str, str] = {
    "owner": "responsable (puede invitar y gestionar el plan)",
    "agent": "comercial",
    "admin": "administrador",
}


def invitation_url(token: str) -> str:
    """The link that lands in the invitee's inbox. One place, because the
    panel route (`/invitacion/[token]`) and this string have to agree."""
    return f"{INVITATION_URL}/{token}"


def build_invitation_email_html(
    agency_name: str,
    inviter_name: str | None,
    token: str,
    *,
    role: str = "agent",
    expires_days: int = 7,
) -> str:
    """Team invitation. The recipient is a stranger to us, so it says who is
    inviting them, to what, and that the link dies — an unexplained link from
    an unknown brand is indistinguishable from phishing and gets deleted.

    Everything interpolated here is typed by a customer (agency name, the
    inviter's own name), so it is escaped: this is the one product email whose
    audience is outside the account.
    """
    agency = _escape(agency_name or "una agencia")
    inviter = _escape((inviter_name or "").strip())
    who = f"<strong>{inviter}</strong> te ha invitado" if inviter else "Te han invitado"
    role_label = _escape(ROLE_LABELS.get(role, ROLE_LABELS["agent"]))
    body = f"""\
<p style="margin:0 0 16px;line-height:1.5;">
  {who} a unirte al equipo de <strong>{agency}</strong> en selai, la
  herramienta con la que atienden WhatsApp, email y llamadas con un agente
  de IA.
</p>
<p style="margin:0 0 16px;line-height:1.5;">
  Entrarás como <strong>{role_label}</strong>: verás las conversaciones, los
  leads y las visitas de {agency}.
</p>
<p style="margin:0 0 24px;">{_button(invitation_url(token), "Aceptar la invitación")}</p>
<p style="margin:0 0 16px;line-height:1.5;color:#5a5f73;font-size:13px;">
  Crea tu cuenta con <strong>este mismo email</strong>; si usas otro no
  podremos darte acceso a {agency}.
</p>
<p style="margin:0;line-height:1.5;color:#5a5f73;font-size:13px;">
  El enlace caduca en {expires_days} días. Si no esperabas esta invitación,
  puedes ignorar este email.
</p>"""
    return _layout(f"Únete al equipo de {agency} en selai", body)

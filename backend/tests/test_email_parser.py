"""Sanity tests for the inbound email parser."""
from app.channels.email.parser import parse_inbound_email, strip_quoted_reply


def test_parses_postmark_style():
    payload = {
        "From": "Juan Pérez <juan@example.com>",
        "To": "piloto-leads@inbound.realtor.ai",
        "Subject": "Consulta sobre el piso de Malasaña",
        "MessageID": "<abc@example.com>",
        "InReplyTo": "",
        "References": "",
        "TextBody": "Hola, me interesa el piso de la calle San Bernardo. ¿Sigue disponible?",
    }
    p = parse_inbound_email(payload)
    assert p is not None
    assert p.identity.email == "juan@example.com"
    assert p.identity.display_name == "Juan Pérez"
    assert "Consulta sobre el piso de Malasaña" in p.text
    assert "San Bernardo" in p.text


def test_strip_quoted_reply_spanish():
    body = (
        "Genial, me interesa visitarlo el sábado.\n\n"
        "El 23 de abril de 2026, 12:34, agente@piloto.test escribió:\n"
        "> Hola, te paso información del piso..."
    )
    assert strip_quoted_reply(body).startswith("Genial")
    assert "agente@piloto.test" not in strip_quoted_reply(body)


def test_returns_none_when_no_from():
    assert parse_inbound_email({"To": "a@b.com", "TextBody": "x"}) is None


# ---------------------------------------------------------------------------
# Resend Inbound envelope ({"type": "email.received", "data": {...}})
# ---------------------------------------------------------------------------


def test_parses_resend_inbound_shape():
    payload = {
        "type": "email.received",
        "created_at": "2026-07-25T09:00:00.000Z",
        "data": {
            "from": "Juan Pérez <juan@example.com>",
            "to": ["inmo-sol-leads@leads.selai.es"],
            "subject": "Consulta sobre el piso de Malasaña",
            "text": "Hola, ¿sigue disponible el piso de la calle Pez?",
            "headers": {
                "Message-ID": "<abc@resend.example>",
                "In-Reply-To": "",
                "References": "",
            },
        },
    }
    p = parse_inbound_email(payload)
    assert p is not None
    assert p.identity.email == "juan@example.com"
    assert p.identity.display_name == "Juan Pérez"
    assert "Consulta sobre el piso de Malasaña" in p.text
    assert "calle Pez" in p.text
    assert p.external_message_id == "<abc@resend.example>"
    assert p.raw["to"] == "inmo-sol-leads@leads.selai.es"
    assert p.raw["subject"] == "Consulta sobre el piso de Malasaña"


def test_parses_resend_inbound_with_header_list_and_threading():
    payload = {
        "type": "email.received",
        "data": {
            "from": "juan@example.com",
            "to": ["inmo-sol-leads@leads.selai.es", "otra@leads.selai.es"],
            "subject": "Re: Visita",
            "text": "Perfecto, el sábado me va bien.",
            "headers": [
                {"name": "Message-ID", "value": "<msg-2@resend.example>"},
                {"name": "In-Reply-To", "value": "<msg-1@resend.example>"},
                {"name": "References", "value": "<msg-1@resend.example>"},
            ],
        },
    }
    p = parse_inbound_email(payload)
    assert p is not None
    assert p.raw["to"] == "inmo-sol-leads@leads.selai.es"  # first recipient wins
    assert p.external_message_id == "<msg-2@resend.example>"
    # Threads by In-Reply-To, exactly like the common shape.
    assert p.identity.external_thread_id == "<msg-1@resend.example>"


def test_resend_inbound_without_from_returns_none():
    assert (
        parse_inbound_email({"type": "email.received", "data": {"to": ["a@b.com"]}})
        is None
    )

"""Sanity tests for the WhatsApp webhook parser."""
from app.channels.whatsapp.parser import parse_whatsapp_webhook, to_e164

SAMPLE = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WABA_ID",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "34600000000",
                            "phone_number_id": "PHONE_ID",
                        },
                        "contacts": [
                            {"profile": {"name": "Juan"}, "wa_id": "34600111222"}
                        ],
                        "messages": [
                            {
                                "from": "34600111222",
                                "id": "wamid.ABC",
                                "timestamp": "1700000000",
                                "type": "text",
                                "text": {"body": "Hola, busco piso en Malasaña"},
                            }
                        ],
                    },
                }
            ],
        }
    ],
}


def test_parses_single_text_message():
    payloads = parse_whatsapp_webhook(SAMPLE)
    assert len(payloads) == 1
    p = payloads[0]
    assert p.text == "Hola, busco piso en Malasaña"
    assert p.identity.whatsapp_wa_id == "34600111222"
    assert p.identity.display_name == "Juan"
    assert p.external_message_id == "wamid.ABC"
    assert p.raw["phone_number_id"] == "PHONE_ID"


def test_to_e164_spanish_mobile():
    assert to_e164("34600111222") == "+34600111222"


def test_to_e164_invalid():
    assert to_e164("notaphone") is None


def test_ignores_status_events():
    status_payload = {
        "entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "delivered"}]}}]}]
    }
    assert parse_whatsapp_webhook(status_payload) == []


def test_survives_payloads_shaped_wrong():
    """A validly signed payload is not necessarily a well-formed one: the
    signature only proves *someone with the secret* sent it, and an agency
    holds its own. A surprise shape must skip the message, not raise — Meta
    treats a 500 as "retry forever"."""
    for hostile in (
        {"entry": "not-a-list"},
        {"entry": ["not-a-dict"]},
        {"entry": [{"changes": "nope"}]},
        {"entry": [{"changes": ["nope"]}]},
        {"entry": [{"changes": [{"value": "nope"}]}]},
        {"entry": [{"changes": [{"value": {"messages": "nope"}}]}]},
        {"entry": [{"changes": [{"value": {"messages": ["nope"]}}]}]},
        {"entry": [{"changes": [{"value": {"messages": [{}], "metadata": "nope"}}]}]},
        {"entry": [{"changes": [{"value": {"messages": [{}], "contacts": ["x"]}}]}]},
    ):
        assert isinstance(parse_whatsapp_webhook(hostile), list)


def test_contact_with_a_broken_profile_still_yields_the_message():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "PHONE_ID"},
                            "contacts": [{"wa_id": "34600111222", "profile": "nope"}],
                            "messages": [
                                {
                                    "from": "34600111222",
                                    "id": "wamid.X",
                                    "type": "text",
                                    "text": {"body": "Hola"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    parsed = parse_whatsapp_webhook(payload)
    assert len(parsed) == 1
    assert parsed[0].identity.display_name is None
    assert parsed[0].text == "Hola"

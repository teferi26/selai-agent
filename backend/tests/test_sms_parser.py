"""Twilio SMS payload normalization."""
from app.channels.sms.parser import parse_twilio_sms


def test_parses_basic_sms():
    p = parse_twilio_sms({
        "From": "+34612345678",
        "To": "+34911234567",
        "Body": "Hola, me interesa el piso",
        "MessageSid": "SM123",
    })
    assert p is not None
    assert p.identity.phone_e164 == "+34612345678"
    assert p.text == "Hola, me interesa el piso"
    assert p.external_message_id == "SM123"


def test_returns_none_when_empty_body():
    assert parse_twilio_sms({"From": "+34612345678", "Body": ""}) is None


def test_returns_none_when_no_from():
    assert parse_twilio_sms({"Body": "hi"}) is None

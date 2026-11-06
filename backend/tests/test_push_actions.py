from app.services.push import LEAD_ACTIONS_CATEGORY, _message_payload


def test_valid_e164_enables_native_lead_actions():
    payload = _message_payload(
        "ExponentPushToken[test]",
        "Lead caliente",
        "Llámale ahora",
        {"conversation_id": "conv", "phone": "+34600111222"},
    )

    assert payload["categoryId"] == LEAD_ACTIONS_CATEGORY
    assert payload["data"]["phone"] == "+34600111222"


def test_missing_or_invalid_phone_does_not_offer_call_action():
    missing = _message_payload("token", "Aviso", "Abre la conversación", None)
    invalid = _message_payload(
        "token", "Aviso", "Abre la conversación", {"phone": "tel:+34600111222"}
    )

    assert "categoryId" not in missing
    assert "categoryId" not in invalid

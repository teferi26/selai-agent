"""Tests for the PII redaction module."""
from __future__ import annotations

from app.services.pii import redact, redact_dict


class TestRedactEmail:
    def test_basic_email(self):
        assert redact("Mi correo es juan.perez@mail.example.com") == "Mi correo es [EMAIL]"

    def test_email_in_sentence(self):
        out = redact("escríbeme a foo@bar.es por favor")
        assert "foo@bar.es" not in out
        assert "[EMAIL]" in out

    def test_multiple_emails(self):
        out = redact("a@x.com y b@y.com")
        assert out.count("[EMAIL]") == 2


class TestRedactPhone:
    def test_spanish_mobile_with_prefix(self):
        out = redact("Llámame al +34 666 12 34 56")
        assert "666" not in out
        assert "[PHONE]" in out

    def test_spanish_mobile_without_prefix(self):
        out = redact("mi móvil es 666123456")
        assert "666123456" not in out
        assert "[PHONE]" in out

    def test_landline_900(self):
        out = redact("teléfono 911 22 33 44")
        assert "[PHONE]" in out

    def test_phone_with_dashes(self):
        out = redact("tel: 666-12-34-56")
        assert "666" not in out
        assert "[PHONE]" in out


class TestRedactIban:
    def test_spanish_iban(self):
        # Spain IBAN: ES + 22 digits = 24 chars
        out = redact("transfiere a ES9121000418450200051332 hoy")
        assert "ES9121000418450200051332" not in out
        assert "[IBAN]" in out

    def test_other_iban(self):
        out = redact("DE89370400440532013000 es la cuenta")
        assert "DE89370400440532013000" not in out
        assert "[IBAN]" in out


class TestRedactDni:
    def test_dni(self):
        out = redact("mi DNI es 12345678Z")
        assert "12345678Z" not in out
        assert "[ID]" in out

    def test_nie(self):
        out = redact("NIE: X1234567L")
        assert "X1234567L" not in out
        assert "[ID]" in out


class TestRedactAddress:
    def test_calle_with_number(self):
        out = redact("vivo en Calle Mayor 23, Madrid")
        assert "Calle Mayor 23" not in out
        assert "[ADDR]" in out

    def test_avenida(self):
        out = redact("la oficina está en Avda Diagonal 405")
        assert "Avda Diagonal 405" not in out
        assert "[ADDR]" in out


class TestRedactUrl:
    def test_https(self):
        out = redact("mira esto https://example.com/foo")
        assert "example.com" not in out
        assert "[URL]" in out

    def test_http(self):
        out = redact("ver http://idealista.com/123 hoy")
        assert "idealista.com" not in out
        assert "[URL]" in out


class TestRedactNames:
    def test_two_word_name_is_redacted(self):
        out = redact("Hola, soy Juan Pérez del banco")
        assert "Juan Pérez" not in out
        assert "[NAME]" in out

    def test_city_name_is_preserved(self):
        out = redact("vivo en Madrid")
        assert "Madrid" in out

    def test_brand_name_is_preserved(self):
        out = redact("contesto por WhatsApp normalmente")
        assert "WhatsApp" in out

    def test_allowed_first_name_kept(self):
        out = redact("Hola María", allow_first_name_of="María García")
        # Single-word "María" wouldn't match anyway, but confirm allow plumbed
        assert "María" in out

    def test_allowed_first_name_in_two_word_pattern(self):
        # Even when "María Casas" appears, María is allowed → keep
        out = redact("María Casas vendrá mañana", allow_first_name_of="María García")
        # "Casas" alone is fine, "María" is allowed; pair shouldn't be redacted
        assert "María" in out

    def test_single_word_at_sentence_start_not_redacted(self):
        # We don't redact single capitalised words to avoid false positives.
        out = redact("Hola tal")
        assert out == "Hola tal"


class TestRedactCombined:
    def test_email_phone_iban_address(self):
        text = (
            "Llámame al 666123456 o escríbeme a foo@bar.es. "
            "Mi cuenta es ES9121000418450200051332 y vivo en Calle Sol 14."
        )
        out = redact(text)
        for original in ("666123456", "foo@bar.es", "ES9121000418450200051332", "Calle Sol 14"):
            assert original not in out
        for tag in ("[PHONE]", "[EMAIL]", "[IBAN]", "[ADDR]"):
            assert tag in out

    def test_empty_input(self):
        assert redact("") == ""
        assert redact(None) == ""

    def test_no_pii_unchanged(self):
        text = "busco un piso de 2 habitaciones por 350 mil"
        assert redact(text) == text


class TestRedactDict:
    def test_nested(self):
        d = {
            "name": "Juan Pérez",
            "contact": {"email": "j@p.es", "phone": "666123456"},
            "tags": ["lead", "VIP"],
        }
        out = redact_dict(d)
        assert "[EMAIL]" in out["contact"]["email"]
        assert "[PHONE]" in out["contact"]["phone"]
        assert "[NAME]" in out["name"]
        assert out["tags"] == ["lead", "VIP"]  # single caps preserved

    def test_non_string_leaves_untouched(self):
        d = {"count": 5, "active": True, "amounts": [1, 2, 3]}
        assert redact_dict(d) == d

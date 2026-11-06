"""Foreign-buyer detection."""
from app.services.foreign_buyer import is_foreign_buyer_signal


def test_english_text_triggers():
    assert is_foreign_buyer_signal("I'm looking for a flat in Madrid", "en") is True


def test_spanish_text_does_not_trigger():
    assert is_foreign_buyer_signal("Busco piso en Madrid", "es") is False


def test_spanish_text_with_explicit_signal_triggers():
    assert (
        is_foreign_buyer_signal("Vivo en el extranjero, quiero comprar en Madrid", "es")
        is True
    )


def test_french_language_triggers():
    assert is_foreign_buyer_signal("Bonjour, je cherche un appartement", "fr") is True

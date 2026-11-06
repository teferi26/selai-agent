"""Detección de idioma."""
from app.services.language import detect_language


def test_detects_spanish():
    assert detect_language("Hola, busco un piso en Malasaña con dos habitaciones") == "es"


def test_detects_english():
    assert detect_language("Hi, I am looking for a two bedroom flat in Malasaña") == "en"


def test_detects_french():
    assert detect_language("Bonjour, je cherche un appartement à Madrid") == "fr"


def test_short_text_falls_back():
    assert detect_language("Hi", fallback="es") == "es"


def test_unsupported_falls_back():
    # Mostly Hangul → not in our supported list, fallback applied.
    assert detect_language("안녕하세요 안녕하세요 안녕하세요", fallback="es") == "es"

"""Sanity tests for the heuristic part of the competitor detector."""
from app.services.competitor_detector import quick_heuristic_score


def test_normal_buyer_message_scores_zero():
    msgs = [
        "Hola, ¿está disponible el piso de Malasaña?",
        "Me gustaría visitarlo el sábado",
    ]
    assert quick_heuristic_score(msgs) == 0


def test_competitor_recon_lights_up():
    msgs = [
        "¿Qué comisión cobráis a propietarios?",
        "¿Qué método de captación usáis?",
        "¿Cuántos pisos lleváis en cartera?",
    ]
    assert quick_heuristic_score(msgs) >= 3


def test_partial_match_still_counts():
    assert quick_heuristic_score(["coste por lead que tenéis?"]) == 1

"""Lightweight language detection.

Used to set `lead.language` from the first message and to give the agent
runner a hint. We keep it deterministic (seeded) and fall back to "es" so
behaviour is predictable for tests.
"""
from __future__ import annotations

from langdetect import DetectorFactory, LangDetectException, detect

from app.core.logging import get_logger

DetectorFactory.seed = 0
log = get_logger(__name__)

_SUPPORTED = {"es", "en", "fr", "ca", "pt", "de", "it"}


def detect_language(text: str, fallback: str = "es") -> str:
    text = (text or "").strip()
    if len(text) < 4:
        return fallback
    try:
        code = detect(text).split("-")[0].lower()
    except LangDetectException:
        return fallback
    return code if code in _SUPPORTED else fallback

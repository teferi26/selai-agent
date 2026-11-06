"""Render the agent's reply into something an email client shows with dignity.

The model writes light markdown — `**bold**`, `- ` bullets, `---` separators —
and the reply used to go out as a text-only email. Every client showed the
asterisks literally: the very first email a lead receives from the agency
looked like a robot's scratchpad. (Reported by the owner from a real mailbox.)

So each reply becomes two parts:

- `html`: minimal, personal-looking markup. Deliberately NOT the branded
  template from `services/emails.py` — this is a person-to-person
  conversation, and a marketing frame around it would scream "automated".
- `text`: the same content with the markdown markers stripped, for clients
  that prefer plain text.

No markdown library: the input language is three constructs wide, and a
dependency that renders arbitrary markdown is a bigger attack surface than
three regexes (same reasoning as the hand-rolled Svix verifier). Everything is
HTML-escaped BEFORE any markup is added, so lead-controlled text (their name,
their message quoted back) can never smuggle tags in.
"""
from __future__ import annotations

import html as _html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_SEPARATOR = re.compile(r"^\s*(?:-{3,}|_{3,}|\*{3,})\s*$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$")


def _strip_markers(text: str) -> str:
    """Plain-text part: same words, no markup noise."""
    lines: list[str] = []
    for line in text.splitlines():
        if _SEPARATOR.match(line):
            # A separator says "topic change"; in plain text a blank line
            # already says that.
            lines.append("")
            continue
        line = _HEADING.sub(r"\1", line)
        line = _BOLD.sub(r"\1", line)
        lines.append(line)
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _inline_html(line: str) -> str:
    """Escape first, then the one inline construct we honour."""
    return _BOLD.sub(r"<strong>\1</strong>", _html.escape(line, quote=False))


def email_parts(text: str) -> tuple[str, str]:
    """(plain_text, html) for a conversational reply."""
    plain = _strip_markers(text)

    blocks: list[str] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(
                "<p style=\"margin:0 0 1em 0;\">" + "<br>".join(paragraph) + "</p>"
            )
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            items = "".join(f"<li style=\"margin:0 0 .35em 0;\">{b}</li>" for b in bullets)
            blocks.append(f"<ul style=\"margin:0 0 1em 1.2em;padding:0;\">{items}</ul>")
            bullets.clear()

    for raw_line in text.splitlines():
        if _SEPARATOR.match(raw_line):
            flush_paragraph()
            flush_bullets()
            continue
        bullet = _BULLET.match(raw_line)
        if bullet:
            flush_paragraph()
            bullets.append(_inline_html(bullet.group(1)))
            continue
        flush_bullets()
        heading = _HEADING.match(raw_line)
        if heading:
            flush_paragraph()
            paragraph.append(f"<strong>{_inline_html(heading.group(1))}</strong>")
            flush_paragraph()
            continue
        if not raw_line.strip():
            flush_paragraph()
            continue
        paragraph.append(_inline_html(raw_line))
    flush_paragraph()
    flush_bullets()

    body = "\n".join(blocks)
    html = (
        "<div style=\"font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,"
        "sans-serif;font-size:15px;line-height:1.55;color:#1f2937;max-width:640px;\">"
        f"{body}</div>"
    )
    return plain, html

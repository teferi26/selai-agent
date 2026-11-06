"""El primer email que un lead recibe de la agencia no puede parecer un
borrador de robot.

El modelo escribe markdown ligero y el correo salía solo como texto: todos los
clientes de correo enseñaban los asteriscos y los `---` tal cual (reportado
por el dueño desde un buzón real). `email_parts` produce las dos partes:
texto limpio y HTML mínimo con aspecto de correo personal.
"""
from __future__ import annotations

from app.channels.email.formatting import email_parts

MUESTRA = """¡Hola Lucía! Gracias por tu interés.

**Para tu consulta (ático con terraza):**

Un par de preguntas rápidas:
- ¿Prefieres Madrid o Valencia?
- ¿Cuántas habitaciones mínimo?

---

Un cordial saludo,
**Asistente Virtual de Inmobiliaria Demo**"""


def test_plain_text_loses_every_marker():
    plain, _ = email_parts(MUESTRA)
    assert "**" not in plain
    assert "---" not in plain
    assert "Para tu consulta (ático con terraza):" in plain
    assert "- ¿Prefieres Madrid o Valencia?" in plain  # las viñetas se leen bien en texto


def test_html_renders_bold_bullets_and_paragraphs():
    _, html = email_parts(MUESTRA)
    assert "<strong>Para tu consulta (ático con terraza):</strong>" in html
    assert "<li" in html and "¿Prefieres Madrid o Valencia?" in html
    assert "<p" in html
    assert "**" not in html
    assert "---" not in html


def test_lead_controlled_text_cannot_smuggle_html():
    """El modelo cita al lead; el lead puede escribir etiquetas."""
    plain, html = email_parts(
        'Me dijiste: <script>alert("x")</script> y **<img src=x onerror=y>**'
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img" not in html
    # El strong se aplica sobre el contenido YA escapado.
    assert "<strong>&lt;img src=x onerror=y&gt;</strong>" in html
    assert "<script>" in plain  # el texto plano no interpreta nada: va literal


def test_headings_become_bold_lines():
    _, html = email_parts("## Resumen de tu visita\nTodo confirmado.")
    assert "<strong>Resumen de tu visita</strong>" in html
    plain, _ = email_parts("## Resumen de tu visita")
    assert plain == "Resumen de tu visita"


def test_plain_input_stays_plain():
    plain, html = email_parts("Hola, ¿sigue disponible el piso?\nGracias.")
    assert plain == "Hola, ¿sigue disponible el piso?\nGracias."
    assert "¿sigue disponible el piso?<br>Gracias." in html


def test_triple_newlines_collapse_in_plain():
    plain, _ = email_parts("Hola.\n\n\n\n¿Sigue disponible?")
    assert plain == "Hola.\n\n¿Sigue disponible?"

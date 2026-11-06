"""Constants the panel and the backend must agree on, checked against each other.

Every bug this file guards against looks the same from the outside: both sides
compile, both sides pass their own tests, the API returns 200 — and the product
does something different from what the screen says. The weekday scale was
exactly that (the panel said 1 = lunes, the backend read 0 = lunes, and the GET
handed the raw number back so the panel repainted its own version of the truth).
A type checker cannot catch it: `days: number[]` is honest on both sides.

So this test reads the TypeScript source as text and compares the shared
constants with the Python ones. It is deliberately in the backend suite: the
backend is the side that decides, so it is the side that must complain.

If a test here fails, do not "fix" it by editing the expectation. One of the two
layers moved and the other did not; the message tells you which.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent.tools import describe_business_hours
from app.api.agency_settings import AgencySettingsPatch, BusinessHours, _serialize
from app.services.conversation_service import DEFAULT_BUSINESS_HOURS, is_within_business_hours
from app.services.event_dispatcher import SUPPORTED_EVENTS, TEST_EVENT

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_SRC = REPO_ROOT / "dashboard" / "src"
MOBILE_SRC = REPO_ROOT / "mobile" / "src"
BACKEND_APP = REPO_ROOT / "backend" / "app"

IA_CLIENT_TSX = DASHBOARD_SRC / "app" / "(app)" / "settings" / "ia" / "IAClient.tsx"
# The phone grew its own copy of the weekday scale when the AI settings screen
# landed there. A second copy is a second chance to drift, and the drift is
# invisible: `days: number[]` type-checks either way, the GET echoes back what
# it was sent, and the only symptom is an agency answered on the wrong day.
MOBILE_AI_SETTINGS_TS = MOBILE_SRC / "lib" / "ai-settings.ts"
AGENCY_SETTINGS_TS = DASHBOARD_SRC / "lib" / "agency-settings.ts"
SETUP_STEPS_TS = DASHBOARD_SRC / "lib" / "setup-steps.ts"
CHANNELS_TS = DASHBOARD_SRC / "lib" / "channels.ts"


def _read(path: Path) -> str:
    if not path.exists():
        # Skipping would silently retire the guard, which is the failure mode
        # this whole file exists to prevent. This is a monorepo: the file is
        # either there or somebody moved it and owes this test an update.
        pytest.fail(
            f"No se encuentra {path.relative_to(REPO_ROOT)}.\n"
            "Este test compara constantes del panel con las del backend. Si el "
            "fichero se ha movido o renombrado, actualiza la ruta aquí; no "
            "borres la comprobación."
        )
    return path.read_text(encoding="utf-8")


def _block(source: str, name: str, opener: str, closer: str, where: Path) -> str:
    """The literal body of `const <name> ... = <opener> ... <closer>`."""
    match = re.search(
        rf"const {name}\b[^=]*=\s*{re.escape(opener)}(.*?){re.escape(closer)}",
        source,
        re.S,
    )
    if not match:
        pytest.fail(
            f"No se ha podido leer la constante {name} en "
            f"{where.relative_to(REPO_ROOT)}.\n"
            "El test la parsea del fuente para compararla con el backend: si has "
            "cambiado su forma, ajusta el parseo, porque sin él la comprobación "
            "no existe."
        )
    return match.group(1)


# ---------------------------------------------------------------------------
# 1. Weekday scale: panel DAYS  <->  datetime.weekday()
# ---------------------------------------------------------------------------

# Names as the panel writes them, indexed by Python weekday.
SPANISH_WEEKDAYS = [
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
]

# 2026-07-20 is a Monday, so MONDAY + timedelta(days=n) is the day Python
# numbers `n`. Lets the test check meaning, not just the numbers.
MONDAY = datetime(2026, 7, 20, 12, 0)

_DAY_ENTRY = re.compile(
    r"\{\s*value:\s*(-?\d+)\s*,\s*label:\s*\"([^\"]*)\"\s*,\s*name:\s*\"([^\"]*)\"\s*\}"
)


def _panel_days() -> list[tuple[int, str, str]]:
    body = _block(_read(IA_CLIENT_TSX), "DAYS", "[", "];", IA_CLIENT_TSX)
    entries = [(int(v), label, name) for v, label, name in _DAY_ENTRY.findall(body)]
    if not entries:
        pytest.fail(
            f"El array DAYS de {IA_CLIENT_TSX.relative_to(REPO_ROOT)} no se ha "
            "podido parsear. Se espera `{ value, label, name }` por día."
        )
    return entries


def _panel_default_hour_days() -> list[int]:
    body = _block(_read(IA_CLIENT_TSX), "DEFAULT_HOURS", "{", "};", IA_CLIENT_TSX)
    match = re.search(r"days:\s*\[([^\]]*)\]", body)
    if not match:
        pytest.fail("DEFAULT_HOURS de IAClient.tsx no declara `days`.")
    return [int(n) for n in re.findall(r"-?\d+", match.group(1))]


def test_panel_weekday_values_are_python_weekdays():
    values = [value for value, _, _ in _panel_days()]
    expected = list(range(7))
    assert values == expected, (
        "El panel y el backend no numeran los días igual.\n"
        f"  panel (IAClient.tsx DAYS): {values}\n"
        f"  backend (datetime.weekday(), 0 = lunes): {expected}\n"
        "El backend valida 0-6 y evalúa con datetime.weekday(); si el panel usa "
        "otra escala, el horario se guarda sin error y se aplica en días "
        "distintos a los que el usuario ha marcado, y la UI no lo enseña."
    )


def test_panel_weekday_names_match_the_day_the_backend_understands():
    for value, _, name in _panel_days():
        real_day = MONDAY + timedelta(days=value)
        assert name == SPANISH_WEEKDAYS[value], (
            f"El panel llama «{name}» al día {value}, pero el backend trata el "
            f"día {value} como {SPANISH_WEEKDAYS[value]}. Un usuario que marque "
            f"«{name}» sería atendido en {SPANISH_WEEKDAYS[value]}."
        )
        # Y de verdad: el evaluador abre ese día y no otro.
        only_this_day = {"start": "09:00", "end": "19:00", "days": [value]}
        assert is_within_business_hours(real_day, only_this_day) is True, (
            f"Marcando solo «{name}» (valor {value}) el backend NO considera "
            f"abierto el {name}."
        )
        assert is_within_business_hours(real_day + timedelta(days=1), only_this_day) is False


_MOBILE_DAY_ENTRY = re.compile(
    r"\{\s*value:\s*(-?\d+)\s*,\s*short:\s*\"([^\"]*)\"\s*,\s*name:\s*\"([^\"]*)\"\s*\}"
)


def _mobile_days() -> list[tuple[int, str, str]]:
    body = _block(_read(MOBILE_AI_SETTINGS_TS), "DAYS", "[", "];", MOBILE_AI_SETTINGS_TS)
    entries = [(int(v), short, name) for v, short, name in _MOBILE_DAY_ENTRY.findall(body)]
    if not entries:
        pytest.fail(
            f"El array DAYS de {MOBILE_AI_SETTINGS_TS.relative_to(REPO_ROOT)} no se "
            "ha podido parsear. Se espera `{ value, short, name }` por día."
        )
    return entries


def test_mobile_weekday_scale_matches_the_backend_and_the_panel():
    """La app tiene su propia copia de la escala; las tres tienen que decir lo
    mismo o una agencia queda atendida el día que no era."""
    mobile = [(value, name) for value, _, name in _mobile_days()]
    panel = [(value, name) for value, _, name in _panel_days()]
    expected = list(enumerate(SPANISH_WEEKDAYS))

    assert mobile == expected, (
        "La app móvil no numera los días como el backend.\n"
        f"  app (mobile/src/lib/ai-settings.ts DAYS): {mobile}\n"
        f"  backend (datetime.weekday(), 0 = lunes): {expected}\n"
        "Se guarda sin error y se aplica en días distintos a los marcados."
    )
    assert mobile == panel, (
        "El panel y la app no coinciden entre sí:\n"
        f"  app:   {mobile}\n"
        f"  panel: {panel}\n"
        "El mismo horario se vería distinto en cada superficie."
    )


def test_mobile_default_hours_match_the_backend_default():
    body = _block(_read(MOBILE_AI_SETTINGS_TS), "DEFAULT_HOURS", "{", "};", MOBILE_AI_SETTINGS_TS)
    match = re.search(r"days:\s*\[([^\]]*)\]", body)
    if not match:
        pytest.fail("DEFAULT_HOURS de mobile/src/lib/ai-settings.ts no declara `days`.")
    mobile = [int(n) for n in re.findall(r"-?\d+", match.group(1))]
    backend = list(DEFAULT_BUSINESS_HOURS["days"])
    assert mobile == backend, (
        "El horario por defecto de la app no es el del backend.\n"
        f"  app:     {mobile}\n"
        f"  backend: {backend}"
    )


def test_panel_default_hours_match_the_backend_default():
    panel = _panel_default_hour_days()
    backend = list(DEFAULT_BUSINESS_HOURS["days"])
    assert panel == backend, (
        "El horario por defecto del panel no es el del backend.\n"
        f"  panel (IAClient.tsx DEFAULT_HOURS.days): {panel}\n"
        f"  backend (conversation_service.DEFAULT_BUSINESS_HOURS): {backend}\n"
        "Los dos deben significar lunes a viernes. Si no coinciden, una agencia "
        "que no toque nada tiene un horario distinto según quién lo mire."
    )


def test_every_panel_day_is_accepted_by_the_api():
    """Ningún día que el panel pueda enviar puede dar 422."""
    for value, _, name in _panel_days():
        try:
            BusinessHours(start="09:00", end="19:00", days=[value])
        except ValidationError as exc:
            pytest.fail(
                f"El panel ofrece «{name}» (valor {value}) pero la API lo "
                f"rechaza: {exc}. El usuario marca el día, ve un error genérico "
                "y no puede configurar su horario."
            )


def test_sunday_no_longer_returns_422():
    """El caso concreto que estaba roto: Domingo era el 7 y la API lo rechazaba."""
    sunday = [value for value, _, name in _panel_days() if name == "Domingo"]
    assert sunday == [6], f"Domingo debería ser el 6 en el panel, es {sunday}."
    validated = BusinessHours(start="09:00", end="19:00", days=sunday)
    assert validated.days == [6]


def test_marking_only_sunday_survives_the_whole_round_trip():
    """No basta con que el validador acepte el 6: el cuerpo del PATCH, lo que
    se guarda en el blob y lo que devuelve el GET tienen que seguir diciendo
    domingo, y el evaluador tiene que abrir en domingo y solo en domingo.
    Un 422 que desaparece pero deja el dato a medio camino es el mismo fallo
    con otra cara."""
    sunday = [value for value, _, name in _panel_days() if name == "Domingo"]

    # 1. El cuerpo que manda el panel.
    patch = AgencySettingsPatch(
        business_hours={"start": "10:00", "end": "14:00", "days": sunday}
    )
    provided = patch.model_dump(exclude_unset=True)
    stored = provided["business_hours"]
    assert stored == {"start": "10:00", "end": "14:00", "days": [6]}

    # 2. Lo que el GET repinta en el panel: el mismo número, sin traducir.
    view = _serialize(
        SimpleNamespace(
            name="Inmo",
            slug="inmo",
            timezone="Europe/Madrid",
            default_language="es",
            settings={"business_hours": stored},
        )
    )
    assert view.settings.business_hours == stored

    # 3. Y significa domingo de verdad.
    sunday_noon = MONDAY + timedelta(days=6, hours=-1)  # domingo a las 11:00
    assert is_within_business_hours(sunday_noon, stored) is True
    assert is_within_business_hours(sunday_noon - timedelta(days=1), stored) is False


def test_agent_speaks_the_same_weekday_names_as_the_panel():
    """`describe_business_hours` es lo que el agente le dice al cliente por
    WhatsApp o por teléfono. Indexa WEEKDAYS_ES con el mismo número que guarda
    el panel, así que si las dos listas se separan el agente anuncia un horario
    distinto del que enseña la pantalla — y ahí ya no hay error que ver."""
    for value, _, name in _panel_days():
        spoken = describe_business_hours({"start": "09:00", "end": "19:00", "days": [value]})
        assert name.lower() in spoken, (
            f"El panel llama «{name}» al día {value}, pero el agente lo cuenta "
            f"como «{spoken}». El cliente oiría un día y vería otro."
        )


def test_shared_type_documents_the_backend_convention():
    doc = _read(AGENCY_SETTINGS_TS)
    assert re.search(r"0\s*=\s*lunes", doc), (
        f"{AGENCY_SETTINGS_TS.relative_to(REPO_ROOT)} debe documentar el tipo "
        "BusinessHours como 0 = lunes … 6 = domingo. Es el único sitio donde un "
        "desarrollador del panel lee qué significa `days`."
    )
    assert not re.search(r"days:\s*1\s*=\s*lunes", doc), (
        "El tipo BusinessHours sigue documentando la escala vieja (1 = lunes). "
        "El backend usa 0 = lunes."
    )


# ---------------------------------------------------------------------------
# 2. Setup checklist keys: what the backend ticks <-> what the panel reads
# ---------------------------------------------------------------------------


def _panel_checklist_keys() -> set[str]:
    """Every checklist key the panel knows about: sus pasos y las claves que
    lee o escribe en cualquier pantalla."""
    keys = set(re.findall(r'"([^"]+)"', _block(
        _read(SETUP_STEPS_TS), "SETUP_STEP_KEYS", "[", "] as const;", SETUP_STEPS_TS
    )))
    for path in list(DASHBOARD_SRC.rglob("*.ts")) + list(DASHBOARD_SRC.rglob("*.tsx")):
        source = path.read_text(encoding="utf-8")
        keys.update(re.findall(r'checklist\[\s*"([^"]+)"\s*\]', source))
        for literal in re.findall(r"setup_checklist:\s*\{([^}]*)\}", source):
            keys.update(re.findall(r"(\w+)\s*:", literal))
        for literal in re.findall(r"saveChecklist\(\s*\{([^}]*)\}", source):
            keys.update(re.findall(r"(\w+)\s*:", literal))
    return keys


def _backend_written_checklist_keys() -> set[str]:
    keys: set[str] = set()
    for path in BACKEND_APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        keys.update(re.findall(r'checklist\[\s*"([^"]+)"\s*\]\s*=', source))
    return keys


def test_backend_only_ticks_checklist_keys_the_panel_reads():
    """`setup_checklist` es un dict libre: el backend acepta y guarda cualquier
    clave. Por eso nada impide que marque una casilla que el panel no mira, y el
    usuario vería un paso sin completar que el backend da por hecho."""
    written = _backend_written_checklist_keys()
    known = _panel_checklist_keys()
    orphans = sorted(written - known)
    assert not orphans, (
        "El backend marca claves de setup_checklist que el panel no lee:\n"
        f"  {orphans}\n"
        f"  el backend escribe: {sorted(written)}\n"
        f"  el panel conoce: {sorted(known)}\n"
        "Una clave que el backend marca y el panel ignora es progreso que el "
        "usuario nunca ve: el paso se queda en gris para siempre."
    )


def test_panel_has_setup_steps():
    # Si el parseo deja de encontrar pasos, la comprobación de arriba pasaría
    # trivialmente (un conjunto vacío no tiene huérfanos que detectar).
    assert len(_panel_checklist_keys()) >= 5


def test_backend_ticks_at_least_one_checklist_key():
    # El otro lado del mismo agujero: si el regex deja de encontrar lo que el
    # backend escribe, `written - known` es vacío y el test de arriba pasa sin
    # comprobar nada. Una guarda que no puede fallar es peor que ninguna,
    # porque además tranquiliza.
    written = _backend_written_checklist_keys()
    assert written, (
        "No se ha encontrado ni una sola escritura de setup_checklist en el "
        f"backend ({BACKEND_APP.relative_to(REPO_ROOT)}). O el backend ya no "
        "marca pasos, o el parseo ha dejado de reconocer cómo los marca; en el "
        "segundo caso la comprobación de huérfanos pasa siempre. Ajusta el "
        "parseo, no borres el test."
    )


# ---------------------------------------------------------------------------
# 3. Webhook events: panel WEBHOOK_EVENTS <-> dispatcher SUPPORTED_EVENTS
# ---------------------------------------------------------------------------


def _panel_webhook_events() -> list[str]:
    body = _block(_read(CHANNELS_TS), "WEBHOOK_EVENTS", "[", "];", CHANNELS_TS)
    return re.findall(r'key:\s*"([^"]+)"', body)


def test_webhook_events_match_the_dispatcher():
    panel = set(_panel_webhook_events())
    missing = sorted(SUPPORTED_EVENTS - panel)
    extra = sorted(panel - SUPPORTED_EVENTS)
    assert not missing and not extra, (
        "La lista de eventos del panel y la del dispatcher no coinciden.\n"
        f"  el backend emite pero el panel no ofrece: {missing or 'ninguno'}\n"
        f"  el panel ofrece pero el backend no emite: {extra or 'ninguno'}\n"
        "Lo segundo es lo grave: el CRM se suscribe a un evento, la suscripción "
        "se guarda sin error y no llega nunca nada, porque `emit()` descarta lo "
        "que no está en SUPPORTED_EVENTS.\n"
        f"  panel (channels.ts WEBHOOK_EVENTS): {sorted(panel)}\n"
        f"  backend (event_dispatcher.SUPPORTED_EVENTS): {sorted(SUPPORTED_EVENTS)}"
    )


def test_panel_does_not_offer_the_internal_test_event():
    assert TEST_EVENT not in _panel_webhook_events(), (
        f"«{TEST_EVENT}» es el evento interno de prueba: no se emite por "
        "suscripción, solo se envía a mano al probar un webhook. Ofrecerlo en el "
        "panel promete avisos que no llegarán."
    )


def test_every_panel_event_has_a_label():
    body = _block(_read(CHANNELS_TS), "WEBHOOK_EVENTS", "[", "];", CHANNELS_TS)
    labels = re.findall(r'label:\s*"([^"]+)"', body)
    assert len(labels) == len(_panel_webhook_events())
    assert all(label.strip() for label in labels)


# ---------------------------------------------------------------------------
# La dirección donde la agencia recibe sus leads
# ---------------------------------------------------------------------------

# El dominio lo decide el backend (`settings.email_leads_domain`) y viaja al
# panel dentro de `GET /channels` como `email.address`. Una copia en el panel
# es peligrosa de una forma muy concreta: no falla, no avisa, se copia igual de
# bien con el botón de copiar, y la agencia la pega en Idealista para no
# recibir jamás un lead. El dominio ademas cambia con el plan del proveedor de
# correo entrante, asi que la copia se queda vieja sola.
_HARDCODED_LEADS_ADDRESS = re.compile(r"-leads@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def test_the_panel_does_not_keep_its_own_copy_of_the_leads_address():
    offenders: list[str] = []
    for path in sorted(DASHBOARD_SRC.rglob("*.ts*")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith(("//", "*", "/*")):
                continue  # los comentarios pueden citarla como ejemplo
            if _HARDCODED_LEADS_ADDRESS.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not offenders, (
        "El panel vuelve a construir la dirección de leads por su cuenta.\n"
        "Tiene que venir del backend (`GET /channels` → `email.address`), que es "
        "quien conoce `settings.email_leads_domain`. Una copia local sigue "
        "pareciendo correcta cuando ya no lo es:\n"
        + "\n".join(f"  {line}" for line in offenders)
    )


def test_the_backend_builds_the_address_from_the_setting():
    """El otro lado: que el backend no la tenga escrita a mano tampoco."""
    from app.api.channels import leads_email_for
    from app.core.settings import settings

    original = settings.email_leads_domain
    try:
        settings.email_leads_domain = "ejemplo.test"
        assert leads_email_for("inmo-sol") == "inmo-sol-leads@ejemplo.test"
    finally:
        settings.email_leads_domain = original


# ---------------------------------------------------------------------------
# El anual anunciado y el anual cobrado (n19)
# ---------------------------------------------------------------------------

PRICING_DATA_TS = DASHBOARD_SRC / "components" / "marketing" / "pricing-data.ts"


def test_the_annual_price_on_the_web_is_what_stripe_charges():
    """La web redondeaba primero el mensual (99 → 79 €/mes → 948 €/año) y
    Stripe cobraba los 950,40 reales: 2,40 € de más sobre lo anunciado como
    exacto. La fórmula canónica es la del backend; la web debe replicarla.
    """
    from app.scripts.sync_stripe_plans import annual_amount_cents

    ts = PRICING_DATA_TS.read_text(encoding="utf-8")
    # La web calcula el total como round(mensual * 12 * (1-dto) * 100) / 100 —
    # la misma expresión que annual_amount_cents. Si alguien la cambia, este
    # parseo deja de casar y el test lo señala.
    assert "Math.round(monthly * 12 * (1 - ANNUAL_DISCOUNT) * 100) / 100" in ts, (
        "pricing-data.ts ya no usa la fórmula de Stripe "
        "(sync_stripe_plans.annual_amount_cents). El total anual que enseña "
        "la web tiene que ser el céntimo exacto que Stripe cobra."
    )
    for monthly, esperado_cents in ((99, 95040), (199, 191040), (399, 383040)):
        assert annual_amount_cents(monthly) == esperado_cents
        # Y el equivalente en la web: round(m*12*0.8*100)/100 == cents/100.
        assert round(monthly * 12 * 0.8 * 100) / 100 == esperado_cents / 100


def test_no_screen_promises_a_metered_overage():
    """(n20) El pago por exceso (0,30 €/min, 0,50 €/contacto) no existe en el
    producto: no hay usage records de Stripe ni ningún cobro por consumo. Al
    agotar los minutos, el teléfono deja de atender y la salida es ampliar de
    plan. Prometer un cobro que no existe es la clase de sorpresa que acaba
    en una reclamación de consumo."""
    patron = re.compile(r"0[.,]30\s*€|0[.,]50\s*€|por\s+minuto\s+adicional", re.IGNORECASE)
    offenders: list[str] = []
    for path in sorted(DASHBOARD_SRC.rglob("*.ts*")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            if patron.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")
    assert not offenders, (
        "Vuelve a haber una promesa de pago por exceso que el producto no "
        "puede cobrar:\n" + "\n".join(f"  {o}" for o in offenders)
    )


# ---------------------------------------------------------------------------
# El precio anunciado y el que se cobra
# ---------------------------------------------------------------------------

# Stripe cobra el importe publicado sin ninguna línea de impuesto (Stripe Tax
# está activo pero sin registro fiscal, así que calcula 0%). Cualquier texto que
# diga que al precio se le suma algo promete un cobro que no ocurre.
#
# El último resto de esta promesa vivía en el `metadata.description` de /ayuda:
# no se ve en pantalla, solo en Google y al compartir el enlace. Por eso el
# barrido es sobre el fichero entero y no sobre lo que se renderiza.
_TAX_EXCLUSIVE_CLAIM = re.compile(
    r"\+\s*IVA|sin\s+IVA|IVA\s+aparte|más\s+IVA|IVA\s+no\s+includ|21\s*%\s*de\s*IVA",
    re.IGNORECASE,
)

_PRICE_COPY_ROOTS = (DASHBOARD_SRC, MOBILE_SRC)


def test_no_screen_promises_a_price_other_than_the_one_charged():
    offenders: list[str] = []
    for root in _PRICE_COPY_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.ts*")):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if _TAX_EXCLUSIVE_CLAIM.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not offenders, (
        "Hay texto que promete un precio distinto del que cobra el checkout.\n"
        "Stripe cobra los 99/199/399 € publicados y no añade ninguna línea de "
        "impuesto, así que decir «+ IVA» o «sin IVA» anuncia un cargo que nunca "
        "llega. Si algún día se añade un registro fiscal en Stripe, el cambio "
        "empieza por ahí — y por la cláusula 5.1 de los términos.\n"
        + "\n".join(f"  {line}" for line in offenders)
    )

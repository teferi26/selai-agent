"""Item 217, revisión adversarial: los caminos por los que el arreglo seguía
diciendo «hecho» sin estarlo, o pasaba a decir «roto» sin estarlo.

Cinco frentes, todos con la misma firma que el item original — el producto
afirma algo que no es cierto:

1. Una conexión anterior a exigir el secreto **con histórico de mensajes**
   pintaba en verde («Estamos recibiendo mensajes · El webhook funciona») y
   marcaba el paso de la barra lateral, justo mientras el arreglo rechazaba
   todos sus webhooks. La evidencia era real; era vieja.
2. La mentira simétrica: un número servido por *nuestra* app de Meta no
   necesita secreto propio y funciona sin él, pero el panel le decía «falta
   el secreto y por eso no entra ningún mensaje».
3. `inbound_count`/`last_inbound_at` contaban conversaciones. Una
   conversación se crea una vez por cliente, así que la fecha contestaba
   «cuándo escribió un cliente nuevo». Un canal vivo con clientes que
   repiten se veía congelado, y uno que se rompió hace semanas seguía
   enseñando la fecha de su último cliente nuevo.
4. El `hub.verify_token` es un secreto **global**, común a todos los
   inquilinos, y `GET /channels` lo imprimía a cualquier miembro de
   cualquier agencia.
5. `GET /webhooks/whatsapp?hub.verify_token=ñ` devolvía 500
   (`hmac.compare_digest` no admite str no ASCII) en un endpoint público.

Más los pines de lo que ya estaba bien y no puede aflojarse: el secreto
global no puede inyectar mensajes en una agencia con app propia, ni siquiera
en un webhook que mezcla varios números.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import MultipleResultsFound

from app.api import channels as channels_module
from app.api import whatsapp as wa_module
from app.channels.whatsapp import security as security_module
from app.core.settings import settings
from app.db.models import ConversationChannel, MessageDirection
from app.db.session import get_db
from app.main import app

GLOBAL_SECRET = "global-app-secret"
GLOBAL_VERIFY_TOKEN = "global-verify-token"
OUR_PNID = "pnid-selai"
AGENCY_PNID = "pnid-agency"
OTHER_PNID = "pnid-other-agency"
AGENCY_SECRET = "agency-app-secret"
OTHER_SECRET = "other-agency-app-secret"
WA_ID = "34600111222"

_REPO = Path(__file__).resolve().parents[2]
_SETUP_DIR = _REPO / "dashboard" / "src" / "app" / "(app)" / "setup"
WHATSAPP_CONNECT_TSX = _SETUP_DIR / "WhatsAppConnect.tsx"
SETUP_CLIENT_TSX = _SETUP_DIR / "SetupClient.tsx"
CANALES_CLIENT_TSX = (
    _REPO / "dashboard" / "src" / "app" / "(app)" / "settings" / "canales"
    / "CanalesClient.tsx"
)


# ---------------------------------------------------------------------------
# GET /webhooks/whatsapp — el verify token
# ---------------------------------------------------------------------------


@pytest.fixture
def verify_env(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(
        settings, "whatsapp_verify_token", SecretStr(GLOBAL_VERIFY_TOKEN)
    )
    return TestClient(app)


def _challenge(client: TestClient, token: str):
    return client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": token,
            "hub.challenge": "1234",
        },
    )


def test_non_ascii_verify_token_is_refused_not_a_crash(verify_env):
    """`hmac.compare_digest` levanta TypeError comparando str no ASCII, y el
    valor llega tal cual de una query string sin autenticar: bastaba con
    `?hub.verify_token=ñ` para sacar un 500 de un endpoint público."""
    resp = _challenge(verify_env, "ñ")
    assert resp.status_code == 403


def test_verify_token_is_not_shared_between_agencies(monkeypatch):
    """El token que ve una agencia no puede ser el mismo que ve otra.

    `GET /channels` lo imprime a cualquier miembro (cualquier rol, haya o no
    WhatsApp conectado). Siendo global, una captura de pantalla lo saca para
    todos los inquilinos, no se puede rotar para uno solo y una filtración no
    se puede atribuir a nadie.
    """
    monkeypatch.setattr(
        settings, "whatsapp_verify_token", SecretStr(GLOBAL_VERIFY_TOKEN)
    )
    a, b = uuid.uuid4(), uuid.uuid4()
    token_a = security_module.agency_verify_token(a)
    token_b = security_module.agency_verify_token(b)

    assert token_a and token_b
    assert token_a != token_b
    # Y sobre todo: el secreto compartido no sale del backend.
    assert GLOBAL_VERIFY_TOKEN not in token_a
    assert GLOBAL_VERIFY_TOKEN not in token_b


def test_each_agency_token_passes_the_challenge(verify_env):
    """Derivarlo no puede romper la suscripción: lo que el panel enseña tiene
    que valer en Meta."""
    token = security_module.agency_verify_token(uuid.uuid4())
    resp = _challenge(verify_env, token)
    assert resp.status_code == 200
    assert resp.text == "1234"


def test_the_global_token_still_passes(verify_env):
    """Las apps ya configuradas tienen el valor antiguo guardado en Meta.
    Invalidarlo por higiene dejaría el canal mudo sin avisar a nadie."""
    assert _challenge(verify_env, GLOBAL_VERIFY_TOKEN).status_code == 200


def test_a_forged_agency_token_is_refused(verify_env):
    """Con la forma correcta pero sin la clave no se pasa: el sufijo es un
    HMAC, no un identificador."""
    forged = f"{uuid.uuid4().hex}-{'a' * 32}"
    assert _challenge(verify_env, forged).status_code == 403


def test_without_a_configured_token_nobody_passes(monkeypatch):
    """Sin token configurado no hay «todos pasan»: cualquiera podría apuntar
    su app de Meta a esta URL."""
    monkeypatch.setattr(settings, "whatsapp_verify_token", SecretStr(""))
    client = TestClient(app)
    assert security_module.agency_verify_token(uuid.uuid4()) == ""
    assert _challenge(client, "").status_code == 403
    assert _challenge(client, f"{uuid.uuid4().hex}-{'a' * 32}").status_code == 403


# ---------------------------------------------------------------------------
# GET /channels — la evidencia que enseña el panel
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, value):
        self._value = value

    def one(self):
        return self._value

    def scalar_one(self):
        return self._value


class StatsSession:
    """Contesta las consultas de `GET /channels` leyendo lo que de verdad
    preguntan, no el orden en que llegan: si el conteo de WhatsApp vuelve a
    hacerse sobre conversaciones, este doble falla en alto."""

    def __init__(
        self, agency, *, whatsapp_inbound=(0, None), email=(0, None), sms_inbound=(0, None)
    ):
        self.agency = agency
        self.whatsapp_inbound = whatsapp_inbound
        self.email = email
        # SMS self-serve trajo a `GET /channels` la misma evidencia sobre
        # mensajes entrantes para el canal SMS. No es lo que vigila este
        # módulo, pero pasa por la misma rama y hay que contestarla.
        self.sms_inbound = sms_inbound
        self.whatsapp_query = ""

    async def get(self, model, pk):
        return self.agency if pk == self.agency.id else None

    async def execute(self, stmt):
        sql = " ".join(str(stmt).split()).lower()
        params = stmt.compile().params.values()
        if "webhook_subscriptions" in sql:
            return _Row(0)
        if "from messages" in sql:
            assert MessageDirection.INBOUND in params, (
                "el conteo de WhatsApp tiene que mirar mensajes ENTRANTES"
            )
            if ConversationChannel.SMS in params:
                # La consulta gemela del canal SMS, no la que vigila este test.
                return _Row(self.sms_inbound)
            assert ConversationChannel.WHATSAPP in params
            self.whatsapp_query = sql
            return _Row(self.whatsapp_inbound)
        if "from conversations" in sql:
            assert ConversationChannel.EMAIL in params, (
                "el único conteo sobre conversaciones que queda es el de email"
            )
            return _Row(self.email)
        raise AssertionError(f"consulta inesperada: {sql}")

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _agency(settings_blob: dict):
    return SimpleNamespace(
        id=uuid.uuid4(), name="Inmo Sol", slug="inmo-sol", settings=settings_blob
    )


@pytest.fixture
def channels_env(monkeypatch):
    # `clerk_jwks_url` vacío + development = el bypass de dev que ya usan los
    # demás tests de este router; aquí no se prueba la autenticación.
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    monkeypatch.setattr(settings, "public_base_url", "https://api.selai.es")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", OUR_PNID)
    monkeypatch.setattr(
        settings, "whatsapp_verify_token", SecretStr(GLOBAL_VERIFY_TOKEN)
    )
    yield
    app.dependency_overrides.pop(get_db, None)


def _status(session):
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    resp = TestClient(app).get(f"/api/v1/agencies/{session.agency.id}/channels")
    assert resp.status_code == 200, resp.text
    return resp.json()["whatsapp"]


OWN_APP = {
    "whatsapp_access_token": "EAAG-token",
    "whatsapp_phone_number_id": AGENCY_PNID,
    "whatsapp_app_secret": AGENCY_SECRET,
}
OWN_APP_NO_SECRET = {
    "whatsapp_access_token": "EAAG-token",
    "whatsapp_phone_number_id": AGENCY_PNID,
}
ON_OUR_NUMBER = {
    "whatsapp_access_token": "EAAG-token",
    "whatsapp_phone_number_id": OUR_PNID,
}


def test_inbound_evidence_counts_messages_not_conversations(channels_env):
    """Una conversación se crea una vez por cliente y nunca más, así que su
    fecha contesta «cuándo escribió un cliente nuevo». Con ella, un canal
    vivo cuyos clientes repiten se veía congelado en el día que empezó, y uno
    roto hace semanas seguía enseñando la fecha de su último cliente nuevo —
    la caja verde afirma «nos están entrando mensajes» en presente."""
    now = datetime.now(UTC)
    session = StatsSession(_agency(dict(OWN_APP)), whatsapp_inbound=(37, now))
    wa = _status(session)

    assert wa["inbound_count"] == 37
    assert wa["last_inbound_at"].startswith(now.isoformat()[:19])
    assert "join conversations" in session.whatsapp_query


def test_email_and_whatsapp_evidence_do_not_cross(channels_env):
    session = StatsSession(
        _agency(dict(OWN_APP)),
        whatsapp_inbound=(0, None),
        email=(9, datetime.now(UTC) - timedelta(days=1)),
    )
    wa = _status(session)
    assert wa["inbound_count"] == 0
    assert wa["last_inbound_at"] is None


def test_a_number_on_our_app_is_not_told_its_secret_is_missing(channels_env):
    """La mentira simétrica. `_agency_signing_secret` valida NUESTRO número
    con el secreto global, así que una agencia sentada en él funciona sin
    guardar nada; el panel le decía «falta el secreto de tu app y por eso no
    entra ningún mensaje»."""
    session = StatsSession(_agency(dict(ON_OUR_NUMBER)))
    wa = _status(session)

    assert wa["connected"] is True
    assert wa["app_secret_set"] is False
    assert wa["app_secret_required"] is False


def test_an_own_app_number_without_secret_is_flagged_as_required(channels_env):
    session = StatsSession(_agency(dict(OWN_APP_NO_SECRET)))
    wa = _status(session)
    assert wa["app_secret_required"] is True
    assert wa["app_secret_set"] is False


def test_the_panel_never_sees_the_global_verify_token(channels_env):
    """El valor de env es común a todos los inquilinos; lo que se imprime en
    el panel de una agencia tiene que ser suyo."""
    session = StatsSession(_agency(dict(OWN_APP)))
    wa = _status(session)

    assert wa["webhook_verify_token"] != GLOBAL_VERIFY_TOKEN
    assert GLOBAL_VERIFY_TOKEN not in wa["webhook_verify_token"]
    assert wa["webhook_verify_token"] == security_module.agency_verify_token(
        session.agency.id
    )


# ---------------------------------------------------------------------------
# El webhook: el secreto global no alcanza a una agencia con app propia
# ---------------------------------------------------------------------------


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        return None


class MultiAgencySession:
    """Resuelve cada `phone_number_id` a su agencia, como en producción con
    varios inquilinos: el conteo de agencias devuelve siempre 2, así que el
    atajo del piloto («si solo hay una agencia, esa») nunca entra y lo que se
    ejerce es el enrutado real."""

    AGENCY_COUNT = 2

    def __init__(self, by_pnid: dict):
        self.by_pnid = by_pnid

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "messages" in sql:
            return _ScalarResult(None)
        if "count" in sql:
            return _ScalarResult(self.AGENCY_COUNT)
        # El pnid es el único parámetro que puede ser una clave conocida; el
        # otro es el nombre de la clave JSON.
        pnid = next(
            (
                v
                for v in stmt.compile().params.values()
                if isinstance(v, str) and v in self.by_pnid
            ),
            None,
        )
        return _ScalarResult(self.by_pnid.get(pnid))

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _message(pnid: str, message_id: str) -> dict:
    return {
        "value": {
            "metadata": {"phone_number_id": pnid},
            "contacts": [{"wa_id": WA_ID, "profile": {"name": "Juan"}}],
            "messages": [
                {
                    "from": WA_ID,
                    "id": message_id,
                    "type": "text",
                    "text": {"body": "Hola"},
                }
            ],
        }
    }


class _DummySession:
    async def execute(self, stmt):
        return _ScalarResult(None)

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _NoopClient:
    def __init__(self, access_token=None, phone_number_id=None):
        pass

    async def send_text(self, to, text):
        return "wamid.OUT"

    async def mark_as_read(self, message_id):
        pass

    async def download_media(self, media_id, **kwargs):
        return None


@pytest.fixture
def webhook_env(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(GLOBAL_SECRET))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", OUR_PNID)
    monkeypatch.setattr(wa_module, "WhatsAppClient", _NoopClient)
    monkeypatch.setattr(wa_module, "DEBOUNCE_SECONDS", 0)

    @asynccontextmanager
    async def _task_session():
        yield _DummySession()

    monkeypatch.setattr(wa_module, "task_session", _task_session)

    handled: list = []

    async def fake_handle(db, payload):
        handled.append(payload)
        return SimpleNamespace(
            reply_text=None, conversation_id=uuid.uuid4(), handed_off=False
        )

    monkeypatch.setattr(wa_module, "handle_inbound", fake_handle)
    yield handled
    app.dependency_overrides.pop(get_db, None)


def _post_batch(session, changes: list[dict], secret: str):
    body = {"entry": [{"changes": changes}]}
    raw = json.dumps(body).encode()

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app).post(
        "/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256="
            + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
        },
    )


def test_global_secret_cannot_smuggle_a_message_into_a_protected_agency(webhook_env):
    """El ataque que el fallback global abría: un webhook que empieza por
    nuestro número (validado con el secreto global) y cuelga detrás un cambio
    para el número de una agencia con app propia."""
    protected = SimpleNamespace(id=uuid.uuid4(), settings=dict(OWN_APP))
    session = MultiAgencySession({AGENCY_PNID: protected, OUR_PNID: None})

    resp = _post_batch(
        session,
        [_message(OUR_PNID, "wamid.OURS"), _message(AGENCY_PNID, "wamid.THEIRS")],
        GLOBAL_SECRET,
    )

    assert resp.status_code == 200
    handled = webhook_env
    # El de nuestro número pasa; el de la agencia protegida, no.
    assert [p.agency_id for p in handled] == []


def test_one_agency_secret_cannot_smuggle_a_message_into_another(webhook_env):
    """Y el mismo ataque entre inquilinos: la agencia A firma con su secreto
    un webhook que también trae un mensaje para el número de B."""
    a = SimpleNamespace(id=uuid.uuid4(), settings=dict(OWN_APP))
    b = SimpleNamespace(
        id=uuid.uuid4(),
        settings={
            "whatsapp_access_token": "EAAG-b",
            "whatsapp_phone_number_id": OTHER_PNID,
            "whatsapp_app_secret": OTHER_SECRET,
        },
    )
    session = MultiAgencySession({AGENCY_PNID: a, OTHER_PNID: b})

    resp = _post_batch(
        session,
        [_message(AGENCY_PNID, "wamid.A"), _message(OTHER_PNID, "wamid.B")],
        AGENCY_SECRET,
    )

    assert resp.status_code == 200
    assert [p.agency_id for p in webhook_env] == [a.id]


def test_two_agencies_claiming_the_same_number_do_not_crash_the_webhook(webhook_env):
    """`scalar_one_or_none()` sobre dos filas levanta MultipleResultsFound, o
    sea un 500 en CADA webhook de ese número: Meta reintenta y acaba
    desactivando la suscripción, y las dos agencias se quedan mudas con el
    panel en verde. Nada impide guardar el mismo número dos veces — conectar
    solo pregunta a Meta si el token de quien llama puede leerlo, y dos
    inquilinos del mismo Meta Business pueden."""

    class _TwoOwners:
        def scalar_one_or_none(self):
            raise MultipleResultsFound("two agencies stored this phone_number_id")

    class _TwoOwnersSession(MultiAgencySession):
        async def execute(self, stmt):
            sql = str(stmt).lower()
            if "messages" in sql:
                return _ScalarResult(None)
            if "count" in sql:
                return _ScalarResult(self.AGENCY_COUNT)
            return _TwoOwners()

    resp = _post_batch(
        _TwoOwnersSession({}), [_message(AGENCY_PNID, "wamid.DUP")], AGENCY_SECRET
    )

    assert resp.status_code == 401
    assert webhook_env == []


def test_a_legacy_agency_without_secret_is_not_reachable_by_the_global_one(webhook_env):
    """Sin secreto guardado no hay vuelta al global: si lo hubiera, quien
    tuviera el secreto compartido podría escribir en el inquilino que se
    aisló. Es un 401, y por eso el panel tiene que gritar."""
    legacy = SimpleNamespace(id=uuid.uuid4(), settings=dict(OWN_APP_NO_SECRET))
    session = MultiAgencySession({AGENCY_PNID: legacy})

    resp = _post_batch(session, [_message(AGENCY_PNID, "wamid.X")], GLOBAL_SECRET)

    assert resp.status_code == 401
    assert webhook_env == []


# ---------------------------------------------------------------------------
# El panel: lo que dicen los .tsx (no hay runner de JS en el repo)
# ---------------------------------------------------------------------------


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_green_box_cannot_win_over_a_channel_we_are_rejecting():
    """El caso que el arreglo dejaba abierto: una conexión anterior a exigir
    el secreto, CON conversaciones antiguas, veía «Estamos recibiendo
    mensajes… El webhook funciona» mientras rechazábamos todos sus webhooks.
    La prueba era real; era vieja. `secretMissing` tiene que decidirse antes
    que `lastInboundAt`."""
    src = _src(WHATSAPP_CONNECT_TSX)
    body = src.split("export function InboundStatusNotice", 1)[1]
    assert "secretMissing" in body.split("if (lastInboundAt)", 1)[0], (
        "InboundStatusNotice mira lastInboundAt antes que secretMissing"
    )
    assert "El webhook funciona" not in src, (
        "afirmación en presente sostenida sobre evidencia del pasado"
    )


def test_the_missing_secret_verdict_is_not_shown_to_numbers_on_our_app():
    """Simétrico: el bloque rojo se decide con `secretMissing`, no con
    `!appSecretSet` — que también es cierto, y correcto, para un número
    servido por nuestra app."""
    src = _src(WHATSAPP_CONNECT_TSX)
    assert "app_secret_required" in src
    assert re.search(r"\{secretMissing \?", src), (
        "el bloque rojo tiene que colgar de secretMissing"
    )
    assert "const healthy = subscribed && !secretMissing;" in src


def test_every_surface_that_paints_whatsapp_uses_the_same_verdict():
    for path in (SETUP_CLIENT_TSX, CANALES_CLIENT_TSX):
        src = _src(path)
        assert "whatsappSecretMissing" in src, f"{path.name} decide por su cuenta"
        assert "secretMissing={waSecretMissing}" in src


def test_the_sidebar_tick_is_cleared_by_a_disconnect():
    """`checklist.whatsapp` se derivaba solo de `last_inbound_at`, que no se
    borra al desconectar: Canales lo ponía a false y el wizard lo volvía a
    marcar en cuanto se abría. El paso 4 pintaba entonces una conexión que ya
    no existía."""
    src = _src(SETUP_CLIENT_TSX)
    effect = src.split("const whatsappReceiving", 1)[1].split("const doneMap", 1)[0]
    assert "channels.whatsapp.connected" in effect
    assert "whatsappSecretMissing(channels.whatsapp)" in effect
    assert "whatsappReceiving === whatsappChecklistDone" in effect
    assert "setup_checklist: { whatsapp: whatsappReceiving }" in effect
    # Y el paso deja de creerse el checklist cuando /channels ya ha hablado.
    assert 'channels ? channels.whatsapp.connected : !!checklist["whatsapp"]' in src


def test_the_empty_state_does_not_blame_the_user_for_not_sending():
    """Cuando el secreto es de otra app, Meta SÍ nos escribe y nosotros
    rechazamos. Decir solo «envía un mensaje de prueba» manda al dueño a
    repetir para siempre lo único que ya había hecho bien."""
    src = _src(WHATSAPP_CONNECT_TSX)
    empty_state = src.split("todavía no nos ha entrado ningún mensaje", 1)[1]
    assert "es el de otra app" in empty_state
    assert "lo rechazamos" in empty_state


def test_the_connect_form_still_requires_the_secret():
    """Pin del arreglo original: si el campo vuelve a ser opcional, todo lo
    de arriba deja de importar."""
    src = _src(WHATSAPP_CONNECT_TSX)
    secret_field = src.split('htmlFor="wa-app-secret"', 1)[1]
    assert "required" in secret_field.split("</div>", 1)[0]
    assert "usamos nuestra comprobación general" not in src
    assert "tus mensajes entran igual" not in src


def test_channels_contract_is_mirrored_in_the_dashboard_types():
    src = _src(_REPO / "dashboard" / "src" / "lib" / "channels.ts")
    assert "app_secret_required: boolean;" in src
    assert (
        channels_module.WhatsAppStatus.model_fields["app_secret_required"] is not None
    )

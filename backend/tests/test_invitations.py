"""Invitaciones de equipo: la única puerta de entrada que no crea agencia.

Hasta ahora el producto vendía "hasta 2/5/15 personas de tu equipo" y sólo
sabía crear un usuario: el dueño del alta. Un comercial que se registraba
acababa con una agencia vacía propia. Lo que se ejerce aquí es exactamente lo
que hace falta para que esa promesa sea cierta y no se regale:

* el límite de plazas del plan (miembros **más** invitaciones pendientes),
* que sólo el responsable invita,
* que el enlace es nominativo (otro email → 403) y mortal (caducado, revocado
  o ya usado → tres errores distintos),
* que aceptar mete al invitado en LA agencia que le invitó y no crea ninguna,
* y que el invitado no puede colarse por el alta pública.

Contra una sesión falsa que responde las consultas reales (mismo patrón que
`test_voice_routing`): los valores se leen de los parámetros compilados, así
que si un endpoint empieza a preguntar otra cosa el doble falla en alto.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import invitations as invitations_module
from app.api import onboarding as onboarding_module
from app.core.auth import AuthContext, require_auth
from app.db.models import SubscriptionStatus, UserRole
from app.db.models.invitation import (
    Invitation,
    default_expiry,
    generate_token,
    normalize_email,
)
from app.db.session import get_db
from app.scripts.seed_plans import PLANS
from app.services import emails as emails_module
from app.services.legal_documents import current_versions, load_document

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()
OWNER_EMAIL = "dueno@inmosol.example.com"
INVITEE_EMAIL = "maria@inmosol.example.com"

_SPECS = {spec["code"]: spec for spec in PLANS}


def _plan(code: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), **_SPECS[code])


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=(), *, scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar if self._scalar is not None else (self._rows[0] if self._rows else 0)

    def scalar_one_or_none(self):
        if self._scalar is not None:
            return self._scalar
        return self._rows[0] if self._rows else None


def _user(
    email: str,
    *,
    role: UserRole = UserRole.AGENT,
    agency_id: uuid.UUID = AGENCY_ID,
    clerk_user_id: str | None = "user_clerk",
    full_name: str = "Persona",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        email=email,
        full_name=full_name,
        role=role,
        is_active=True,
        clerk_user_id=clerk_user_id,
        created_at=datetime.now(UTC),
    )


def _invitation(
    *,
    email: str = INVITEE_EMAIL,
    agency_id: uuid.UUID = AGENCY_ID,
    role: UserRole = UserRole.AGENT,
    expires_at: datetime | None = None,
    accepted_at: datetime | None = None,
    revoked_at: datetime | None = None,
    invited_by: uuid.UUID | None = None,
) -> Invitation:
    inv = Invitation(
        agency_id=agency_id,
        email=normalize_email(email),
        role=role,
        token=generate_token(),
        invited_by_user_id=invited_by,
        expires_at=expires_at or default_expiry(),
        accepted_at=accepted_at,
        revoked_at=revoked_at,
    )
    inv.id = uuid.uuid4()
    inv.created_at = datetime.now(UTC)
    return inv


def _agency(agency_id: uuid.UUID = AGENCY_ID) -> SimpleNamespace:
    return SimpleNamespace(id=agency_id, name="Inmobiliaria Sol", slug="inmo-sol", settings={})


class FakeSession:
    """Suscripción + plan + usuarios + invitaciones de una agencia."""

    def __init__(
        self,
        *,
        plan: SimpleNamespace | None = None,
        members: list | None = None,
        invitations: list | None = None,
        agency: SimpleNamespace | None = None,
        status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
        properties_used: int = 0,
        integrity_error: bool = False,
    ):
        self.plan = plan
        self.members = list(members or [])
        self.invitations = list(invitations or [])
        self.agency = agency if agency is not None else _agency()
        self.properties_used = properties_used
        self.integrity_error = integrity_error
        self.subscription = (
            None
            if plan is None
            else SimpleNamespace(
                id=uuid.uuid4(),
                agency_id=AGENCY_ID,
                plan_id=plan.id,
                status=status,
                voice_minutes_used=0,
                trial_ends_at=None,
            )
        )
        self.added: list = []
        self.commits = 0

    # -- consultas ---------------------------------------------------------
    async def execute(self, stmt):
        sql = " ".join(str(stmt).split())
        params = stmt.compile().params

        if "FROM subscriptions" in sql:
            return _Result([self.subscription] if self.subscription else [])
        if "FROM plans" in sql:
            return _Result([_plan("pro")])
        if "FROM properties" in sql:
            return _Result(scalar=self.properties_used)

        if "FROM invitations" in sql:
            if "count(" in sql:
                # `id != :id_1` es la invitación que se está reenviando: ya
                # tiene su plaza, contarla otra vez la duplicaría.
                excluded = params.get("id_1") if "invitations.id != :" in sql else None
                return _Result(
                    scalar=len(
                        [
                            i
                            for i in self.invitations
                            if i.is_pending and i.id != excluded
                        ]
                    )
                )
            # Con el "= :" : la lista de columnas de `select(Invitation)` ya
            # menciona token y email, así que buscar el nombre suelto casaría
            # con cualquier consulta.
            if "invitations.token = :" in sql:
                token = params.get("token_1")
                return _Result([i for i in self.invitations if i.token == token])
            if "invitations.id = :" in sql:
                # Anular y reenviar buscan por id Y por agencia en el propio
                # SQL: si alguien quita el segundo filtro, este doble le
                # devuelve la fila de la otra agencia y el test lo ve.
                inv_id = params.get("id_1")
                agency_id = params.get("agency_id_1")
                return _Result(
                    [
                        i
                        for i in self.invitations
                        if i.id == inv_id
                        and (agency_id is None or i.agency_id == agency_id)
                    ]
                )
            if "invitations.email = :" in sql:
                email = params.get("email_1")
                agency_id = params.get("agency_id_1")
                return _Result(
                    [
                        i
                        for i in self.invitations
                        if i.email == email
                        and i.is_pending
                        and (agency_id is None or i.agency_id == agency_id)
                    ]
                )
            return _Result(
                sorted(self.invitations, key=lambda i: i.created_at, reverse=True)
            )

        if "FROM users" in sql:
            if "count(" in sql:
                return _Result(scalar=len(self.members))
            if "lower(users.email)" in sql:
                email = next(
                    (v for v in params.values() if isinstance(v, str) and "@" in v), None
                )
                return _Result([m for m in self.members if m.email.lower() == email])
            if "users.full_name" in sql and "users.id IN" in sql:
                return _Result([(m.id, m.full_name) for m in self.members])
            return _Result(list(self.members))

        raise AssertionError(f"consulta inesperada: {sql}")

    async def get(self, model, pk):
        name = getattr(model, "__name__", "")
        if name == "Agency":
            return self.agency if self.agency and self.agency.id == pk else None
        if name == "Plan":
            return self.plan
        if name == "Invitation":
            return next((i for i in self.invitations if i.id == pk), None)
        if name == "User":
            return next((m for m in self.members if m.id == pk), None)
        return None

    # -- escritura ---------------------------------------------------------
    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        if self.integrity_error:
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("insert", {}, Exception("duplicate email"))
        self.commits += 1

    async def rollback(self):
        pass


def _ctx(
    *,
    email: str | None = OWNER_EMAIL,
    user: SimpleNamespace | None = None,
    clerk_user_id: str = "user_clerk",
) -> AuthContext:
    return AuthContext(
        clerk_user_id=clerk_user_id,
        email=email,
        raw_claims={},
        user=user,
        dev_bypass=False,
    )


@pytest.fixture(autouse=True)
def no_outbound_email(monkeypatch):
    """Ningún test toca Resend; se guarda a quién se habría escrito."""
    sent: list[dict] = []

    async def _send(to, subject, html, category=None):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr(invitations_module, "send_email", _send)
    monkeypatch.setattr(emails_module, "send_email", _send)
    return sent


@pytest.fixture
def make_client():
    """App aislada con los tres routers de invitaciones + el alta pública.

    No se monta sobre `app.main` a propósito: el orquestador es quien añade los
    `include_router` compartidos, y estos tests no pueden depender de ese paso.
    """
    apps: list[FastAPI] = []

    def _build(session: FakeSession, auth: AuthContext | None = None) -> TestClient:
        test_app = FastAPI()
        test_app.include_router(invitations_module.router)
        test_app.include_router(invitations_module.members_router)
        test_app.include_router(invitations_module.public_router)
        test_app.include_router(onboarding_module.router)

        async def _db_override():
            yield session

        test_app.dependency_overrides[get_db] = _db_override
        if auth is not None:
            test_app.dependency_overrides[require_auth] = lambda: auth
        apps.append(test_app)
        return TestClient(test_app)

    yield _build
    for test_app in apps:
        test_app.dependency_overrides.clear()


INVITATIONS_URL = f"/api/v1/agencies/{AGENCY_ID}/invitations"
MEMBERS_URL = f"/api/v1/agencies/{AGENCY_ID}/members"


def _owner(email: str = OWNER_EMAIL) -> SimpleNamespace:
    return _user(email, role=UserRole.OWNER, full_name="Ana Dueña")


# ---------------------------------------------------------------------------
# El límite de plazas que se vende
# ---------------------------------------------------------------------------


def test_starter_sells_two_seats():
    """Si alguien sube el tope en seed_plans, estos tests dejan de valer."""
    assert _SPECS["starter"]["max_agents"] == 2
    assert _SPECS["pro"]["max_agents"] == 5
    assert _SPECS["agencia"]["max_agents"] == 15


def test_owner_can_invite_within_the_plan(make_client, no_outbound_email):
    owner = _owner()
    session = FakeSession(plan=_plan("starter"), members=[owner])
    client = make_client(session, _ctx(user=owner))

    resp = client.post(INVITATIONS_URL, json={"email": "Maria@InmoSol.example.com"})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "maria@inmosol.example.com"  # normalizado
    assert body["status"] == "pending"
    assert body["role"] == "agent"
    stored = session.added[0]
    assert stored.agency_id == AGENCY_ID
    assert stored.invited_by_user_id == owner.id
    # El email sale con el enlace y el token dentro.
    assert no_outbound_email[0]["to"] == "maria@inmosol.example.com"
    assert stored.token in no_outbound_email[0]["html"]


def test_the_plan_limit_counts_members(make_client):
    """Starter vende 2 personas: con 2 dentro no se invita a una tercera."""
    owner = _owner()
    session = FakeSession(plan=_plan("starter"), members=[owner, _user("comercial@inmosol.example.com")])

    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": INVITEE_EMAIL}
    )

    assert resp.status_code == 402, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "plan_limit_reached"
    assert detail["resource"] == "agents"
    assert session.added == []


def test_pending_invitations_occupy_a_seat(make_client):
    """El agujero evidente: 1 miembro y 4 invitaciones sueltas en un plan de 2
    serían 5 personas dentro. Una invitación pendiente ya es una plaza dada."""
    owner = _owner()
    session = FakeSession(plan=_plan("starter"), members=[owner], invitations=[_invitation()])

    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": "otro@inmosol.example.com"}
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["used"] == 2
    assert resp.json()["detail"]["limit"] == 2
    assert session.added == []


def test_an_expired_invitation_frees_its_seat(make_client):
    """Caducada no ocupa: si no, un tope se agota solo con el tiempo."""
    owner = _owner()
    stale = _invitation(expires_at=datetime.now(UTC) - timedelta(days=1))
    session = FakeSession(plan=_plan("starter"), members=[owner], invitations=[stale])

    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": "otro@inmosol.example.com"}
    )

    assert resp.status_code == 201, resp.text


def test_an_agency_without_a_plan_is_not_blocked(make_client):
    """Entornos de desarrollo y datos sembrados: sin plan no hay tope."""
    owner = _owner()
    session = FakeSession(plan=None, members=[owner])

    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": INVITEE_EMAIL}
    )

    assert resp.status_code == 201, resp.text


def test_a_paused_agency_cannot_invite(make_client):
    owner = _owner()
    session = FakeSession(
        plan=_plan("pro"), members=[owner], status=SubscriptionStatus.PAUSED
    )

    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": INVITEE_EMAIL}
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "subscription_inactive"


def test_the_seat_counter_the_panel_paints(make_client):
    """"3 de 5 plazas": miembros + pendientes, contra el tope del plan."""
    owner = _owner()
    session = FakeSession(
        plan=_plan("pro"),
        members=[owner, _user("comercial@inmosol.example.com")],
        invitations=[_invitation()],
    )

    body = make_client(session, _ctx(user=owner)).get(MEMBERS_URL).json()

    assert body["seats"] == {"members": 2, "pending": 1, "used": 3, "limit": 5}
    assert [m["email"] for m in body["members"]] == [owner.email, "comercial@inmosol.example.com"]


# ---------------------------------------------------------------------------
# Sólo el responsable invita
# ---------------------------------------------------------------------------


def test_a_commercial_agent_cannot_invite(make_client):
    agent = _user("comercial@inmosol.example.com", role=UserRole.AGENT)
    session = FakeSession(plan=_plan("pro"), members=[agent])

    resp = make_client(session, _ctx(email=agent.email, user=agent)).post(
        INVITATIONS_URL, json={"email": INVITEE_EMAIL}
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "role_required"
    assert session.added == []


def test_a_commercial_agent_cannot_revoke(make_client):
    agent = _user("comercial@inmosol.example.com", role=UserRole.AGENT)
    inv = _invitation()
    session = FakeSession(plan=_plan("pro"), members=[agent], invitations=[inv])

    resp = make_client(session, _ctx(email=agent.email, user=agent)).delete(
        f"{INVITATIONS_URL}/{inv.id}"
    )

    assert resp.status_code == 403
    assert inv.revoked_at is None


def test_a_commercial_agent_still_sees_the_team(make_client):
    """Leer quién está en el equipo no es una acción de responsable."""
    agent = _user("comercial@inmosol.example.com", role=UserRole.AGENT)
    session = FakeSession(plan=_plan("pro"), members=[agent])

    assert make_client(session, _ctx(email=agent.email, user=agent)).get(
        MEMBERS_URL
    ).status_code == 200


def test_another_agency_cannot_list_invitations(make_client):
    """La frontera multi-tenant, sobre el router entero."""
    intruder = _owner("otro@otraagencia.es")
    intruder.agency_id = OTHER_AGENCY_ID
    session = FakeSession(plan=_plan("pro"), members=[intruder])

    resp = make_client(session, _ctx(email=intruder.email, user=intruder)).get(
        INVITATIONS_URL
    )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Duplicados
# ---------------------------------------------------------------------------


def test_inviting_someone_who_is_already_in_the_team(make_client):
    owner = _owner()
    session = FakeSession(plan=_plan("pro"), members=[owner, _user(INVITEE_EMAIL)])

    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": INVITEE_EMAIL}
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "already_member"


def test_inviting_an_email_that_belongs_to_another_agency(make_client):
    """`users.email` es único global: no es un fallo temporal, es un no."""
    owner = _owner()
    outsider = _user(INVITEE_EMAIL, agency_id=OTHER_AGENCY_ID)
    session = FakeSession(plan=_plan("pro"), members=[owner, outsider])

    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": INVITEE_EMAIL}
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "email_already_registered"


def test_two_invitations_for_the_same_person(make_client):
    owner = _owner()
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[_invitation()])

    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": INVITEE_EMAIL}
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invitation_already_pending"


# ---------------------------------------------------------------------------
# Revocar y reenviar
# ---------------------------------------------------------------------------


def test_the_owner_revokes_an_invitation(make_client):
    owner = _owner()
    inv = _invitation()
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])

    resp = make_client(session, _ctx(user=owner)).delete(f"{INVITATIONS_URL}/{inv.id}")

    assert resp.status_code == 204
    assert inv.revoked_at is not None
    assert inv.status == "revoked"


def test_an_invitation_of_another_agency_is_not_found(make_client):
    owner = _owner()
    foreign = _invitation(agency_id=OTHER_AGENCY_ID)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[foreign])

    resp = make_client(session, _ctx(user=owner)).delete(f"{INVITATIONS_URL}/{foreign.id}")

    assert resp.status_code == 404
    assert foreign.revoked_at is None


def test_resending_keeps_the_link_alive(make_client, no_outbound_email):
    """Mismo token (el invitado puede tener aún el email), plazo nuevo."""
    owner = _owner()
    inv = _invitation(expires_at=datetime.now(UTC) + timedelta(hours=1), invited_by=None)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])
    token_before = inv.token

    resp = make_client(session, _ctx(user=owner)).post(f"{INVITATIONS_URL}/{inv.id}/resend")

    assert resp.status_code == 200, resp.text
    assert inv.token == token_before
    assert inv.expires_at > datetime.now(UTC) + timedelta(days=6)
    assert no_outbound_email[-1]["to"] == INVITEE_EMAIL


def test_a_revoked_invitation_is_not_resent(make_client, no_outbound_email):
    owner = _owner()
    inv = _invitation(revoked_at=datetime.now(UTC))
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])

    resp = make_client(session, _ctx(user=owner)).post(f"{INVITATIONS_URL}/{inv.id}/resend")

    assert resp.status_code == 409
    assert no_outbound_email == []


# ---------------------------------------------------------------------------
# Aceptación — la parte delicada
# ---------------------------------------------------------------------------


def _accept_url(inv: Invitation) -> str:
    return f"/api/v1/invitations/{inv.token}/accept"


def test_accepting_creates_the_user_in_the_inviting_agency(make_client):
    """Lo que arregla la promesa entera: el invitado entra en LA agencia que
    le invitó, con su identidad de Clerk, y no se crea ninguna agencia."""
    owner = _owner()
    inv = _invitation(invited_by=owner.id)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])
    client = make_client(session, _ctx(email=INVITEE_EMAIL, user=None, clerk_user_id="user_maria"))

    resp = client.post(_accept_url(inv), json={"full_name": "María Ruiz"})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["agency_id"] == str(AGENCY_ID)
    assert body["agency_name"] == "Inmobiliaria Sol"
    assert body["role"] == "agent"

    created = [o for o in session.added if type(o).__name__ == "User"]
    assert len(created) == 1
    assert created[0].agency_id == AGENCY_ID
    assert created[0].email == INVITEE_EMAIL
    assert created[0].full_name == "María Ruiz"
    assert created[0].clerk_user_id == "user_maria"
    assert created[0].role is UserRole.AGENT
    assert inv.accepted_at is not None

    # Ni una agencia nueva, ni una suscripción nueva: ese era el bug.
    assert [type(o).__name__ for o in session.added] == ["User"]


def test_accepting_without_a_name_uses_the_email(make_client):
    owner = _owner()
    inv = _invitation(invited_by=owner.id)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(_accept_url(inv))

    assert resp.status_code == 201, resp.text
    assert session.added[0].full_name == "maria"


def test_an_invitation_for_an_owner_seat_grants_the_owner_role(make_client):
    owner = _owner()
    inv = _invitation(role=UserRole.OWNER, invited_by=owner.id)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(_accept_url(inv))

    assert resp.json()["role"] == "owner"
    assert session.added[0].role is UserRole.OWNER


def test_accepting_with_another_email_is_refused(make_client):
    """La plaza es nominativa: aceptar con otra cuenta metería en los datos de
    la agencia a alguien a quien el responsable nunca invitó."""
    owner = _owner()
    inv = _invitation(invited_by=owner.id)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])

    resp = make_client(session, _ctx(email="otro@mail.example.com", user=None)).post(_accept_url(inv))

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "invitation_email_mismatch"
    # El panel tiene que poder explicar con qué email hay que entrar.
    assert detail["invited_email"] == INVITEE_EMAIL
    assert detail["session_email"] == "otro@mail.example.com"
    assert session.added == []
    assert inv.accepted_at is None


def test_a_session_without_email_cannot_accept(make_client):
    owner = _owner()
    inv = _invitation(invited_by=owner.id)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])

    resp = make_client(session, _ctx(email=None, user=None)).post(_accept_url(inv))

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "invitation_email_mismatch"


def test_an_unknown_token_is_not_found(make_client):
    session = FakeSession(plan=_plan("pro"), members=[_owner()])

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(
        "/api/v1/invitations/no-existe/accept"
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "invitation_not_found"


def test_a_revoked_invitation_cannot_be_accepted(make_client):
    inv = _invitation(revoked_at=datetime.now(UTC))
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[inv])

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(_accept_url(inv))

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "invitation_revoked"
    assert session.added == []


def test_an_expired_invitation_cannot_be_accepted(make_client):
    inv = _invitation(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[inv])

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(_accept_url(inv))

    assert resp.status_code == 410
    assert resp.json()["detail"]["code"] == "invitation_expired"
    assert session.added == []


def test_an_already_used_invitation_cannot_be_accepted_again(make_client):
    inv = _invitation(accepted_at=datetime.now(UTC) - timedelta(days=1))
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[inv])

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(_accept_url(inv))

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invitation_already_accepted"
    assert session.added == []


def test_the_four_refusals_have_four_different_statuses(make_client):
    """Cada negativa tiene su código: el panel dice la verdad en cada caso."""
    statuses = set()
    for inv, expected in (
        (_invitation(revoked_at=datetime.now(UTC)), 403),
        (_invitation(accepted_at=datetime.now(UTC)), 409),
        (_invitation(expires_at=datetime.now(UTC) - timedelta(days=1)), 410),
    ):
        session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[inv])
        resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(_accept_url(inv))
        assert resp.status_code == expected
        statuses.add(resp.status_code)
    assert statuses == {403, 409, 410}


def test_a_member_of_another_agency_cannot_take_the_seat(make_client):
    """Mismo email, ya dentro de otra agencia: nadie está en dos sitios."""
    inv = _invitation()
    outsider = _user(INVITEE_EMAIL, agency_id=OTHER_AGENCY_ID)
    session = FakeSession(plan=_plan("pro"), members=[_owner(), outsider], invitations=[inv])

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=outsider)).post(_accept_url(inv))

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "already_in_another_agency"
    assert session.added == []


def test_accepting_twice_from_the_same_account_is_idempotent(make_client):
    """Doble clic en el email: no duplica usuario ni revienta."""
    inv = _invitation()
    already = _user(INVITEE_EMAIL, clerk_user_id=None)
    session = FakeSession(plan=_plan("pro"), members=[_owner(), already], invitations=[inv])

    resp = make_client(
        session, _ctx(email=INVITEE_EMAIL, user=already, clerk_user_id="user_maria")
    ).post(_accept_url(inv))

    assert resp.status_code == 201
    assert resp.json()["user_id"] == str(already.id)
    assert session.added == []
    # De paso enlaza la identidad de Clerk que aún no tenía.
    assert already.clerk_user_id == "user_maria"
    assert inv.accepted_at is not None


def test_a_downgraded_agency_cannot_take_in_the_invitee(make_client):
    """El plan se baja entre la invitación y el clic: la plaza ya no existe."""
    inv = _invitation()
    session = FakeSession(
        plan=_plan("starter"),
        members=[_owner(), _user("comercial@inmosol.example.com")],
        invitations=[inv],
    )

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(_accept_url(inv))

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "plan_limit_reached"
    assert session.added == []


def test_two_tabs_accepting_at_once_do_not_duplicate_the_user(make_client):
    inv = _invitation()
    session = FakeSession(
        plan=_plan("pro"), members=[_owner()], invitations=[inv], integrity_error=True
    )

    resp = make_client(session, _ctx(email=INVITEE_EMAIL, user=None)).post(_accept_url(inv))

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invitation_already_accepted"


# ---------------------------------------------------------------------------
# La pantalla que ve el invitado antes de tener cuenta
# ---------------------------------------------------------------------------


def test_the_landing_page_can_be_read_without_an_account(make_client):
    owner = _owner()
    inv = _invitation(invited_by=owner.id)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[inv])

    # Sin `auth`: la vista previa es pública a propósito.
    resp = make_client(session).get(f"/api/v1/invitations/{inv.token}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agency_name"] == "Inmobiliaria Sol"
    assert body["inviter_name"] == "Ana Dueña"
    assert body["email"] == INVITEE_EMAIL
    assert body["status"] == "pending"


def test_the_landing_page_says_when_the_link_is_dead(make_client):
    inv = _invitation(revoked_at=datetime.now(UTC))
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[inv])

    body = make_client(session).get(f"/api/v1/invitations/{inv.token}").json()

    assert body["status"] == "revoked"


def test_an_unknown_token_has_no_landing_page(make_client):
    session = FakeSession(plan=_plan("pro"), members=[_owner()])
    assert make_client(session).get("/api/v1/invitations/lo-que-sea").status_code == 404


# ---------------------------------------------------------------------------
# El invitado NO acaba en el alta pública
# ---------------------------------------------------------------------------


def _signup_body(email: str) -> dict:
    return {
        "agency_name": "Agencia Fantasma",
        "owner_email": email,
        "owner_full_name": "María Ruiz",
        "accepted_terms": True,
        "accepted_dpa": True,
        "accepted_privacy": True,
        "accepted_versions": current_versions(),
        "accepted_hashes": {
            slug: load_document(slug, version).hash
            for slug, version in current_versions().items()
        },
    }


def test_an_invited_person_cannot_create_a_second_agency(make_client):
    """El bug de raíz: hoy quien se registra invitado crea otra agencia vacía.
    El panel ya le lleva a /invitacion/{token}, pero el alta es pública."""
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[_invitation()])

    # El alta ya no es pública (n3): quien la pide llega con sesión propia, y
    # el email del alta tiene que ser el de esa sesión.
    resp = make_client(session, auth=_ctx(email=INVITEE_EMAIL)).post(
        "/api/v1/onboarding", json=_signup_body(INVITEE_EMAIL)
    )

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "pending_invitation"
    # Ni token ni nombre de agencia: quien llama no ha probado ser ese buzón.
    assert "token" not in detail
    assert "Inmobiliaria Sol" not in str(detail)
    assert session.added == []


def test_a_revoked_invitation_does_not_block_the_signup(make_client, monkeypatch):
    """Si la invitación ya no vale, esa persona sí puede montar su agencia."""
    inv = _invitation(revoked_at=datetime.now(UTC))
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[inv])

    async def _fake_onboard(db, **kwargs):
        return SimpleNamespace(
            agency_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            vapi_assistant_id=None,
            trial_ends_at=datetime.now(UTC),
        )

    monkeypatch.setattr(onboarding_module, "onboard_agency", _fake_onboard)

    resp = make_client(session, auth=_ctx(email=INVITEE_EMAIL)).post(
        "/api/v1/onboarding", json=_signup_body(INVITEE_EMAIL)
    )

    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# El token
# ---------------------------------------------------------------------------


def test_tokens_are_unguessable_and_unique():
    tokens = {generate_token() for _ in range(200)}
    assert len(tokens) == 200
    # token_urlsafe(32) → 43 caracteres, 256 bits. Ni cortos ni derivados.
    assert all(len(t) >= 40 for t in tokens)
    assert all(INVITEE_EMAIL not in t for t in tokens)


def test_status_is_derived_from_the_timestamps():
    assert _invitation().status == "pending"
    assert _invitation(accepted_at=datetime.now(UTC)).status == "accepted"
    assert _invitation(revoked_at=datetime.now(UTC)).status == "revoked"
    assert _invitation(expires_at=datetime.now(UTC) - timedelta(days=1)).status == "expired"
    # Aceptada y revocada a la vez: manda el hecho de que ya se usó.
    both = _invitation(accepted_at=datetime.now(UTC), revoked_at=datetime.now(UTC))
    assert both.status == "accepted"
    # Marcas sin zona (SQLite, filas escritas a mano) no pueden explotar.
    naive = _invitation()
    naive.expires_at = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
    assert naive.status == "expired"


def test_emails_are_normalized():
    assert normalize_email("  Maria@InmoSol.Example.COM ") == "maria@inmosol.example.com"
    assert normalize_email("") == ""


# ---------------------------------------------------------------------------
# (n26) "Invitación enviada" tiene que ser verdad
# ---------------------------------------------------------------------------


def test_a_failed_email_is_reported_with_a_link_to_share(make_client, monkeypatch):
    """El panel decía "invitación enviada" tanto si el correo salía como si el
    proveedor lo rechazaba, y el compañero esperaba un email que no existía.

    El backend dice ahora si salió, y da el enlace para pasarlo por WhatsApp:
    el asiento ya está creado, solo falta que le llegue la puerta.
    """
    async def _falla(to, subject, html, category=None):
        return False

    monkeypatch.setattr(invitations_module, "send_email", _falla)

    owner = _owner()
    session = FakeSession(plan=_plan("pro"), members=[owner])
    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": "companero@inmosol.example.com"}
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email_sent"] is False
    assert body["accept_url"] and "/invitacion/" in body["accept_url"]


def test_a_sent_email_says_so(make_client, no_outbound_email):
    owner = _owner()
    session = FakeSession(plan=_plan("pro"), members=[owner])
    resp = make_client(session, _ctx(user=owner)).post(
        INVITATIONS_URL, json={"email": "companero@inmosol.example.com"}
    )
    assert resp.json()["email_sent"] is True


def test_the_listing_never_leaks_the_token(make_client, no_outbound_email):
    """El token ES la credencial. Solo viaja a quien acaba de crear o
    reenviar la invitación (rutas de owner), nunca en el listado, que
    cualquier miembro del equipo puede leer."""
    owner = _owner()
    session = FakeSession(plan=_plan("pro"), members=[owner])
    client = make_client(session, _ctx(user=owner))
    client.post(INVITATIONS_URL, json={"email": "companero@inmosol.example.com"})

    listado = client.get(INVITATIONS_URL)
    assert listado.status_code == 200, listado.text
    for item in listado.json():
        assert item.get("accept_url") is None
        assert item.get("email_sent") is None

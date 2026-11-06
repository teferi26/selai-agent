"""Revisión adversarial de las invitaciones de equipo.

`test_invitations.py` prueba que el camino feliz y los cuatro rechazos
evidentes funcionan. Aquí se ataca lo que queda: los caminos por los que una
plaza vendida se regala, por los que un enlace muerto sigue contando algo, y
por los que alguien que no es responsable toca lo que no debe.

Cada test nombra el ataque, no la función. Si un test de este fichero se pone
rojo, alguien acaba de abrir la puerta de una agencia.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import SubscriptionStatus, UserRole
from app.db.models.invitation import generate_token
from app.services.emails import build_invitation_email_html
from tests.test_invitations import (
    AGENCY_ID,
    INVITATIONS_URL,
    INVITEE_EMAIL,
    MEMBERS_URL,
    OTHER_AGENCY_ID,
    FakeSession,
    _agency,
    _ctx,
    _invitation,
    _owner,
    _plan,
    _user,
)
from tests.test_invitations import make_client as _make_client
from tests.test_invitations import no_outbound_email as _no_outbound_email

# Los dobles y el cliente de pruebas se reutilizan tal cual: si el módulo del
# constructor cambia de forma, estos ataques dejan de compilar en vez de
# seguir verdes contra un fake que ya no se parece al código. Se re-exportan
# con una asignación (y no importando el nombre) para que pytest los vea como
# fixtures de este módulo sin que el linter los tome por un import duplicado.
make_client = _make_client
no_outbound_email = _no_outbound_email

PUBLIC_URL = "/api/v1/invitations"


def _expired(**kw):
    return _invitation(expires_at=datetime.now(UTC) - timedelta(days=1), **kw)


# ---------------------------------------------------------------------------
# Regalar plazas: el ataque económico
# ---------------------------------------------------------------------------


def test_resending_a_dead_invitation_cannot_conjure_a_seat(make_client):
    """Reenviar revive una invitación caducada, y una caducada NO ocupa plaza.

    Starter vende 2 personas. Dueño (1 miembro) + 1 invitación viva = 2, lleno.
    La caducada de antes ya no contaba... hasta que el dueño le da a "reenviar"
    y vuelve a estar viva: 3 plazas prometidas en un plan de 2. La tercera
    persona descubre el problema al pulsar el enlace, no el dueño al enviarlo.
    """
    stale = _expired(email="antiguo@inmosol.example.com")
    session = FakeSession(
        plan=_plan("starter"),
        members=[_owner()],
        invitations=[stale, _invitation(email="viva@inmosol.example.com")],
    )
    client = make_client(session, _ctx(user=session.members[0]))

    resp = client.post(f"{INVITATIONS_URL}/{stale.id}/resend")

    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "plan_limit_reached"
    # Y sigue muerta: reenviar no debe dejarla a medio revivir.
    assert stale.is_expired()


def test_resending_a_live_invitation_still_works_when_there_is_room(
    make_client, no_outbound_email
):
    """El control de plazas no puede romper el reenvío legítimo: la propia
    invitación que se reenvía ya ocupa su plaza, no puede contarse dos veces."""
    inv = _invitation()
    session = FakeSession(
        plan=_plan("starter"), members=[_owner()], invitations=[inv]
    )
    client = make_client(session, _ctx(user=session.members[0]))

    resp = client.post(f"{INVITATIONS_URL}/{inv.id}/resend")

    assert resp.status_code == 200, resp.text
    assert not inv.is_expired()
    assert no_outbound_email[-1]["to"] == INVITEE_EMAIL


def test_resending_is_blocked_when_the_subscription_died(make_client):
    """Impagado: reenviar es prometer una plaza que no se puede servir."""
    stale = _expired()
    session = FakeSession(
        plan=_plan("pro"),
        members=[_owner()],
        invitations=[stale],
        status=SubscriptionStatus.UNPAID,
    )
    client = make_client(session, _ctx(user=session.members[0]))

    resp = client.post(f"{INVITATIONS_URL}/{stale.id}/resend")

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "subscription_inactive"


def test_five_pending_plus_five_accepted_never_happens(make_client):
    """El ataque del enunciado: llenar el plan de invitaciones y luego que
    todas se acepten. Pro vende 5; con 3 miembros y 2 pendientes está lleno."""
    session = FakeSession(
        plan=_plan("pro"),
        members=[_owner(), _user("a@inmosol.example.com"), _user("b@inmosol.example.com")],
        invitations=[_invitation(email="c@inmosol.example.com"), _invitation(email="d@inmosol.example.com")],
    )
    client = make_client(session, _ctx(user=session.members[0]))

    resp = client.post(
        INVITATIONS_URL, json={"email": "sexta@inmosol.example.com", "role": "agent"}
    )

    assert resp.status_code == 402
    assert resp.json()["detail"] == {
        "code": "plan_limit_reached",
        "resource": "agents",
        "used": 5,
        "limit": 5,
    }


def test_the_last_invitee_loses_instead_of_the_plan(make_client):
    """Aunque hubiera más invitaciones vivas de las que caben (una carrera
    entre dos responsables invitando a la vez, un plan rebajado después), el
    número de personas dentro no puede pasar del plan: quien acepta cuando ya
    no queda sitio se queda fuera con un 402 que nombra a quién avisar."""
    inv = _invitation(email="tarde@inmosol.example.com")
    session = FakeSession(
        plan=_plan("starter"),
        members=[_owner(), _user("primera@inmosol.example.com")],
        invitations=[inv],
    )
    client = make_client(session, _ctx(email="tarde@inmosol.example.com", user=None))

    resp = client.post(f"{PUBLIC_URL}/{inv.token}/accept", json={})

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "plan_limit_reached"
    assert session.added == []
    assert inv.accepted_at is None


def test_during_the_trial_the_seats_are_the_trial_plan_s(make_client):
    """DECISIÓN CONSCIENTE, no descuido, y con una consecuencia que hay que
    tener a la vista: la prueba de 14 días se vende como "todas las funciones
    del plan Pro", y `plan_limits.enforced_plan_state` aplica esa promesa
    también a las plazas. Una agencia aparcada en Starter (2) puede montar un
    equipo de 5 durante la prueba.

    A diferencia de los minutos de voz, una plaza NO se devuelve al acabar la
    prueba: las filas de `users` siguen ahí y nada las quita. Si esto deja de
    ser aceptable, el cambio es leer aquí el plan aparcado (`get_plan_state`)
    en vez del vigente — pero es una decisión de producto y de facturación,
    no de este módulo.
    """
    session = FakeSession(
        plan=_plan("starter"), members=[_owner()], status=SubscriptionStatus.TRIALING
    )
    client = make_client(session, _ctx(user=session.members[0]))

    seats = client.get(MEMBERS_URL).json()["seats"]

    assert seats["limit"] == 5, "la prueba concede las plazas del plan Pro"


# ---------------------------------------------------------------------------
# Quién manda: rol y agencia
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path_suffix",
    [("post", ""), ("delete", "/{id}"), ("post", "/{id}/resend")],
)
def test_no_commercial_agent_reaches_any_mutating_route(make_client, method, path_suffix):
    """`require_role` está en las tres rutas que cambian algo, no sólo en la
    de invitar."""
    inv = _invitation()
    agent = _user("comercial@inmosol.example.com", role=UserRole.AGENT)
    session = FakeSession(plan=_plan("pro"), members=[_owner(), agent], invitations=[inv])
    client = make_client(session, _ctx(email=agent.email, user=agent))

    url = INVITATIONS_URL + path_suffix.format(id=inv.id)
    resp = client.request(method, url, json={"email": "x@inmosol.example.com"})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "role_required"
    assert resp.json()["detail"]["required"] == ["admin", "owner"]


def test_an_inactive_owner_has_no_powers_left(make_client):
    """Dar de baja a alguien tiene que quitarle el mando, no sólo el acceso:
    `require_role` mira `is_active` igual que `ensure_agency_access`."""
    owner = _owner()
    owner.is_active = False
    session = FakeSession(plan=_plan("pro"), members=[owner])
    client = make_client(session, _ctx(user=owner))

    resp = client.post(INVITATIONS_URL, json={"email": "nueva@inmosol.example.com"})

    assert resp.status_code == 403


def test_an_owner_cannot_mint_an_administrator(make_client):
    """`admin` es nuestro, para soporte. Repartirlo desde el panel sería dar
    permisos que el cliente no compró."""
    session = FakeSession(plan=_plan("pro"), members=[_owner()])
    client = make_client(session, _ctx(user=session.members[0]))

    resp = client.post(INVITATIONS_URL, json={"email": "x@inmosol.example.com", "role": "admin"})

    assert resp.status_code == 422


def test_an_owner_cannot_invite_into_someone_elses_agency(make_client):
    """Ser dueño de la agencia A no da nada sobre la B."""
    owner = _owner()
    session = FakeSession(plan=_plan("pro"), members=[owner])
    client = make_client(session, _ctx(user=owner))

    resp = client.post(
        f"/api/v1/agencies/{OTHER_AGENCY_ID}/invitations",
        json={"email": "x@rival.es"},
    )

    assert resp.status_code == 403


def test_an_owner_cannot_read_someone_elses_team(make_client):
    owner = _owner()
    session = FakeSession(plan=_plan("pro"), members=[owner])
    client = make_client(session, _ctx(user=owner))

    resp = client.get(f"/api/v1/agencies/{OTHER_AGENCY_ID}/members")

    assert resp.status_code == 403


@pytest.mark.parametrize("verb,suffix", [("delete", ""), ("post", "/resend")])
def test_an_invitation_id_of_another_agency_is_never_touched(make_client, verb, suffix):
    """El id es un UUID de otra agencia: adivinarlo no puede servir de nada.
    404, no 204, y sobre todo la fila de la otra agencia intacta."""
    foreign = _invitation(agency_id=OTHER_AGENCY_ID, email="ajena@rival.es")
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[foreign])
    client = make_client(session, _ctx(user=session.members[0]))

    resp = getattr(client, verb)(f"{INVITATIONS_URL}/{foreign.id}{suffix}")

    assert resp.status_code == 404
    assert foreign.revoked_at is None
    assert session.commits == 0


def test_the_team_list_never_leaks_another_agencys_invitations(make_client):
    """La lista se filtra por agencia en SQL; si alguien quita ese filtro en un
    refactor, este test lo ve."""
    mine = _invitation(email="mia@inmosol.example.com")
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[mine])
    client = make_client(session, _ctx(user=session.members[0]))

    resp = client.get(INVITATIONS_URL)

    assert resp.status_code == 200
    assert [row["email"] for row in resp.json()] == ["mia@inmosol.example.com"]


def test_no_response_ever_carries_the_token(make_client, no_outbound_email):
    """El token es la credencial. Un comercial que lee la lista no puede
    quedarse con el enlace de otra persona y usarlo él."""
    inv = _invitation()
    agent = _user("comercial@inmosol.example.com", role=UserRole.AGENT)
    session = FakeSession(plan=_plan("pro"), members=[_owner(), agent], invitations=[inv])
    client = make_client(session, _ctx(email=agent.email, user=agent))

    body = client.get(INVITATIONS_URL).text

    assert inv.token not in body
    assert "token" not in body


# ---------------------------------------------------------------------------
# El enlace: reutilizar, adivinar, sobrevivir
# ---------------------------------------------------------------------------


def test_an_accepted_token_is_dead_for_everyone_else(make_client):
    """Reutilizar un token ya aceptado desde OTRA cuenta: ni siquiera llega a
    mirarse el email, la invitación ya se gastó."""
    used = _invitation(accepted_at=datetime.now(UTC))
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[used])
    client = make_client(session, _ctx(email="oportunista@otro.es", user=None))

    resp = client.post(f"{PUBLIC_URL}/{used.token}/accept", json={})

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invitation_already_accepted"
    assert session.added == []


def test_a_dead_link_stops_naming_the_person_it_was_for(make_client):
    """La vista previa es pública y el token vive para siempre en el buzón de
    quien lo recibió (y de quien se lo reenvíe). Mientras la invitación está
    viva, decir a quién va dirigida es el único modo de que la persona sepa con
    qué email registrarse. En cuanto está muerta ya no compra nada: seguir
    publicando el email y el nombre de quien invitó es regalar datos
    personales a cualquiera que conserve el enlace."""
    dead = _invitation(accepted_at=datetime.now(UTC), invited_by=uuid.uuid4())
    session = FakeSession(plan=_plan("pro"), members=[_owner()], invitations=[dead])
    client = make_client(session)

    body = client.get(f"{PUBLIC_URL}/{dead.token}").json()

    assert body["status"] == "accepted"
    assert body["email"] == ""
    assert body["inviter_name"] is None
    assert body["agency_name"] == ""


def test_a_live_link_still_says_what_the_invitee_needs(make_client):
    """El contrapeso del test anterior: viva, la vista previa sigue diciendo
    agencia, quién invita y con qué email hay que entrar."""
    owner = _owner()
    live = _invitation(invited_by=owner.id)
    session = FakeSession(plan=_plan("pro"), members=[owner], invitations=[live])
    client = make_client(session)

    body = client.get(f"{PUBLIC_URL}/{live.token}").json()

    assert body["status"] == "pending"
    assert body["email"] == INVITEE_EMAIL
    assert body["inviter_name"] == "Ana Dueña"
    assert body["agency_name"] == "Inmobiliaria Sol"


def test_a_token_that_does_not_exist_answers_the_same_as_a_wrong_one(make_client):
    """Enumerar tokens no puede dar señal: inventado y con formato de token
    real responden lo mismo."""
    session = FakeSession(plan=_plan("pro"), members=[_owner()])
    client = make_client(session)

    invented = client.get(f"{PUBLIC_URL}/{generate_token()}")
    garbage = client.get(f"{PUBLIC_URL}/aaaa")

    assert invented.status_code == garbage.status_code == 404
    assert invented.json() == garbage.json()


def test_the_preview_of_an_invitation_never_names_a_stranger(make_client):
    """`invited_by_user_id` es SET NULL y podría apuntar a un usuario de otra
    agencia tras un arreglo manual: una ruta pública no puede nombrarlo."""
    intruder = _user("ajeno@rival.es", agency_id=OTHER_AGENCY_ID, full_name="Quien Sea")
    inv = _invitation(invited_by=intruder.id)
    session = FakeSession(plan=_plan("pro"), members=[intruder], invitations=[inv])
    client = make_client(session)

    body = client.get(f"{PUBLIC_URL}/{inv.token}").json()

    assert body["inviter_name"] is None


# ---------------------------------------------------------------------------
# Aceptar: la agencia equivocada
# ---------------------------------------------------------------------------


def test_the_seat_goes_to_the_agency_that_paid_for_it(make_client):
    """Aceptar mete al invitado en la agencia que invitó, nunca en la que
    aparezca en el cuerpo de la petición ni en ninguna otra."""
    inv = _invitation(agency_id=OTHER_AGENCY_ID)
    session = FakeSession(
        plan=_plan("pro"),
        members=[],
        invitations=[inv],
        agency=_agency(OTHER_AGENCY_ID),
    )
    client = make_client(session, _ctx(email=INVITEE_EMAIL, user=None))

    resp = client.post(
        f"{PUBLIC_URL}/{inv.token}/accept",
        json={"full_name": "María", "agency_id": str(AGENCY_ID)},
    )

    assert resp.status_code == 201
    assert resp.json()["agency_id"] == str(OTHER_AGENCY_ID)
    assert session.added[0].agency_id == OTHER_AGENCY_ID


def test_capitalisation_does_not_make_a_different_person(make_client):
    """Clerk devuelve el email tal y como lo escribió la persona. "MARIA@..."
    y "maria@..." son la misma y tienen que poder entrar."""
    inv = _invitation()
    session = FakeSession(plan=_plan("pro"), members=[], invitations=[inv])
    client = make_client(session, _ctx(email="  MARIA@Inmosol.EXAMPLE.Com  ", user=None))

    resp = client.post(f"{PUBLIC_URL}/{inv.token}/accept", json={})

    assert resp.status_code == 201
    assert session.added[0].email == INVITEE_EMAIL


def test_a_lookalike_email_is_not_the_invitee(make_client):
    """Un email parecido no vale: la comparación es exacta, no "empieza por"."""
    inv = _invitation()
    session = FakeSession(plan=_plan("pro"), members=[], invitations=[inv])
    client = make_client(session, _ctx(email="maria@inmosol.example.com.rival.es", user=None))

    resp = client.post(f"{PUBLIC_URL}/{inv.token}/accept", json={})

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "invitation_email_mismatch"
    assert session.added == []


def test_the_invitee_cannot_promote_themselves_while_accepting(make_client):
    """El rol lo fija quien invita. Lo que venga en el cuerpo se ignora."""
    inv = _invitation(role=UserRole.AGENT)
    session = FakeSession(plan=_plan("pro"), members=[], invitations=[inv])
    client = make_client(session, _ctx(email=INVITEE_EMAIL, user=None))

    resp = client.post(
        f"{PUBLIC_URL}/{inv.token}/accept", json={"full_name": "M", "role": "owner"}
    )

    assert resp.status_code == 201
    assert resp.json()["role"] == "agent"
    assert session.added[0].role is UserRole.AGENT


def test_revoking_after_the_person_joined_does_not_pretend_to_undo_it(make_client):
    """Anular el enlace no echa a nadie. Decir 204 haría creer al dueño que ha
    quitado el acceso a alguien que sigue dentro."""
    accepted = _invitation(accepted_at=datetime.now(UTC))
    session = FakeSession(
        plan=_plan("pro"), members=[_owner()], invitations=[accepted]
    )
    client = make_client(session, _ctx(user=session.members[0]))

    resp = client.delete(f"{INVITATIONS_URL}/{accepted.id}")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invitation_already_accepted"
    assert accepted.revoked_at is None


def test_a_revoked_invitation_frees_its_seat_immediately(make_client):
    """Anular tiene que devolver la plaza en el acto, o el dueño se queda sin
    poder invitar a nadie más hasta que caduque sola."""
    session = FakeSession(
        plan=_plan("starter"),
        members=[_owner()],
        invitations=[_invitation(revoked_at=datetime.now(UTC))],
    )
    client = make_client(session, _ctx(user=session.members[0]))

    resp = client.get(MEMBERS_URL)

    assert resp.json()["seats"] == {"members": 1, "pending": 0, "used": 1, "limit": 2}


# ---------------------------------------------------------------------------
# El email que sale de aquí
# ---------------------------------------------------------------------------


def test_the_invitation_email_cannot_be_used_to_inject_html():
    """El nombre de la agencia y el de quien invita los escribe un cliente y
    el email lo lee alguien de fuera de la cuenta: es el único correo del
    producto cuyo destinatario es un desconocido."""
    html = build_invitation_email_html(
        "<script>alert(1)</script>",
        '"><img src=x onerror=alert(1)>',
        "tok",
        role="agent",
    )

    # Lo que importa no es que las palabras desaparezcan, sino que ninguna
    # abra una etiqueta ni cierre un atributo: quedan como texto.
    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&quot;&gt;" in html


def test_the_invitation_email_carries_the_link_and_the_deadline():
    html = build_invitation_email_html("Inmobiliaria Sol", "Ana", "TOK123", expires_days=7)

    assert "https://www.selai.es/invitacion/TOK123" in html
    assert "7 días" in html


def test_an_unknown_role_does_not_leak_into_the_email():
    """`ROLE_LABELS.get(role, ...)` con un rol raro no puede escupir el valor
    crudo dentro del HTML."""
    html = build_invitation_email_html("A", "B", "t", role="<b>jefe</b>")

    assert "<b>jefe</b>" not in html
    assert "comercial" in html

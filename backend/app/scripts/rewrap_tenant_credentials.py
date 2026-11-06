"""Check or rewrap tenant credentials without replacing ``agency.settings``.

Run without flags for a read-only preflight. Use ``--apply`` only after every
API and worker process understands both ``enc:v1`` and keyed ``enc:v2``
envelopes. The write phase uses a per-key compare-and-swap and one transaction:
an unreadable value or concurrent credential change leaves the database
untouched.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.core.settings import settings
from app.services.secret_store import (
    SECRET_SETTINGS_KEYS,
    SecretDecryptionError,
    SecretKeyConfigurationError,
    ensure_explicit_tenant_keyring,
    rewrap_secret,
)


class CredentialRewrapError(RuntimeError):
    """The online data step cannot complete without operator attention."""


@dataclass(frozen=True)
class RewrapAction:
    agency_id: Any
    setting_key: str
    expected: str = field(repr=False)
    replacement: str = field(repr=False)


@dataclass(frozen=True)
class WebhookSecretRewrapAction:
    subscription_id: Any
    expected: str = field(repr=False)
    replacement: str = field(repr=False)


_AGENCIES = sa.table(
    "agencies",
    sa.column("id"),
    sa.column("settings", sa.JSON()),
)

_WEBHOOK_SUBSCRIPTIONS = sa.table(
    "webhook_subscriptions",
    sa.column("id"),
    sa.column("secret"),
)

_UPDATE_ONE_KEY = sa.text(
    """
    UPDATE agencies
       SET settings = CAST(
               jsonb_set(
                   CAST(settings AS jsonb),
                   ARRAY[CAST(:setting_key AS text)],
                   to_jsonb(CAST(:replacement AS text)),
                   true
               ) AS json
           ),
           updated_at = now()
     WHERE id = :agency_id
       AND (settings ->> CAST(:setting_key AS text)) = :expected
    """
)

_UPDATE_WEBHOOK_SECRET = sa.text(
    """
    UPDATE webhook_subscriptions
       SET secret = :replacement,
           updated_at = now()
     WHERE id = :subscription_id
       AND secret = :expected
    """
)


def plan_rewrap_actions(rows: Iterable[Mapping[str, Any]]) -> list[RewrapAction]:
    """Preflight every value before any transaction is allowed to write."""
    actions: list[RewrapAction] = []
    for row in rows:
        agency_id = row["id"]
        raw_settings = row.get("settings")
        if raw_settings is None:
            blob: Mapping[str, Any] = {}
        elif isinstance(raw_settings, Mapping):
            blob = raw_settings
        else:
            raise CredentialRewrapError(f"agency {agency_id}: settings is not an object")

        for setting_key in sorted(SECRET_SETTINGS_KEYS):
            raw = blob.get(setting_key)
            if raw in (None, ""):
                continue
            if not isinstance(raw, str):
                raise CredentialRewrapError(
                    f"agency {agency_id}, {setting_key}: credential is not a string"
                )
            try:
                replacement = rewrap_secret(raw)
            except SecretDecryptionError as exc:
                raise CredentialRewrapError(
                    f"agency {agency_id}, {setting_key}: credential cannot be decrypted"
                ) from exc
            if replacement != raw:
                actions.append(
                    RewrapAction(
                        agency_id=agency_id,
                        setting_key=setting_key,
                        expected=raw,
                        replacement=replacement,
                    )
                )
    return actions


def scan_rewrap_actions(connection: sa.Connection) -> list[RewrapAction]:
    rows = connection.execute(
        sa.select(_AGENCIES.c.id, _AGENCIES.c.settings).order_by(_AGENCIES.c.id)
    ).mappings()
    return plan_rewrap_actions(rows)


def plan_webhook_rewrap_actions(
    rows: Iterable[Mapping[str, Any]],
) -> list[WebhookSecretRewrapAction]:
    """Preflight outbound webhook signing secrets without logging their values."""
    actions: list[WebhookSecretRewrapAction] = []
    for row in rows:
        subscription_id = row["id"]
        raw = row.get("secret")
        if not isinstance(raw, str) or not raw:
            raise CredentialRewrapError(
                f"webhook subscription {subscription_id}: signing secret is missing or invalid"
            )
        try:
            replacement = rewrap_secret(raw)
        except SecretDecryptionError as exc:
            raise CredentialRewrapError(
                f"webhook subscription {subscription_id}: signing secret cannot be decrypted"
            ) from exc
        if replacement != raw:
            actions.append(
                WebhookSecretRewrapAction(
                    subscription_id=subscription_id,
                    expected=raw,
                    replacement=replacement,
                )
            )
    return actions


def scan_webhook_rewrap_actions(
    connection: sa.Connection,
) -> list[WebhookSecretRewrapAction]:
    rows = connection.execute(
        sa.select(_WEBHOOK_SUBSCRIPTIONS.c.id, _WEBHOOK_SUBSCRIPTIONS.c.secret).order_by(
            _WEBHOOK_SUBSCRIPTIONS.c.id
        )
    ).mappings()
    return plan_webhook_rewrap_actions(rows)


def apply_rewrap_actions(
    connection: sa.Connection,
    actions: Sequence[RewrapAction],
) -> None:
    """Apply all actions atomically; a same-key race rolls everything back."""
    for action in actions:
        result = connection.execute(
            _UPDATE_ONE_KEY,
            {
                "agency_id": action.agency_id,
                "setting_key": action.setting_key,
                "expected": action.expected,
                "replacement": action.replacement,
            },
        )
        if result.rowcount != 1:
            raise CredentialRewrapError(
                f"agency {action.agency_id}, {action.setting_key}: concurrent credential update"
            )


def apply_webhook_rewrap_actions(
    connection: sa.Connection,
    actions: Sequence[WebhookSecretRewrapAction],
) -> None:
    """Apply outbound webhook secret changes with per-row compare-and-swap."""
    for action in actions:
        result = connection.execute(
            _UPDATE_WEBHOOK_SECRET,
            {
                "subscription_id": action.subscription_id,
                "expected": action.expected,
                "replacement": action.replacement,
            },
        )
        if result.rowcount != 1:
            raise CredentialRewrapError(
                f"webhook subscription {action.subscription_id}: concurrent signing secret update"
            )


def run(*, apply: bool) -> int:
    active_key_id = ensure_explicit_tenant_keyring()
    if apply and settings.tenant_credential_write_mode != "v2":
        raise SecretKeyConfigurationError(
            "TENANT_CREDENTIAL_WRITE_MODE must be v2 before applying the data step"
        )
    engine = sa.create_engine(settings.database_url_sync, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            actions = scan_rewrap_actions(connection)
            webhook_actions = scan_webhook_rewrap_actions(connection)
        pending = len(actions) + len(webhook_actions)
        print(f"tenant credential key={active_key_id}; pending={pending}")
        if not apply:
            return 1 if pending else 0
        if not pending:
            return 0

        with engine.begin() as connection:
            apply_rewrap_actions(connection, actions)
            apply_webhook_rewrap_actions(connection, webhook_actions)

        with engine.connect() as connection:
            remaining = scan_rewrap_actions(connection)
            remaining_webhooks = scan_webhook_rewrap_actions(connection)
        remaining_count = len(remaining) + len(remaining_webhooks)
        if remaining_count:
            print(
                f"credential rewrap needs another pass; pending={remaining_count}",
                file=sys.stderr,
            )
            return 3
        print(f"tenant credentials rewrapped={pending}")
        return 0
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the preflighted compare-and-swap updates; default is read-only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(apply=args.apply)
    except (SecretKeyConfigurationError, CredentialRewrapError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except SQLAlchemyError:
        # SQLAlchemy exception strings may include bound ciphertext. The
        # operator gets a stable code without putting credentials in logs.
        print(
            "credential rewrap database operation failed; transaction rolled back", file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

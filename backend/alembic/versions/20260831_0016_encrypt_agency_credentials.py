"""Mark the expand/contract rollout for tenant credential encryption.

Revision ID: 20260831_0016
Revises: 20260801_0015

This revision intentionally performs no data rewrite. Rewriting the complete
``agencies.settings`` JSON inside Alembic loses concurrent preference changes,
cannot be represented by ``alembic --sql`` and exposes ciphertext to old app
instances during a rolling deploy. After every process can read v1/v2, run
``python -m app.scripts.rewrap_tenant_credentials --apply``. That command
requires the explicit tenant keyring and updates one credential key at a time
with compare-and-swap protection.
"""

from __future__ import annotations

revision: str = "20260831_0016"
down_revision: str | None = "20260801_0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Code-first expansion; the separate online command owns the data step."""


def downgrade() -> None:
    """No schema or data was changed by this revision."""

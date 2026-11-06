"""inbound dedup: partial unique index on messages(agency_id, external_message_id)

Revision ID: 20260725_0012
Revises: 20260725_0011
Create Date: 2026-07-25

Item 173. Meta and Resend retry a webhook whose response was slow or lost,
so the same provider message id can arrive twice. The route short-circuits
on the duplicate before running the agent, but that check races with itself
under concurrency (two workers, two replicas): the index is what makes the
guarantee, turning a second insert into an IntegrityError instead of a
second answer to the lead.

Partial on two conditions:

- `external_message_id IS NOT NULL`, because most rows have no provider id
  and NULLs would not collide anyway;
- `direction = 'inbound'`, because outbound rows store whatever the sending
  client returned and an unconfigured client returns the literal
  "dev-stub" (app/channels/email/client.py, app/channels/whatsapp/client.py).
  A direction-blind unique index would make the second follow-up ever sent
  on such a deployment fail with an IntegrityError — a rule about inbound
  retries breaking the outbound path.

Existing duplicates would make CREATE UNIQUE INDEX fail, so they are
disarmed first by clearing the id of every inbound copy but the oldest.
Nothing is deleted: the message stays, only its (now ambiguous) provider id
is dropped. Outbound rows are left alone — the index does not cover them.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260725_0012"
down_revision: str | None = "20260725_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_messages_agency_external_id"


def upgrade() -> None:
    op.execute(
        """
        UPDATE messages AS m
           SET external_message_id = NULL
         WHERE m.external_message_id IS NOT NULL
           AND m.direction = 'inbound'
           AND EXISTS (
                 SELECT 1
                   FROM messages AS older
                  WHERE older.agency_id = m.agency_id
                    AND older.external_message_id = m.external_message_id
                    AND older.direction = 'inbound'
                    AND (older.created_at, older.id) < (m.created_at, m.id)
               )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
            ON messages (agency_id, external_message_id)
         WHERE external_message_id IS NOT NULL AND direction = 'inbound'
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")

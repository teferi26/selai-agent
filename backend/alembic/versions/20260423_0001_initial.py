"""initial schema

Revision ID: 20260423_0001
Revises:
Create Date: 2026-04-23

Creates the full initial schema in one shot so the pilot can start clean.
Subsequent changes should live in their own migrations.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260423_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    property_type = postgresql.ENUM(
        "apartment", "house", "duplex", "studio", "penthouse", "office", "commercial", "land",
        name="propertytype", create_type=False,
    )
    property_status = postgresql.ENUM(
        "draft", "available", "reserved", "sold", "rented", "archived",
        name="propertystatus", create_type=False,
    )
    lead_status = postgresql.ENUM(
        "new", "engaged", "qualified", "visit_scheduled", "visited",
        "negotiating", "won", "lost", "dormant",
        name="leadstatus", create_type=False,
    )
    lead_temperature = postgresql.ENUM("hot", "warm", "cold", name="leadtemperature", create_type=False)
    conv_channel = postgresql.ENUM(
        "whatsapp", "email", "voice", "sms", "web", name="conversationchannel", create_type=False,
    )
    conv_status = postgresql.ENUM(
        "active", "snoozed", "handed_off", "closed", name="conversationstatus", create_type=False,
    )
    msg_direction = postgresql.ENUM("inbound", "outbound", name="messagedirection", create_type=False)
    msg_role = postgresql.ENUM("lead", "agent_ai", "agent_human", "system", name="messagerole", create_type=False)
    visit_status = postgresql.ENUM(
        "requested", "confirmed", "rescheduled", "cancelled", "no_show", "completed",
        name="visitstatus", create_type=False,
    )
    user_role = postgresql.ENUM("owner", "agent", "admin", name="userrole", create_type=False)

    for enum_name, values in [
        ("propertytype", ("apartment", "house", "duplex", "studio", "penthouse", "office", "commercial", "land")),
        ("propertystatus", ("draft", "available", "reserved", "sold", "rented", "archived")),
        ("leadstatus", ("new", "engaged", "qualified", "visit_scheduled", "visited", "negotiating", "won", "lost", "dormant")),
        ("leadtemperature", ("hot", "warm", "cold")),
        ("conversationchannel", ("whatsapp", "email", "voice", "sms", "web")),
        ("conversationstatus", ("active", "snoozed", "handed_off", "closed")),
        ("messagedirection", ("inbound", "outbound")),
        ("messagerole", ("lead", "agent_ai", "agent_human", "system")),
        ("visitstatus", ("requested", "confirmed", "rescheduled", "cancelled", "no_show", "completed")),
        ("userrole", ("owner", "agent", "admin")),
    ]:
        op.execute(f"DO $$ BEGIN CREATE TYPE {enum_name} AS ENUM ({', '.join(repr(v) for v in values)}); EXCEPTION WHEN duplicate_object THEN null; END $$")

    op.create_table(
        "agencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("contact_email", sa.String(255)),
        sa.Column("contact_phone", sa.String(40)),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"),
        sa.Column("default_language", sa.String(8), nullable=False, server_default="es"),
        sa.Column("settings", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_agencies_slug", "agencies", ["slug"])
    op.create_index("ix_agencies_slug", "agencies", ["slug"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="agent"),
        sa.Column("phone", sa.String(40)),
        sa.Column("google_calendar_id", sa.String(255)),
        sa.Column("google_refresh_token", sa.String(512)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_agency_id", "users", ["agency_id"])

    op.create_table(
        "properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("property_type", property_type, nullable=False),
        sa.Column("status", property_status, nullable=False, server_default="available"),
        sa.Column("operation", sa.String(16), nullable=False, server_default="sale"),
        sa.Column("price", sa.Integer, nullable=False),
        sa.Column("price_per_sqm", sa.Integer),
        sa.Column("address", sa.String(512), nullable=False, server_default=""),
        sa.Column("neighborhood", sa.String(128)),
        sa.Column("city", sa.String(128), nullable=False),
        sa.Column("postal_code", sa.String(16)),
        sa.Column("country", sa.String(2), nullable=False, server_default="ES"),
        sa.Column("latitude", sa.Numeric(10, 7)),
        sa.Column("longitude", sa.Numeric(10, 7)),
        sa.Column("bedrooms", sa.Integer),
        sa.Column("bathrooms", sa.Integer),
        sa.Column("area_sqm", sa.Integer),
        sa.Column("year_built", sa.Integer),
        sa.Column("floor", sa.String(16)),
        sa.Column("energy_rating", sa.String(4)),
        sa.Column("has_elevator", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_parking", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_terrace", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_pool", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_ac", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_heating", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("furnished", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("pet_friendly", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("image_urls", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("video_url", sa.String(512)),
        sa.Column("virtual_tour_url", sa.String(512)),
        sa.Column("extra", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("embedding", Vector(1024)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_properties_agency_id", "properties", ["agency_id"])
    op.create_index("ix_properties_reference", "properties", ["reference"])
    op.create_index("ix_properties_city", "properties", ["city"])

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("phone_e164", sa.String(32)),
        sa.Column("email", sa.String(255)),
        sa.Column("whatsapp_wa_id", sa.String(64)),
        sa.Column("status", lead_status, nullable=False, server_default="new"),
        sa.Column("temperature", lead_temperature, nullable=False, server_default="cold"),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("budget_min", sa.Integer),
        sa.Column("budget_max", sa.Integer),
        sa.Column("preferred_cities", sa.JSON),
        sa.Column("preferred_neighborhoods", sa.JSON),
        sa.Column("min_bedrooms", sa.Integer),
        sa.Column("min_bathrooms", sa.Integer),
        sa.Column("min_area_sqm", sa.Integer),
        sa.Column("operation", sa.String(16)),
        sa.Column("must_haves", sa.JSON),
        sa.Column("deal_breakers", sa.JSON),
        sa.Column("qualification_notes", sa.Text, nullable=False, server_default=""),
        sa.Column("detected_objections", sa.JSON),
        sa.Column("interested_property_ids", sa.JSON),
        sa.Column("source_channel", sa.String(32)),
        sa.Column("source_campaign", sa.String(128)),
        sa.Column("consent_marketing", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("language", sa.String(8), nullable=False, server_default="es"),
        sa.Column("extra", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_leads_agency_id", "leads", ["agency_id"])
    op.create_index("ix_leads_phone_e164", "leads", ["phone_e164"])
    op.create_index("ix_leads_email", "leads", ["email"])
    op.create_index("ix_leads_whatsapp_wa_id", "leads", ["whatsapp_wa_id"])
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_temperature", "leads", ["temperature"])

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", conv_channel, nullable=False),
        sa.Column("status", conv_status, nullable=False, server_default="active"),
        sa.Column("external_thread_id", sa.String(255)),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversations_agency_id", "conversations", ["agency_id"])
    op.create_index("ix_conversations_lead_id", "conversations", ["lead_id"])
    op.create_index("ix_conversations_channel", "conversations", ["channel"])
    op.create_index("ix_conversations_status", "conversations", ["status"])
    op.create_index("ix_conversations_external_thread_id", "conversations", ["external_thread_id"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", msg_direction, nullable=False),
        sa.Column("role", msg_role, nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("raw", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("external_message_id", sa.String(255)),
        sa.Column("media", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_messages_agency_id", "messages", ["agency_id"])
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_external_message_id", "messages", ["external_message_id"])

    op.create_table(
        "visits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False, server_default="30"),
        sa.Column("status", visit_status, nullable=False, server_default="requested"),
        sa.Column("calendar_event_id", sa.String(255)),
        sa.Column("pre_visit_briefing", sa.Text, nullable=False, server_default=""),
        sa.Column("post_visit_notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_visits_agency_id", "visits", ["agency_id"])
    op.create_index("ix_visits_lead_id", "visits", ["lead_id"])
    op.create_index("ix_visits_property_id", "visits", ["property_id"])
    op.create_index("ix_visits_status", "visits", ["status"])

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("output_text", sa.Text, nullable=False, server_default=""),
        sa.Column("tool_calls", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_runs_agency_id", "agent_runs", ["agency_id"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_lead_id", "agent_runs", ["lead_id"])


def downgrade() -> None:
    for t in (
        "agent_runs", "visits", "messages", "conversations",
        "leads", "properties", "users", "agencies",
    ):
        op.drop_table(t)
    for enum_name in (
        "userrole", "visitstatus", "messagerole", "messagedirection",
        "conversationstatus", "conversationchannel",
        "leadtemperature", "leadstatus",
        "propertystatus", "propertytype",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

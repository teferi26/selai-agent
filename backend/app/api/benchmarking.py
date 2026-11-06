"""Anonymous cross-agency benchmarking.

Returns the requesting agency's metric value alongside the percentile
within all agencies. Aggregates exclude the requester's data when
computing the percentile so they don't see themselves as the median.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep
from app.db.models import (
    Conversation,
    ConversationStatus,
    Lead,
    LeadStatus,
    Message,
    MessageRole,
    Visit,
    VisitStatus,
)
from app.db.session import get_db
from app.services.plan_limits import ensure_report_tier

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/benchmarks",
    tags=["benchmarks"],
    dependencies=[AgencyMemberDep],
)

# Criterion for this endpoint, and why it is a tier and not a flag:
#
# seed_plans.py models reports as `informes`, a *string* tier — "básicos" for
# Starter, "completos" for Pro and Agencia — so `ensure_feature("informes")`
# would let Starter through (any non-empty string is truthy). The enforceable
# line the plan data actually draws is therefore "completos", which is what
# this endpoint requires: Starter is refused, Pro and Agencia pass.
#
# Known gap, deliberately not papered over here: the pricing table sells
# "Informes y comparativa con otras agencias" as Agencia-only, so Pro is
# currently one tier more generous than the page promises. Closing it needs a
# dedicated flag on the `agencia` plan (seed_plans.py belongs to the billing
# track); the moment that flag exists this constant becomes that check. Not
# guessed from `voice_cloning`/`marketplace`: reusing an unrelated Agencia
# flag would silently break the day a plan carries one and not the other.
MIN_REPORT_TIER = "completos"


# (n6) Fewer than MIN_SAMPLE other agencies and the "percentiles" are the
# exact figures of identifiable competitors — with one other agency, p25 =
# p50 = p75 = THEIR number. Below the floor the percentiles are null, and
# above it they are rounded to two significant figures: a percentile IS some
# concrete agency's raw value, and blurring it is what makes the promise of
# "datos anónimos" true.
MIN_SAMPLE = 5


def _blur(value: float) -> float:
    """Round to 2 significant figures (147 -> 150, 8.31 -> 8.3, 0 -> 0)."""
    if value == 0:
        return 0.0
    from math import floor, log10

    digits = 1 - int(floor(log10(abs(value))))
    return round(value, digits)


class Benchmark(BaseModel):
    metric: str
    you: float
    p25: float | None
    p50: float | None
    p75: float | None
    sample_size: int
    higher_is_better: bool


class BenchmarksOut(BaseModel):
    range_days: int
    benchmarks: list[Benchmark]


@router.get("", response_model=BenchmarksOut)
async def get_benchmarks(
    agency_id: uuid.UUID,
    range_days: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
) -> BenchmarksOut:
    await ensure_report_tier(db, agency_id, MIN_REPORT_TIER)

    since = datetime.now(UTC) - timedelta(days=range_days)
    benchmarks: list[Benchmark] = []

    # Helper that computes per-agency value + percentiles.
    async def per_agency(
        select_stmt,
        agency_col,
        metric: str,
        higher_is_better: bool,
    ) -> Benchmark:
        rows = (await db.execute(select_stmt)).all()
        per_ag = {row[0]: float(row[1] or 0) for row in rows}
        you = per_ag.get(agency_id, 0.0)
        others = sorted(v for k, v in per_ag.items() if k != agency_id)
        n = len(others)
        if n < MIN_SAMPLE:
            # Sin muestra suficiente no hay percentiles: los habría de
            # agencias concretas y reconocibles.
            return Benchmark(
                metric=metric, you=you, p25=None, p50=None, p75=None,
                sample_size=n, higher_is_better=higher_is_better,
            )
        return Benchmark(
            metric=metric,
            you=you,
            p25=_blur(others[int(n * 0.25)]),
            p50=_blur(others[int(n * 0.50)]),
            p75=_blur(others[int(n * 0.75)]),
            sample_size=n,
            higher_is_better=higher_is_better,
        )

    # Leads_per_day
    leads_stmt = (
        select(Lead.agency_id, func.count(Lead.id))
        .where(Lead.created_at >= since)
        .group_by(Lead.agency_id)
    )
    benchmarks.append(await per_agency(leads_stmt, Lead.agency_id, "leads_total", True))

    # Visit conversion = visits / leads
    visits_stmt = (
        select(Visit.agency_id, func.count(Visit.id))
        .where(Visit.created_at >= since)
        .group_by(Visit.agency_id)
    )
    benchmarks.append(await per_agency(visits_stmt, Visit.agency_id, "visits_total", True))

    # Won leads
    won_stmt = (
        select(Lead.agency_id, func.count(Lead.id))
        .where(Lead.created_at >= since, Lead.status == LeadStatus.WON)
        .group_by(Lead.agency_id)
    )
    benchmarks.append(await per_agency(won_stmt, Lead.agency_id, "won_leads", True))

    # Avg first-response time per agency (lower is better).
    inbound_subq = (
        select(
            Message.conversation_id.label("c"),
            func.min(Message.created_at).label("first_in"),
            Message.agency_id.label("ag"),
        )
        .where(Message.role == MessageRole.LEAD, Message.created_at >= since)
        .group_by(Message.conversation_id, Message.agency_id)
        .subquery()
    )
    out_subq = (
        select(
            Message.conversation_id.label("c"),
            func.min(Message.created_at).label("first_out"),
        )
        .where(
            Message.role.in_([MessageRole.AGENT_AI, MessageRole.AGENT_HUMAN]),
            Message.created_at >= since,
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    resp_stmt = (
        select(
            inbound_subq.c.ag,
            func.avg(func.extract("epoch", out_subq.c.first_out - inbound_subq.c.first_in)),
        )
        .join(out_subq, out_subq.c.c == inbound_subq.c.c)
        .group_by(inbound_subq.c.ag)
    )
    benchmarks.append(
        await per_agency(resp_stmt, inbound_subq.c.ag, "avg_response_seconds", False)
    )

    # Suppress unused warnings
    _ = (ConversationStatus, VisitStatus, Conversation)

    return BenchmarksOut(range_days=range_days, benchmarks=benchmarks)

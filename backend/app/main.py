"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    agency_settings,
    benchmarking,
    billing,
    channels,
    dashboard,
    devices,
    email,
    files,
    gdpr,
    google_business,
    google_calendar,
    health,
    invitations,
    legal,
    marketplace,
    me,
    offers,
    offices,
    onboarding,
    properties,
    sms,
    support,
    templates,
    visits,
    voice,
    voice_cloning,
    voice_outbound,
    voice_settings,
    web_chat,
    webhooks_admin,
    whatsapp,
)
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.core.settings import settings, verify_production_secrets
from app.db import events as _db_events  # noqa: F401  (register listeners)

configure_logging()
log = get_logger("app")

if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
        send_default_pii=False,  # never ship lead PII to Sentry
    )
    log.info("sentry.enabled", env=settings.app_env)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to serve production on a placeholder SECRET_KEY (item 229). It is
    # what authenticates the Vapi and inbound-email webhooks, so the default
    # value means anyone can inject fake calls and fake leads into any agency.
    # Failing at startup is loud; the alternative fails silently, in the dark.
    verify_production_secrets(settings)
    log.info("app.starting", env=settings.app_env)
    yield
    log.info("app.stopping")


app = FastAPI(
    title="Realtor AI API",
    version="0.1.0",
    lifespan=lifespan,
    # No API browser in production. /docs and /openapi.json enumerate every
    # route, schema and header the backend accepts — a free reconnaissance map
    # for anyone probing api.selai.es. Development keeps them.
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
# The public status surface polls health endpoints from the browser, and the
# web-chat widget will POST from arbitrary agency sites — both need CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.selai.es",
        "https://selai.es",
        "http://localhost:3000",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(me.router)
app.include_router(onboarding.router)
app.include_router(billing.router)
app.include_router(benchmarking.router)
app.include_router(marketplace.router)
app.include_router(webhooks_admin.router)
app.include_router(whatsapp.router)
app.include_router(email.router)
app.include_router(sms.router)
app.include_router(web_chat.router)
app.include_router(google_business.router)
app.include_router(voice.router)
app.include_router(voice.webhook_router)
app.include_router(voice_outbound.router)
app.include_router(gdpr.router)
app.include_router(legal.router)
app.include_router(dashboard.router)
app.include_router(templates.router)
app.include_router(offers.router)
app.include_router(visits.router)
app.include_router(properties.router)
app.include_router(voice_cloning.router)
app.include_router(files.router)
app.include_router(agency_settings.router)
app.include_router(channels.router)
app.include_router(voice_settings.catalog_router)
app.include_router(voice_settings.router)
app.include_router(devices.router)
app.include_router(support.router)
# Team invitations: the agency-scoped CRUD, the members listing, and the
# public pair (preview + accept) that a person who has no agency *yet* has to
# be able to reach — that is the whole point of an invitation.
app.include_router(invitations.router)
app.include_router(invitations.members_router)
app.include_router(invitations.public_router)
app.include_router(offices.router)
# Google Calendar: the agency-scoped connect/disconnect, plus the OAuth
# callback Google redirects to, which carries its tenant in a signed `state`
# instead of a path parameter.
app.include_router(google_calendar.router)
app.include_router(google_calendar.callback_router)


@app.get("/")
async def root() -> dict[str, str]:
    # No "docs" pointer: in production there is nothing to point at, and the
    # root response is public.
    return {
        "name": "Realtor AI API",
        "env": settings.app_env,
    }

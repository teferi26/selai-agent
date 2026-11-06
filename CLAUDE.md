# Realtor AI

SaaS multi-tenant que dota a inmobiliarias de un agente IA capaz de atender WhatsApp, email y llamadas de voz, cualificar leads (hace de "setter"), agendar visitas y devolver toda la información al agente humano.

## Estado
Arrancado el 2026-04-23. Hay una inmobiliaria piloto comprometida.

## Stack
- **Backend**: Python 3.12 + FastAPI (async). SQLAlchemy 2 + Alembic. Pydantic v2.
- **DB**: PostgreSQL 16 + pgvector. Redis para cache + colas.
- **LLM**: Anthropic Claude (Opus para razonamiento de setter, Haiku para respuestas rápidas).
- **WhatsApp**: Meta WhatsApp Business Cloud API.
- **Email**: Resend (saliente). IMAP / webhook (entrante).
- **Voz** (Fase 2): Vapi o LiveKit Agents + Deepgram (STT) + Cartesia/ElevenLabs (TTS).
- **Calendario**: Google Calendar API.
- **Dashboard**: Next.js 14 App Router + shadcn/ui + Tailwind. Auth con Clerk.
- **Async jobs**: Celery + Redis.
- **Deploy**: Docker Compose local. Railway/Fly.io producción inicial.

## Arquitectura (vista lógica)

```
Canales (WA / Email / Voz) ──► Channel Adapter ──► Agent Core ──► Tools ──► DB
                                                       │
                                                       ▼
                                                  Audit Log
```

- **Multi-tenant**: cada `Agency` es un tenant. `agency_id` en TODAS las queries.
- **Channel-agnostic**: la lógica del agente no sabe si la entrada vino de WhatsApp o llamada. Cada canal tiene un adapter que normaliza a `InboundMessage`.
- **Tool use**: el agente usa tools de Claude (`search_properties`, `schedule_visit`, `save_lead_info`, `escalate_to_human`, `send_property_card`).
- **Memoria**: histórico de conversación + perfil persistente del lead (preferencias, presupuesto, objeciones).

## Estructura del repo

```
backend/        # FastAPI + agente + adapters + workers
dashboard/      # Next.js (vendrá después)
infra/          # docker-compose, scripts deploy
docs/           # docs internas (no las generes sin pedirlo)
```

## Convenciones
- Idioma del código: inglés. Idioma de UX y prompts: español por defecto, multi-idioma habilitado.
- No introducir LangChain/LlamaIndex. SDK de Anthropic directo.
- Toda query a DB filtra por `agency_id`. Si no, es bug.
- Toda llamada al LLM se loguea en `agent_runs` con tokens, latencia y resultado.

## Comandos comunes
```bash
# Levantar entorno
docker compose up -d

# Migraciones
cd backend && alembic upgrade head

# Tests
cd backend && pytest

# Sembrar datos demo
cd backend && python -m app.scripts.seed_demo
```

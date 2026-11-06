# Realtor AI Dashboard

Next.js 15 + Tailwind. Habla con la API FastAPI vía rewrite (`/api/backend/*`).

## Setup

```bash
cd dashboard
cp .env.example .env.local
# rellena NEXT_PUBLIC_DEFAULT_AGENCY_ID con el UUID de tu agencia (lo ves en la DB
# tras correr `python -m app.scripts.seed_demo`)
npm install
npm run dev
```

Abre <http://localhost:3000>.

## Páginas

- `/inbox` — bandeja unificada con priorización por temperatura del lead.
- `/conversations/[id]` — histórico de mensajes.
- `/leads` — tabla de leads con filtros básicos.
- `/kpis` — métricas de los últimos 30 días.

## Pendiente para próximo sprint

- Auth (Clerk) y selector de agencia.
- Acción de "responder como humano" en `/conversations/[id]`.
- Acción de handoff con elección de agente.
- Vista de propiedades (alta/edición + fotos).
- Vista de visitas con briefing IA visible.
- Tiempo real (SSE o WebSocket) para que la bandeja se refresque sola.

# Realtor AI

> Original Spanish README (historical, as of the Sprint 5 era). The current English overview is [../README.md](../README.md).

Agente IA multi-canal para inmobiliarias. Atiende **WhatsApp, email y llamadas de voz** en castellano, inglés y francés. Hace de **setter**: cualifica al lead, agenda la visita y le entrega al agente humano un briefing completo.

## Estado actual

Sprints 1, 2 y 3 entregados. WhatsApp, email, SMS, voz (Vapi) y web chat operativos. Brain
unificado de Claude para todos los canales. Dashboard con bandeja, leads, KPIs, funnel,
ofertas, llamadas y plantillas. Pendiente: Sprint 4 (features defensivos / wow).

## Estructura

```
backend/       FastAPI + agente Claude + adapters WhatsApp/email/voz + workers
dashboard/     Next.js (placeholder)
infra/         Docker Compose, Dockerfile
docs/          Docs internas
```

## Arranque rápido (local)

1. **Requisitos**: Docker Desktop, Python 3.12 (para `alembic`/`seed` fuera de Docker si prefieres).
2. **Configurar entorno**:
   ```bash
   cp backend/.env.example backend/.env
   # Edita backend/.env y pon al menos ANTHROPIC_API_KEY.
   ```
3. **Levantar servicios**:
   ```bash
   cd infra
   docker compose up -d postgres redis
   ```
4. **Migrar y sembrar**:
   ```bash
   cd backend
   pip install -e ".[dev]"
   alembic upgrade head
   python -m app.scripts.seed_demo
   ```
5. **Arrancar API**:
   ```bash
   uvicorn app.main:app --reload
   ```
6. **Comprobar**: <http://localhost:8000/docs>

## Arrancar también el dashboard

```bash
cd dashboard
cp .env.example .env.local
# pega el UUID de la agencia (lo verás en la DB tras `seed_demo`)
npm install
npm run dev
```

Abrir <http://localhost:3000>.

## Conectar WhatsApp Business Cloud API

1. Crea una App en <https://developers.facebook.com> de tipo "Business" y activa el producto "WhatsApp".
2. Apunta el webhook a `https://TU_DOMINIO/webhooks/whatsapp`. En local usa `ngrok http 8000`.
3. `WHATSAPP_VERIFY_TOKEN`: inventa uno y repítelo en la consola de Meta.
4. `WHATSAPP_APP_SECRET`: del apartado "App Secret" de Meta.
5. `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID`: del número de prueba o del número productivo.
6. Suscribe el webhook al campo `messages`.

## Conectar email entrante

Apunta tu proveedor de email (Postmark inbound, SendGrid Inbound Parse, Cloudflare Email Worker)
al endpoint `POST /webhooks/email` con un header `X-Webhook-Secret` igual a `SECRET_KEY` y un
JSON con los campos `From`, `To`, `Subject`, `MessageID`, `InReplyTo`, `References`,
`TextBody`, `HtmlBody`, `StrippedTextReply`. La agencia se resuelve por el local-part del `To`
(`<slug>-leads@inbound.tu-dominio.com` → slug "piloto").

## Conectar Google Calendar

1. Crea OAuth2 client (tipo Web) en Google Cloud Console y rellena `GOOGLE_OAUTH_*` en `.env`.
2. Por ahora actualiza manualmente `users.google_refresh_token` y `users.google_calendar_id`
   tras el flujo OAuth — endpoint OAuth listo en próximo sprint.

## Tests

```bash
cd backend
pytest
```

En Windows, `verify.ps1` ejecuta backend, operativa comercial, panel, móvil y
un smoke de liveness sin tocar la base de datos por defecto:

```powershell
.\verify.ps1
```

Los pasos mutantes son opt-in y quedan etiquetados en la salida:

```powershell
.\verify.ps1 -BootstrapDependencies -WithLocalServices -ApplyMigrations
```

Una migración a un host no local se bloquea salvo que se añada deliberadamente
`-AllowRemoteDatabaseMutation` después de revisar el destino.

## Sprint 1 — completo ✅

- [x] WhatsApp Business 24/7
- [x] Email entrante con threading + envío Resend
- [x] Detección automática de idioma (langdetect)
- [x] Búsqueda semántica con embeddings Voyage (pgvector)
- [x] Google Calendar sync en `schedule_visit`
- [x] Celery Beat: recordatorios 24h+1h, briefing pre-visita IA
- [x] GDPR: consent log, export, erase
- [x] Dashboard Next.js: bandeja, leads, conversación, KPIs
- [x] Warm handoff API
- [x] Migraciones Alembic + tests críticos

## Sprint 2 — completo ✅

- [x] Lead scoring 0-100 explicable + `lead.extra.score_breakdown` (item 18)
- [x] Memoria cross-canal en el system prompt del agente (item 25)
- [x] "Por qué te encajaría" generado por IA + tool `send_property_card` (items 36, 37)
- [x] Calculadoras hipoteca + gastos por CCAA (tools del agente + servicios) (items 39, 40)
- [x] Plantillas de mensaje con placeholders + endpoints CRUD + página dashboard (item 75)
- [x] Modelo `Offer` + endpoints + PDF firmable (WeasyPrint) + página dashboard (item 65)
- [x] Ficha de propiedad PDF personalizada por lead (WeasyPrint + storage S3-ready) (item 36)
- [x] Histórico de precios en `Property` + alertas automáticas a leads matcheados (item 45)
- [x] Solicitud reseña Google automática 24h tras `LeadStatus.WON` (item 69)
- [x] Endpoint voice-note + Deepgram + estructuración Claude (item 55)
- [x] Endpoint `/funnel` + página visual con tasa de paso por etapa (item 82)
- [x] Migración Alembic + tests críticos

## Sprint 3 — completo ✅

- [x] Voz entrante con Vapi: endpoint `/voice/llm` OpenAI-compatible que delega en nuestro agente (item 3)
- [x] Webhooks Vapi: call.start, status-update, end-of-call-report → persiste transcript completo (item 3)
- [x] Llamadas salientes proactivas a leads dormidos (Celery diario 17:00 UTC) + endpoint manual (item 4)
- [x] Llamada perdida → WhatsApp automático (con fallback a SMS) (item 5)
- [x] Canal SMS Twilio: cliente + webhook con verificación HMAC + fallback (item 6)
- [x] Widget chat web vanilla JS embebible en cualquier site (item 9)
- [x] Parser Idealista/Fotocasa/Habitaclia que extrae lead real del email del portal (item 12)
- [x] Google Business Profile: scaffold de canal + webhook (item 14)
- [x] Detección no-show (Celery cada 15 min) + reagendamiento automático (item 56)
- [x] Página dashboard de llamadas

## Cómo conectar la voz (Vapi)

1. Crea cuenta en <https://vapi.ai>, obtén API key.
2. Crea o importa un Phone Number (puedes traerlo de Twilio).
3. Crea un **Assistant**:
   - Voice: Cartesia o ElevenLabs (ES/EN/FR).
   - Transcriber: Deepgram Nova-3.
   - Model: **custom-llm**, URL = `https://TU_DOMINIO/voice/llm`.
   - Server URL (webhooks) = `https://TU_DOMINIO/webhooks/voice`.
4. Copia el `assistantId` y `phoneNumberId` a:
   - `.env`: `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`.
   - DB: `agencies.settings.vapi_assistant_id`.
5. Llama al número del assistant — habla con el setter IA.

## Cómo embeber el widget web

```html
<script src="https://TU_DOMINIO/webhooks/web/widget.js"
        data-slug="piloto"></script>
```

Aparece un botón flotante 💬 en la esquina inferior derecha.

## Cómo conectar SMS (Twilio)

1. Compra un número en Twilio Console.
2. `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.
3. En el número, "Messaging > A MESSAGE COMES IN" → `https://TU_DOMINIO/webhooks/sms` (POST).
4. Mapea agencia añadiendo `agencies.settings.twilio_sms_number = "+34..."`.

## Cómo capturar leads de Idealista / Fotocasa / Habitaclia

Configura en cada portal el email de notificaciones de leads para que vaya a tu inbox de
captación (`piloto-leads@inbound.tu-dominio.com`). El parser detecta el dominio remitente
(`*.idealista.com`, `*.fotocasa.es`, `*.habitaclia.com`) y reescribe el `From` para que el
lead que se cree apunte al cliente real, no al `noreply` del portal.

## Sprint 4 — completo ✅

- [x] Generación de descripción de pisos desde fotos con Claude multimodal + auditoría (highlights/weaknesses) (item 103)
- [x] Routing geográfico: asigna el agente humano más cercano al piso (haversine) (item 49)
- [x] Detección de competidor espiando: heurística + clasificador Claude Haiku, marca lead y restringe info (item 21)
- [x] Modo "guiri": detector por idioma + intención + bloque de prompt con NIE / hipoteca no residentes (item 109)
- [x] Few-shot por agencia: extrae conversaciones de leads WON, refresca a diario por Celery, las inyecta en el prompt (proxy item 110)
- [x] Voice cloning ElevenLabs: endpoint con consentimiento explícito, guarda voice_id en agency settings (item 101)
- [x] Pre-aprobación hipoteca: tool del agente + scaffold de integración Trioteca con deep link (item 106)
- [x] Clerk auth: middleware JWT con JWKS + dependency en routers de dashboard
- [x] Dashboard PWA: manifest + service worker + viewport mobile (proxy lite item 80)
- [x] Página de catálogo en dashboard con botón "Generar descripción IA"

## Cómo usar lo nuevo

**Revisar una descripción de piso generada desde fotos** (dashboard `/properties` →
"Describir con IA"). La generación crea un borrador recuperable y nunca modifica la
descripción publicada. El contrato separa expresamente generación, edición y aprobación:

```bash
# 1. Crear borrador (devuelve un `id`; un borrador activo requiere confirmación
#    explícita con `replace_existing_draft=true` para regenerarse).
curl -X POST https://API/api/v1/agencies/$AGENCY/properties/$PROP/describe-from-photos \
  -d '{"target_language": "es"}'

# 2. Guardar exactamente el texto revisado, sin volver a invocar al modelo.
curl -X PUT https://API/api/v1/agencies/$AGENCY/properties/$PROP/description-draft/$DRAFT \
  -d '{"description": "Texto revisado"}'

# 3. Aplicar exactamente ese texto (solo owner/admin). La respuesta incluye
#    `undo_id`; los reintentos idénticos son seguros.
curl -X PUT https://API/api/v1/agencies/$AGENCY/properties/$PROP/description \
  -d '{"draft_id": "'$DRAFT'", "description": "Texto revisado"}'

# Cancelar solo elimina el borrador; no toca la descripción publicada.
curl -X DELETE https://API/api/v1/agencies/$AGENCY/properties/$PROP/description-draft/$DRAFT
```

El backend conserva hasta tres versiones previas en el JSON existente de la propiedad,
sin migración, y solo permite restaurar si nadie ha editado después la descripción.

**Voice cloning del top performer**:
```bash
curl -X POST https://API/api/v1/agencies/$AGENCY/voice-cloning \
  -F "name=María, top vendedora" \
  -F "consent_signed=true" \
  -F "files=@muestra1.mp3"
# Devuelve voice_id → configúralo en el assistant Vapi.
```

**Routing geográfico — set agent home base**:
```sql
UPDATE users SET home_lat=40.4168, home_lng=-3.7038 WHERE email='owner@piloto.test';
```

## Sprint 5 — completo ✅

- [x] **Stripe billing**: modelos Plan + Subscription, /billing/plans, /billing/checkout, webhook con verificación HMAC
- [x] **Onboarding self-service**: POST /api/v1/onboarding crea agencia + owner + suscripción trial 14d + provisiona Vapi assistant via API
- [x] **Outgoing webhooks**: modelo WebhookSubscription + dispatcher + delivery Celery firmado HMAC, auto-disable a 20 fallos
- [x] **Marketplace de leads inter-agencias**: publicar (con consentimiento GDPR previo) + claim copia el lead al buyer (item 105)
- [x] **Benchmarking anónimo**: /agencies/{id}/benchmarks devuelve tu valor + p25/p50/p75 entre todas las agencias del SaaS (item 91)
- [x] **Idealista Partner API scaffold**: cliente OAuth client_credentials + fetch_recent_leads
- [x] **Hardening SOC2-ready**: middleware security headers + rate limit por IP+ruta
- [x] **App móvil Expo**: scaffold con login, bandeja, grabador de notas de voz post-visita (item 80)

## Cómo usar lo nuevo del Sprint 5

```bash
# Onboarding self-service (sin auth, abierto al público)
curl -X POST https://API/api/v1/onboarding -d '{
  "agency_name":"Inmobiliaria Sevilla","owner_email":"...","owner_full_name":"..."
}'

# Listar planes
curl https://API/api/v1/billing/plans

# Suscribirse a webhooks salientes (notifica a tu CRM)
curl -X POST https://API/api/v1/agencies/$AG/webhook-subscriptions \
  -d '{"target_url":"https://mi-crm.es/in","events":["lead.created","visit.scheduled"]}'

# Tu posición vs el resto del SaaS
curl https://API/api/v1/agencies/$AG/benchmarks?range_days=30

# Marketplace
curl -X POST https://API/api/v1/marketplace/publish \
  -d '{"lead_id":"...","asking_fee_eur":50,"commission_pct":15}'
```

**App móvil**: ver [mobile/README.md](../mobile/README.md).

## Sprint 6 — operaciones reales (no más features)

El producto ya está. Toca convertirlo en negocio:

- [ ] Lanzamiento real con la inmobiliaria piloto + métricas de uso
- [ ] Iteración basada en feedback real
- [ ] Landing pública + signup
- [ ] Documentación para integradores (CRMs)
- [ ] Sentry + OpenTelemetry + dashboards Grafana
- [ ] Backups DB + procedimiento DR
- [ ] CI/CD GitHub Actions → Fly.io / Railway
- [ ] SOC 2 Type II audit kick-off

Ver [docs/ROADMAP.md](ROADMAP.md) para el plan completo a 100 mejoras.

## Comandos útiles

```bash
# Nueva migración
alembic revision --autogenerate -m "descripcion"

# Tests
pytest -xvs

# Shell SQL
docker compose exec postgres psql -U realtor -d realtor
```

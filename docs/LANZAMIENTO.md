# Guía de lanzamiento — selai.es

Paso a paso para poner selai en producción. Cada paso está marcado:
**[TÚ]** = solo puedes hacerlo tú (cuentas, claves, pagos).
**[YO]** = lo hace Claude cuando le avises de que el paso anterior está listo.

Las claves se pegan siempre en archivos `.env` locales o en el panel de
variables de Railway/Vercel — **nunca en el chat ni en git** (están
ignorados por `.gitignore`).

---

## Ya hecho ✅

- Repo git local con auth multi-tenant, billing, onboarding, UI completa.
- `seed_plans.py` (Starter 99€ / Pro 199€ / Agencia 399€) y
  `sync_stripe_plans.py` (crea productos y precios en Stripe solo).
- `backend/Dockerfile` de producción + `railway.json` (healthcheck, reintentos).
- CI de GitHub Actions (lint + tests backend, typecheck + build dashboard).
- Sentry integrado (solo falta el DSN).
- Dominio **selai.es** comprado.

## Paso 0 — Identidad legal del prestador · [TÚ + ASESORÍA]

Antes de producción y antes de cobrar, confirma la entidad que presta selai y completa en Railway:

```
LEGAL_COMPANY_NAME=<razón social completa>
LEGAL_COMPANY_VAT=<NIF o VAT>
LEGAL_COMPANY_ADDRESS=<domicilio social completo>
LEGAL_COMPANY_CITY=<ciudad de jurisdicción>
LEGAL_DPO_EMAIL=<email legal o de privacidad operativo>
LEGAL_REVIEW_CONFIRMED=true
```

Términos, DPA, Política de Privacidad y la orden de pedido B2B (documento comercial no incluido en esta copia pública) deben quedar revisados por asesoría y sin datos pendientes antes de la primera venta. Pon `LEGAL_REVIEW_CONFIRMED=true` solo después de esa revisión. No pongas datos privados en el código ni en el chat. El backend de producción se niega a arrancar si falta la identidad o no se ha confirmado la revisión.

---

## Paso 1 — GitHub · [TÚ: 2 min] → [YO]

1. Abre un terminal y ejecuta:
   ```
   gh auth login
   ```
   Elige: GitHub.com → HTTPS → Login with a web browser.
2. Avísame. **[YO]**: creo el repo privado `selai`, subo los commits y la CI
   arranca sola en cada push.

## Paso 2 — Clerk (login de usuarios) · [TÚ: 5 min]

1. Crea cuenta en <https://clerk.com> → **Create application**, nombre
   "selai", métodos: Email + Google.
2. En **Configure → API Keys** copia y pega:
   - `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` y `CLERK_SECRET_KEY` →
     `dashboard/.env.local`
   - El **JWKS URL** (botón "Show JWKS URL", termina en
     `/.well-known/jwks.json`) → `CLERK_JWKS_URL` en `backend/.env`
3. En **Paths** pon: Sign-in `/sign-in`, Sign-up `/sign-up`.

## Paso 3 — Stripe (cobros) · [TÚ: 5 min] → [YO]

1. Cuenta en <https://stripe.com> (configura primero **modo test** y repite el proceso completo en **modo live** antes de vender).
2. **Developers → API keys** → usa `sk_test_...` únicamente en desarrollo.
   Para Railway/producción copia la clave live `sk_live_...` →
   `STRIPE_SECRET_KEY`; el backend rechazará una clave de test.
3. Avísame. **[YO]**: ejecuto `seed_plans` + `sync_stripe_plans` — los 3
   productos con sus precios aparecen solos en tu Stripe y quedan
   contratables desde `/billing`.
4. En **Billing → Customer portal**, guarda una configuración live y fija en ella los métodos de pago, facturas, cambios de plan y cancelación que vas a ofrecer. Copia su ID `bpc_...` a `STRIPE_BILLING_PORTAL_CONFIGURATION_ID`; así un cambio posterior del portal por defecto no altera contratos activos.
5. Antes de captar: sincronizar también los tres precios anuales en live, configurar el webhook live y completar una compra real de importe mínimo. Debe aparecer una factura pagada en Stripe y la suscripción `active` en selai; una compra en test no valida cobros reales.
6. Con asesoría fiscal, determina el tratamiento de IVA aplicable y comprueba que los precios de Stripe (`tax_behavior`) conservan el importe final anunciado. Pon `STRIPE_TAX_SETUP_CONFIRMED=true` únicamente tras revisar una factura live correcta; el backend de producción queda bloqueado hasta entonces.

## Paso 4 — Railway (backend en producción) · [TÚ: 10 min]

1. Cuenta en <https://railway.app> (login con GitHub) → **New Project →
   Deploy from GitHub repo** → `selai`. En el servicio: **Settings → Root
   Directory = `backend`** (detecta el Dockerfile y `railway.json` solos).
2. En el mismo proyecto: **+ New → Database → PostgreSQL** y **Redis**.
3. Variables del servicio backend (pestaña Variables):
   ```
   APP_ENV=production
   SECRET_KEY=<cadena aleatoria larga y estable>
   TENANT_CREDENTIAL_ACTIVE_KEY_ID=<identificador de la clave activa, por ejemplo 2026-09>
   TENANT_CREDENTIAL_KEYS={"<identificador>":"<clave Fernet>"}
   TENANT_CREDENTIAL_WRITE_MODE=v2
   TENANT_CREDENTIAL_PLAINTEXT_WRITE_ACK=false
   LEGAL_COMPANY_NAME=<paso 0>
   LEGAL_COMPANY_VAT=<paso 0>
   LEGAL_COMPANY_ADDRESS=<paso 0>
   LEGAL_COMPANY_CITY=<paso 0>
   LEGAL_DPO_EMAIL=<paso 0>
   LEGAL_REVIEW_CONFIRMED=true
   DATABASE_URL=postgresql+asyncpg://<usuario>:<pass>@<host>:<puerto>/<db>
   REDIS_URL=${{Redis.REDIS_URL}}
   ANTHROPIC_API_KEY=<paso 7>
   CLERK_JWKS_URL=<paso 2>
   STRIPE_SECRET_KEY=<sk_live_... del paso 3>
   STRIPE_WEBHOOK_SECRET=<paso 6>
   STRIPE_BILLING_PORTAL_CONFIGURATION_ID=<bpc_... del paso 3>
   STRIPE_TAX_SETUP_CONFIRMED=true
   PUBLIC_BASE_URL=https://api.selai.es
   S3_BUCKET=<bucket privado y persistente>
   S3_REGION=eu-west-1
   S3_ENDPOINT_URL=<endpoint S3 compatible, vacío si usas AWS>
   AWS_ACCESS_KEY_ID=<credencial limitada al bucket>
   AWS_SECRET_ACCESS_KEY=<secreto limitado al bucket>
   SENTRY_DSN=<paso 8, opcional>
   ```
   Guarda `SECRET_KEY` y el keyring tenant en el gestor de secretos. `SECRET_KEY`
   firma integraciones globales y permite leer sobres legacy `enc:v1`; las nuevas
   credenciales privadas de WhatsApp, Twilio, Google Calendar y las claves de firma
   de webhooks CRM se cifran con `TENANT_CREDENTIAL_KEYS`. No retires una clave del
   keyring ni sustituyas `SECRET_KEY` mientras queden sobres antiguos. Sigue
   `docs/ROTACION-CREDENCIALES-TENANT.md` para el primer despliegue, el recifrado y
   cualquier rotación.
   `S3_BUCKET` es obligatorio en producción: fichas y ofertas PDF pueden
   contener datos personales, `/files` está cerrado en producción y el disco
   del contenedor se pierde al redesplegar. Usa un bucket privado S3-compatible
   y concede solo lectura, escritura y borrado sobre ese bucket.
   ⚠️ `DATABASE_URL`: Railway la da como `postgresql://...` — cámbiale el
   prefijo a `postgresql+asyncpg://` (el resto igual).
4. **Settings → Networking → Custom Domain** → `api.selai.es` → Railway te
   da un CNAME (lo usarás en el paso 5).
5. Workers (cuando actives recordatorios/llamadas): **+ New → GitHub repo**
   (mismo repo, root `backend`) dos veces, cambiando el *Start Command*:
   - worker: `celery -A app.workers.celery_app worker --loglevel=info`
   - beat: `celery -A app.workers.celery_app beat --loglevel=info`
6. Con todas las API y workers ya capaces de leer `enc:v2`, ejecuta el preflight
   y el recifrado descritos en `docs/ROTACION-CREDENCIALES-TENANT.md`. No retires
   el modo de compatibilidad ni una clave anterior hasta obtener `pending=0`.

### Contrato de release en Railway

`backend/railway.json` ejecuta `python -m app.scripts.release` como
**Pre-Deploy Command**. Ese job valida primero la configuración de producción,
toma un advisory lock en PostgreSQL y, en orden, aplica Alembic, siembra planes,
sincroniza Stripe y mantiene la demo pública. Si cualquier paso falla, Railway
no promociona la imagen nueva. Configura en Railway un timeout de pre-deploy de
10 minutos; el lock interno deja de esperar a otro release tras 60 segundos.

El `CMD` del contenedor solo arranca Uvicorn. Reiniciar o escalar réplicas ya no
aplica migraciones ni llama a Stripe. Railway promociona una versión solo si
`/readyz` confirma PostgreSQL; `/healthz` se mantiene como liveness trivial para
monitores de proceso. No conviertas `/healthz` en una consulta de dependencias.

Los servicios worker/beat creados desde la misma raíz heredan la configuración
como código. Sus seeds son idempotentes y el lock evita carreras, pero para no
repetir el release configura esos servicios con su propio archivo Railway sin
`preDeployCommand`, o desactiva ese paso en sus ajustes de servicio. El servicio
web es el único propietario del release.

> Nota de plataforma (septiembre de 2026): Railway mantiene `railway.json`
> para servicios existentes solo hasta el 1 de diciembre de 2026 y los
> servicios nuevos ya usan Infrastructure as Code. Si hay que recrear el
> backend, configura en el servicio los mismos valores (`pre-deploy`,
> `/readyz`, timeout y restart policy), ejecuta `railway config pull` para
> generar `.railway/railway.ts` una vez vinculado el proyecto y revisa el plan
> antes de aplicarlo. No se puede generar ese archivo correctamente sin el
> proyecto/servicios reales de Railway.

## Paso 5 — DNS de selai.es · [TÚ o YO según dónde lo compraste]

- `selai.es` (raíz) y `www` → al proyecto de Vercel (el dashboard ya está
  vinculado al proyecto `selai` de Vercel). Si lo compraste **en Vercel**,
  avísame y lo conecto yo al proyecto; si fue en otro registrador, añade
  los registros que Vercel te indique en **Project → Domains**.
- `api.selai.es` → CNAME al dominio que te dio Railway (paso 4.4).
- En Vercel, **Project → Settings → Environment Variables**:
  ```
  BACKEND_URL=https://api.selai.es
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<paso 2>
  CLERK_SECRET_KEY=<paso 2>
  NEXT_PUBLIC_SITE_URL=https://selai.es
  ```

## Paso 6 — Webhook de Stripe · [TÚ: 3 min, después del paso 4]

**Developers → Webhooks → Add endpoint**:
- URL: `https://api.selai.es/api/v1/billing/webhook`
- Eventos: `checkout.session.completed`, `customer.subscription.updated`,
  `customer.subscription.deleted`, `invoice.payment_failed`
- Copia el *Signing secret* (`whsec_...`) → variable
  `STRIPE_WEBHOOK_SECRET` en Railway.

## Paso 7 — Anthropic API key · [TÚ: 2 min]

<https://console.anthropic.com> → API Keys → crear → `ANTHROPIC_API_KEY`
en Railway. Sin ella el agente no responde (los canales quedan en stub).

## Paso 8 — Sentry · [TÚ: 3 min, recomendado]

Cuenta gratis en <https://sentry.io> → proyecto **FastAPI** → copia el DSN
→ `SENTRY_DSN` en Railway. Los errores de producción te llegarán solos.

---

## Después del lanzamiento

- **WhatsApp Business Cloud API** (Meta): el canal estrella — requiere
  verificación de negocio en Meta, empezar cuanto antes (tarda días).
- **Voz** (Vapi + número), **SMS** (Twilio), **email saliente** (Resend +
  dominio verificado selai.es), **Deepgram** (transcripción).
- Comprobar cada mañana que Stripe live, webhook, precios anuales y email transaccional siguen operativos.

## Orden recomendado

0 (identidad legal) → 1 (GitHub) → 2 (Clerk) → 3 (Stripe test y live) →
4 (Railway) → 5 (DNS) → 6 (webhook test y live) → 7 (Anthropic) → 8 (Sentry).
Solo con 0-7 en verde, documentos revisados y una compra live verificada se abre la captación.

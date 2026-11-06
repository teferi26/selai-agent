# selai mobile (Expo)

App móvil para los agentes humanos de la inmobiliaria: bandeja unificada,
leads y métricas desde el bolsillo.

## Pantallas

- **/** Login: Clerk (email + contraseña) o fallback dev con `X-Dev-Email`.
- **/(tabs)/inbox** Bandeja unificada con filtro por canal.
- **/(tabs)/leads** Leads ordenados por score, con filtros de temperatura.
- **/(tabs)/metrics** KPIs de los últimos 30 días.
- **/conversation/[id]** Historial de la conversación + "Tomar el control".
- **/lead/[id]** Ficha del lead con acciones (llamar, WhatsApp, llamada IA).
- **/visit/[id]** Grabar nota de voz post-visita y subirla.

## Arranque

```bash
cd mobile
npm install
cp .env.example .env   # rellena EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY
npx expo start
```

Sin `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY` la app entra en modo dev y se
autentica contra el backend con el header `X-Dev-Email`. Este fallback solo
existe con `EXPO_PUBLIC_APP_ENV=development` y en una ejecución de desarrollo;
una app preview o de producción nunca envía ese header.

## Clerk en EAS

Las claves de Clerk no se guardan en `app.json` ni en `eas.json`. Configúralas
en los entornos del proyecto EAS antes de compilar:

```bash
eas env:set --environment preview --name EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY --value <pk_test_...> --visibility sensitive
eas env:set --environment production --name EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY --value <pk_live_...> --visibility sensitive
eas env:list --environment preview
eas env:list --environment production
```

Una clave publicable forma parte del binario y puede ser leída por quien use
la app; `sensitive` evita versionarla y oculta su valor en los logs. Una build
`preview` falla sin `pk_test_` y una build `production` falla sin `pk_live_`.

## Verificación

```bash
npm run typecheck
npm run validate:auth-config
```

## Pendiente (siguiente fase)

- Push notifications (lead caliente, recordatorio visita)
- Agenda integrada
- Asignar visita a otro compañero desde móvil
- Modo offline (cache de inbox)

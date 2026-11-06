/**
 * Instrumentación de Next en el navegador (Next >= 15.3 carga este fichero
 * automáticamente). Sin NEXT_PUBLIC_SENTRY_DSN no se inicializa nada y no sale
 * ninguna petición del navegador.
 */
import * as Sentry from "@sentry/nextjs";

import { initSentry } from "@/lib/monitoring";

initSentry();

// Marca el inicio de cada navegación del App Router. Sin cliente inicializado
// no hace nada, pero Next espera encontrarlo exportado aquí.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;

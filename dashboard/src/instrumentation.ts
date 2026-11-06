/**
 * Instrumentación de Next en servidor y edge.
 *
 * `initSentry()` es no-op sin NEXT_PUBLIC_SENTRY_DSN, así que este fichero es
 * inofensivo cuando Sentry no está configurado. Un único `init` sirve para los
 * dos runtimes: @sentry/nextjs resuelve su build (node o edge) por sí solo.
 */
import * as Sentry from "@sentry/nextjs";

import { initSentry } from "@/lib/monitoring";

export function register(): void {
  initSentry();
}

// Hook que Next llama con los errores de render en servidor (los mismos que
// acaban enseñando (app)/error.tsx). Sin cliente Sentry inicializado no envía.
export const onRequestError = Sentry.captureRequestError;

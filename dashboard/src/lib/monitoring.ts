/**
 * Sentry, apagado por defecto.
 *
 * Mismo criterio que el backend (backend/app/main.py): sin DSN no se inicializa
 * nada, así que el SDK se queda en no-op y del navegador no sale ni una
 * petición. Además nunca se activa en desarrollo — los fallos de local no
 * deben ensuciar el proyecto de producción ni gastar su cuota.
 *
 * Variable: NEXT_PUBLIC_SENTRY_DSN (ver .env.example).
 */
import * as Sentry from "@sentry/nextjs";

const DSN = process.env.NEXT_PUBLIC_SENTRY_DSN ?? "";

/** True solo con DSN configurado y en build de producción. */
export const sentryEnabled = DSN !== "" && process.env.NODE_ENV === "production";

const options = {
  dsn: DSN,
  // En Vercel distinguimos production / preview; fuera de Vercel vale NODE_ENV.
  environment: process.env.NEXT_PUBLIC_VERCEL_ENV || process.env.NODE_ENV,
  tracesSampleRate: 0.1,
  // En el panel se ven leads reales (nombres, teléfonos, correos). Nada de eso
  // tiene por qué acabar en un tercero, así que ni cabeceras ni cookies ni IP.
  sendDefaultPii: false,
};

/** Arranca el SDK. No hace nada si falta el DSN o estamos en desarrollo. */
export function initSentry(): void {
  if (!sentryEnabled) return;
  Sentry.init(options);
}

/**
 * Manda un error a Sentry si está configurado; si no, no hace nada.
 * Las pantallas de error lo llaman sin preguntar por el entorno.
 *
 * Devuelve el id del evento (null cuando Sentry está apagado) para que el
 * formulario de soporte pueda mandarlo en `error_ref`: sin él, el ticket del
 * usuario y la traza del fallo quedan en dos sitios sin nada que los una.
 */
export function captureError(
  error: unknown,
  extra?: Record<string, string | null | undefined>
): string | null {
  if (!sentryEnabled) return null;
  return Sentry.captureException(error, extra ? { extra } : undefined);
}

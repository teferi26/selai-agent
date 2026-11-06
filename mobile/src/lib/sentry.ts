// ---------------------------------------------------------------------------
// Telemetría de errores (Sentry).
//
// Sin DSN configurado este módulo es un no-op completo: ni siquiera se carga
// el SDK, no se abre ninguna conexión y `captureFatal` devuelve null. Esa es
// la situación por defecto. Para que reporte, define EXPO_PUBLIC_SENTRY_DSN como
// variable de entorno de EAS (preview y production); app.config.js la copia a
// `extra.sentryDsn`. El DSN no se versiona en git.
//
// Reglas que este archivo garantiza:
//   - Desactivado en desarrollo (__DEV__): los errores de dev ya se ven en la
//     consola y ensuciarían el proyecto de Sentry.
//   - Sin PII: no enviamos usuario, ni capturas de pantalla, ni jerarquía de
//     vistas, ni logs de consola (pueden llevar nombres de leads o texto de
//     conversaciones).
// ---------------------------------------------------------------------------
import Constants from "expo-constants";
// Solo los tipos: `import type` desaparece al compilar. El SDK se carga con
// require() más abajo, y únicamente si hay DSN — ver loadSentry().
import type * as SentryModule from "@sentry/react-native";

// Mismo patrón de respaldo que la clave de Clerk en src/lib/auth.ts: la
// variable se incrusta en tiempo de compilación, pero mobile/.env no viaja en
// el archivo de build de EAS. `app.json` -> `extra` sí se empaqueta siempre.
export const SENTRY_DSN =
  process.env.EXPO_PUBLIC_SENTRY_DSN ??
  (Constants.expoConfig?.extra?.sentryDsn as string | undefined) ??
  "";

/** True solo si hay DSN y no estamos en desarrollo. */
export const sentryEnabled = SENTRY_DSN.length > 0 && !__DEV__;

let initialized = false;
let sentry: typeof SentryModule | null = null;

/**
 * Carga el SDK bajo demanda.
 *
 * Importarlo arriba con `import` lo metería en el arranque en frío de todos
 * los usuarios aunque no haya DSN, y sobre todo lo metería en el módulo
 * app/_layout.tsx: si ese módulo falla al cargarse no hay ErrorBoundary que
 * valga, porque el boundary es precisamente uno de sus exports. Cargándolo
 * aquí, dentro de un try, un módulo nativo ausente (por ejemplo una
 * actualización OTA que llega a un binario sin Sentry enlazado) apaga la
 * telemetría en vez de tumbar la app.
 */
function loadSentry(): typeof SentryModule | null {
  if (sentry) return sentry;
  try {
    sentry = require("@sentry/react-native") as typeof SentryModule;
  } catch {
    sentry = null;
  }
  return sentry;
}

/**
 * Arranca Sentry. Idempotente y silencioso: si falla, la app sigue.
 * Se llama una sola vez desde app/_layout.tsx.
 */
export function initSentry(): void {
  if (initialized || !sentryEnabled) return;
  initialized = true;
  try {
    const Sentry = loadSentry();
    if (!Sentry) {
      initialized = false;
      return;
    }
    Sentry.init({
      dsn: SENTRY_DSN,
      debug: false,
      environment: (Constants.expoConfig?.extra?.sentryEnvironment as string) || "production",
      // --- privacidad ---
      sendDefaultPii: false,
      attachScreenshot: false,
      attachViewHierarchy: false,
      enableCaptureFailedRequests: false,
      // --- alcance: solo errores, nada de rendimiento ni sesiones ---
      enableAutoPerformanceTracing: false,
      enableAutoSessionTracking: false,
      maxBreadcrumbs: 30,
      beforeSend(event) {
        // Aunque sendDefaultPii sea false, nos aseguramos de que nada que
        // identifique a la persona o al dispositivo salga del teléfono.
        delete event.user;
        delete event.server_name;
        const device = event.contexts?.device;
        if (device) delete device.name;
        return event;
      },
      beforeBreadcrumb(breadcrumb) {
        // Los logs de consola pueden arrastrar nombres de leads o mensajes.
        if (breadcrumb.category === "console") return null;
        // De las peticiones guardamos la ruta, no la query.
        if (typeof breadcrumb.data?.url === "string") {
          breadcrumb.data.url = breadcrumb.data.url.split("?")[0];
        }
        return breadcrumb;
      },
    });
  } catch {
    // La telemetría nunca puede tumbar el arranque de la app.
    initialized = false;
  }
}

/**
 * Registra un error grave y devuelve el id del evento, que usamos como
 * `error_ref` en el formulario de soporte para poder cruzar el mensaje del
 * usuario con la traza.
 *
 * Devuelve null cuando Sentry está apagado (que hoy es siempre): en ese caso
 * el formulario de soporte manda una referencia derivada del propio error.
 */
export function captureFatal(error: unknown, screen: string): string | null {
  if (!sentryEnabled || !initialized) return null;
  try {
    return loadSentry()?.captureException(error, { tags: { screen } }) ?? null;
  } catch {
    return null;
  }
}

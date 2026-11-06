// ---------------------------------------------------------------------------
// Soporte: envío del formulario "Reportar un problema" y respaldo por correo.
//
// El endpoint solo exige sesión iniciada (no pertenencia a agencia) porque
// este formulario tiene que funcionar precisamente cuando algo está roto.
// Aun así puede fallar (sin cobertura, sesión caducada, backend caído), y por
// eso `supportMailtoUrl` construye el mismo contexto en un mailto: el usuario
// nunca se queda sin vía para contarnos el problema.
// ---------------------------------------------------------------------------
import * as Application from "expo-application";
import Constants from "expo-constants";
import { api } from "@/lib/api";

export const SUPPORT_EMAIL = "hola@selai.es";

/** Límites del contrato del endpoint (backend valida lo mismo). */
export const MESSAGE_MIN = 5;
export const MESSAGE_MAX = 4000;
const SCREEN_MAX = 200;
const APP_VERSION_MAX = 40;
const ERROR_REF_MAX = 200;
const CONTACT_EMAIL_MAX = 200;

export type SupportRequest = {
  message: string;
  surface: "web" | "mobile";
  screen: string | null;
  app_version: string | null;
  error_ref: string | null;
  contact_email: string | null;
};

/** "1.0.0 (42)" — versión declarada en app.json + build nativo instalado. */
export function appVersionLabel(): string {
  const version = Constants.expoConfig?.version ?? "?";
  const build = Application.nativeBuildVersion ?? "dev";
  return `${version} (${build})`.slice(0, APP_VERSION_MAX);
}

function trimOrNull(value: string | null | undefined, max: number): string | null {
  const s = (value ?? "").trim();
  return s ? s.slice(0, max) : null;
}

/**
 * Referencia técnica para `error_ref` cuando no hay id de Sentry: nombre y
 * mensaje del error, recortados. No es un identificador único, pero permite
 * agrupar informes del mismo fallo al leerlos.
 */
export function errorReference(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof Error) {
    return trimOrNull(`${error.name}: ${error.message}`, ERROR_REF_MAX);
  }
  return trimOrNull(String(error), ERROR_REF_MAX);
}

export function buildSupportRequest(input: {
  message: string;
  screen: string;
  errorRef?: string | null;
  contactEmail?: string | null;
}): SupportRequest {
  return {
    message: input.message.trim().slice(0, MESSAGE_MAX),
    surface: "mobile",
    screen: trimOrNull(input.screen, SCREEN_MAX),
    app_version: appVersionLabel(),
    error_ref: trimOrNull(input.errorRef, ERROR_REF_MAX),
    contact_email: trimOrNull(input.contactEmail, CONTACT_EMAIL_MAX),
  };
}

/** POST /api/v1/support. Responde {"ok": true} o lanza ApiError. */
export function sendSupportRequest(payload: SupportRequest): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>("/api/v1/support", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export const SUPPORT_SUBJECT = "Problema en la app de selai";

/**
 * El informe en texto plano: lo que ha escrito el usuario más el contexto que
 * nosotros sabemos. Es el cuerpo del mailto y también lo que se copia al
 * portapapeles si ni el correo se abre, para que su texto no se pierda nunca.
 */
export function supportPlainText(payload: SupportRequest): string {
  const lines = [
    payload.message,
    "",
    "---",
    "Datos que nos ayudan a localizarlo (no los borres):",
    `Aplicación: móvil ${payload.app_version ?? "?"}`,
  ];
  if (payload.screen) lines.push(`Pantalla: ${payload.screen}`);
  if (payload.error_ref) lines.push(`Referencia: ${payload.error_ref}`);
  return lines.join("\n");
}

/**
 * mailto con el mismo contexto ya escrito, para cuando la petición no sale.
 * El usuario solo tiene que darle a enviar.
 */
export function supportMailtoUrl(payload: SupportRequest): string {
  return `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(
    SUPPORT_SUBJECT
  )}&body=${encodeURIComponent(supportPlainText(payload))}`;
}

/**
 * mailto para los "escríbenos a hola@selai.es" repartidos por la app.
 *
 * No usa el formulario de `SupportModal` a propósito: esos textos aparecen en
 * sitios donde el usuario está a medias de otra cosa (un paso del asistente,
 * un error de importación) y abrir un modal encima le haría perderlo. Aquí
 * solo se le abre el correo con el asunto y el contexto ya puestos.
 *
 * `context` es la pantalla o el paso ("Paso 3 · Teléfono de tu IA"): viaja en
 * el cuerpo para que el mensaje nos llegue ya situado.
 */
export function contextMailtoUrl(context?: string | null): string {
  const lines = [
    "",
    "---",
    "Datos que nos ayudan a localizarlo (no los borres):",
    `Aplicación: móvil ${appVersionLabel()}`,
  ];
  if (context) lines.push(`Pantalla: ${context}`);
  return `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(
    SUPPORT_SUBJECT
  )}&body=${encodeURIComponent(lines.join("\n"))}`;
}

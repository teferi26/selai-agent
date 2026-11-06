/**
 * Contrato del formulario de soporte — POST /api/v1/support.
 *
 * Va por el proxy autenticado del panel (`api()` antepone /api/backend), que
 * es quien pone el JWT de Clerk. El endpoint solo pide usuario autenticado:
 * no exige agencia, precisamente para que el formulario siga sirviendo cuando
 * lo que está roto es la agencia del usuario.
 *
 * Límites replicados aquí porque el backend los valida y un 422 en el peor
 * momento (justo cuando algo ya ha fallado) no ayuda a nadie.
 */
import { api } from "@/lib/api";

export const SUPPORT_MESSAGE_MIN = 5;
export const SUPPORT_MESSAGE_MAX = 4000;
const SCREEN_MAX = 200;
const ERROR_REF_MAX = 200;
const CONTACT_EMAIL_MAX = 200;

/** Correo publicado en la web (404 y /ayuda). Es la salida cuando el POST falla. */
export const SUPPORT_EMAIL = "hola@selai.es";

/** Vercel expone el commit desplegado cuando el proyecto tiene activadas las
 * variables de sistema. Si no está, mandamos null en lugar de inventar una
 * versión que no significaría nada. */
const COMMIT = process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA;
export const APP_VERSION: string | null = COMMIT ? `web-${COMMIT.slice(0, 7)}` : null;

type SupportPayload = {
  message: string;
  surface: "web";
  screen: string | null;
  app_version: string | null;
  error_ref: string | null;
  contact_email: string | null;
};

export type SupportInput = {
  message: string;
  /** Ruta donde estaba el usuario (usePathname). */
  screen?: string | null;
  /** Digest de Next o id de Sentry cuando venimos de una pantalla de error. */
  errorRef?: string | null;
  /** Correo alternativo para la respuesta. */
  contactEmail?: string | null;
};

function clamp(value: string | null | undefined, max: number): string | null {
  const trimmed = (value ?? "").trim();
  return trimmed ? trimmed.slice(0, max) : null;
}

export async function sendSupportRequest(input: SupportInput): Promise<void> {
  const payload: SupportPayload = {
    message: input.message.trim().slice(0, SUPPORT_MESSAGE_MAX),
    surface: "web",
    screen: clamp(input.screen, SCREEN_MAX),
    app_version: APP_VERSION,
    error_ref: clamp(input.errorRef, ERROR_REF_MAX),
    contact_email: clamp(input.contactEmail, CONTACT_EMAIL_MAX),
  };
  await api<{ ok: boolean }>("/api/v1/support", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** `api()` mete el status en el mensaje del Error ("API 401: ..."). */
export function statusOf(err: unknown): number | null {
  const match = err instanceof Error ? /^API (\d{3}):/.exec(err.message) : null;
  return match ? Number(match[1]) : null;
}

export const SUPPORT_SUBJECT = "Problema en el panel de selai";

/**
 * El mensaje del usuario más el contexto que habríamos mandado nosotros.
 * Es lo que se lleva cuando el POST no sale: sirve igual para el mailto y para
 * el portapapeles.
 */
export function buildSupportText(input: SupportInput): string {
  const contactEmail = clamp(input.contactEmail, CONTACT_EMAIL_MAX);
  return [
    input.message.trim(),
    "",
    "---",
    input.screen ? `Pantalla: ${input.screen}` : null,
    input.errorRef ? `Código del fallo: ${input.errorRef}` : null,
    APP_VERSION ? `Versión: ${APP_VERSION}` : null,
    // Si pidió respuesta en otra dirección, viaja también: el correo saldrá
    // desde su cuenta habitual y si no lo decimos aquí se pierde el dato.
    contactEmail ? `Responder a: ${contactEmail}` : null,
  ]
    .filter((line): line is string => line !== null)
    .join("\n");
}

/**
 * Enlace mailto con lo que ya había escrito, para cuando el envío no sale.
 *
 * Ojo: varios clientes de correo recortan los mailto largos (Outlook alrededor
 * de los 2000 caracteres) y en un escritorio sin cliente configurado el enlace
 * no hace nada. Por eso el diálogo ofrece además copiar el texto y deja el
 * formulario a la vista: el mailto es la vía cómoda, no la única.
 */
export function buildSupportMailto(input: SupportInput): string {
  const subject = encodeURIComponent(SUPPORT_SUBJECT);
  const body = encodeURIComponent(buildSupportText(input));
  return `mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}`;
}

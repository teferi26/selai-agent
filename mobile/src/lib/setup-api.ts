// ---------------------------------------------------------------------------
// API surface consumed by the native "Puesta en marcha" wizard.
//
// Every call here EXECUTES a real connection (the app used to only report
// progress and send the owner to a desktop). Contracts verified against:
//   backend/app/api/agency_settings.py   PATCH settings
//   backend/app/api/voice_settings.py    GET /voice/catalog, PUT /voice, POST /voice/preview
//   backend/app/api/channels.py          GET/POST/DELETE channels + whatsapp + phone + webchat
//   backend/app/api/webhooks_admin.py    webhook-subscriptions CRUD + test
//   backend/app/api/properties.py        POST /properties/import
//   backend/app/api/web_chat.py          POST /webhooks/web/{slug}/message
//
// Auth is the existing one: everything goes through api()/apiUpload()/apiFetch()
// from lib/api.ts, which carry the Clerk bearer token (or the X-Dev-Email
// fallback) registered by ClerkTokenBridge.
// ---------------------------------------------------------------------------
import { File, Paths } from "expo-file-system";
import { ApiError, api, apiFetch, apiUpload } from "@/lib/api";

// ---------------------------------------------------------------------------
// Ceilings per endpoint.
//
// lib/api.ts stops every request at 30 s so no spinner can turn forever. Four
// of these calls legitimately take longer, because a third party is on the
// other end and the backend already waits for it: cutting them at 30 s would
// abort work that was about to succeed. Each number is that backend wait plus
// room for a slow mobile connection.
// ---------------------------------------------------------------------------

/** ElevenLabs synthesis: the backend gives it 30 s. */
const PREVIEW_TIMEOUT_MS = 60_000;
/** Graph verification (15 s) + the subscribed_apps call (10 s). */
const WHATSAPP_TIMEOUT_MS = 45_000;
/** Vapi provisioning (20 s) plus the fallback email. */
const PHONE_TIMEOUT_MS = 60_000;
/** The website check: up to 4 hops at 10 s each (channels.py). */
const WEBCHAT_VERIFY_TIMEOUT_MS = 60_000;
/** A real delivery to the customer's CRM, made inline. */
const WEBHOOK_TEST_TIMEOUT_MS = 45_000;
/** The assistant has to think and answer — an LLM round trip. */
const CHAT_TIMEOUT_MS = 90_000;

// ---------------------------------------------------------------------------
// Settings (paso 1 + persistencia del progreso)
// ---------------------------------------------------------------------------

// El PATCH de settings vive en `lib/api.ts`, junto a su GET: lo comparten el
// asistente y la pantalla de comportamiento de la IA, y esta última no debería
// tener que importar este módulo (que arrastra subida de ficheros y audio)
// solo para guardar un ajuste. Se re-exporta con el nombre que ya usaba el
// asistente para no tocar sus pasos.
export { patchAgencySettings } from "@/lib/api";
export type { AgencySettingsPatch as SettingsPatch } from "@/lib/api";

/** The settings blob exposes arbitrary keys as `unknown`; these two read the
 * wizard's fields without lying about their type. */
export function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function asTextList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

// ---------------------------------------------------------------------------
// Voz (paso 2)
// ---------------------------------------------------------------------------

export type VoiceCatalogItem = {
  id: string;
  provider: string;
  name: string;
  language: string;
  gender: string;
  description: string;
  preview_url?: string | null;
};

/** Public catalog (no agency in the path). */
export function fetchVoiceCatalog(): Promise<VoiceCatalogItem[]> {
  return api<VoiceCatalogItem[]>("/api/v1/voice/catalog");
}

export function setAgencyVoice(
  agencyId: string,
  body: { provider: string; voice_id: string; voice_name: string }
): Promise<{ ok: boolean; applied_to_vapi: boolean }> {
  return api(`/api/v1/agencies/${agencyId}/voice`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** Only ElevenLabs voices can be previewed today (the backend answers 501 for
 * anything else). Raised so the UI can say so instead of showing "error". */
export class PreviewUnavailableError extends Error {
  constructor() {
    super("preview_unavailable");
    this.name = "PreviewUnavailableError";
  }
}

export const VOICE_PREVIEW_TEXT =
  "Hola, soy el asistente de tu inmobiliaria. ¿En qué puedo ayudarte?";

/**
 * Downloads a voice sample to the cache directory and returns its file URI.
 *
 * The endpoint answers with raw `audio/mpeg` bytes over an authenticated
 * POST, so the authenticated bytes are downloaded first and then supplied to
 * expo-audio as a local file source.
 */
export async function downloadVoicePreview(
  agencyId: string,
  provider: string,
  voiceId: string,
  text: string = VOICE_PREVIEW_TEXT
): Promise<string> {
  const res = await apiFetch(
    `/api/v1/agencies/${agencyId}/voice/preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, voice_id: voiceId, text: text.slice(0, 300) }),
    },
    PREVIEW_TIMEOUT_MS
  );
  if (res.status === 501) throw new PreviewUnavailableError();
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || `HTTP ${res.status}`);
  }

  // Blob → base64 → fichero: es el único camino que React Native soporta de
  // punta a punta para un cuerpo binario (arrayBuffer() no está garantizado).
  // La muestra son unos pocos KB, así que el sobrecoste no se nota.
  const base64 = await responseToBase64(res);

  // Un fichero por voz, reescrito en cada escucha: la caché puede vaciarse
  // sola y volver a escuchar nunca debe reproducir la voz anterior.
  // `overwrite` en vez de borrar-y-crear: dos escuchas seguidas de la misma
  // voz no pueden chocar entre el borrado y la creación.
  const file = new File(Paths.cache, `selai-voz-${voiceId}.mp3`);
  file.create({ overwrite: true, intermediates: true });
  file.write(base64, { encoding: "base64" });
  return file.uri;
}

async function responseToBase64(res: Response): Promise<string> {
  const blob = await res.blob();
  const dataUrl: string = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("read_failed"));
    reader.onload = () => resolve(String(reader.result));
    reader.readAsDataURL(blob);
  });
  const comma = dataUrl.indexOf(",");
  return comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
}

// ---------------------------------------------------------------------------
// Canales (pasos 3, 4, 5, 7)
// ---------------------------------------------------------------------------

export type PhoneState = "none" | "requested" | "active";

export type ChannelsStatus = {
  whatsapp: {
    connected: boolean;
    phone_number: string | null;
    verified_name: string | null;
    webhook_callback_url: string;
    webhook_verify_token: string;
    subscribed_apps: boolean;
    app_secret_set: boolean;
  };
  email: {
    address: string;
    received_count: number;
    last_received_at: string | null;
  };
  phone: { number: string | null; status: PhoneState };
  webchat: {
    slug: string;
    verified: boolean;
    verified_url: string | null;
    verified_at: string | null;
  };
  webhooks: { count: number };
};

export function fetchChannels(agencyId: string): Promise<ChannelsStatus> {
  return api<ChannelsStatus>(`/api/v1/agencies/${agencyId}/channels`);
}

export type WhatsAppConnectOut = {
  connected: boolean;
  phone_number: string | null;
  verified_name: string | null;
  subscribed_apps: boolean;
  app_secret_set: boolean;
  webhook_callback_url: string;
  webhook_verify_token: string;
};

export function connectWhatsApp(
  agencyId: string,
  body: {
    phone_number_id: string;
    waba_id: string | null;
    access_token: string;
    app_secret: string | null;
  }
): Promise<WhatsAppConnectOut> {
  return api<WhatsAppConnectOut>(
    `/api/v1/agencies/${agencyId}/channels/whatsapp`,
    { method: "POST", body: JSON.stringify(body) },
    WHATSAPP_TIMEOUT_MS
  );
}

export function sendWhatsAppTest(
  agencyId: string,
  toPhone: string
): Promise<{ sent: boolean; to_phone: string; message: string }> {
  return api(
    `/api/v1/agencies/${agencyId}/channels/whatsapp/test`,
    { method: "POST", body: JSON.stringify({ to_phone: toPhone }) },
    WHATSAPP_TIMEOUT_MS
  );
}

export function provisionPhone(
  agencyId: string
): Promise<{ status: Exclude<PhoneState, "none">; number?: string | null }> {
  return api(
    `/api/v1/agencies/${agencyId}/channels/phone`,
    { method: "POST", body: JSON.stringify({}) },
    PHONE_TIMEOUT_MS
  );
}

export type WebchatVerifyReason =
  | "ok"
  | "widget_not_found"
  | "other_slug"
  | "unreachable"
  | "blocked_url";

export function verifyWebchat(
  agencyId: string,
  url: string
): Promise<{ verified: boolean; url: string; reason: WebchatVerifyReason }> {
  return api(
    `/api/v1/agencies/${agencyId}/channels/webchat/verify`,
    { method: "POST", body: JSON.stringify({ url }) },
    WEBCHAT_VERIFY_TIMEOUT_MS
  );
}

// ---------------------------------------------------------------------------
// Avisos automáticos al CRM (paso 8)
// ---------------------------------------------------------------------------

export type WebhookSubscription = {
  id: string;
  target_url: string;
  events: string[];
  is_active: boolean;
  secret_preview: string;
  consecutive_failures: number;
  /** Only ever populated by the POST that creates it — see webhooks_admin.py. */
  secret_full_once: string | null;
};

export type WebhookTestOut = {
  delivered: boolean;
  status_code: number | null;
  error: string | null;
  is_active: boolean;
  consecutive_failures: number;
};

export function fetchWebhookSubscriptions(agencyId: string): Promise<WebhookSubscription[]> {
  return api<WebhookSubscription[]>(`/api/v1/agencies/${agencyId}/webhook-subscriptions`);
}

export function createWebhookSubscription(
  agencyId: string,
  body: { target_url: string; events: string[] }
): Promise<WebhookSubscription> {
  return api<WebhookSubscription>(`/api/v1/agencies/${agencyId}/webhook-subscriptions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function testWebhookSubscription(
  agencyId: string,
  subId: string
): Promise<WebhookTestOut> {
  return api<WebhookTestOut>(
    `/api/v1/agencies/${agencyId}/webhook-subscriptions/${subId}/test`,
    { method: "POST" },
    WEBHOOK_TEST_TIMEOUT_MS
  );
}

export async function deleteWebhookSubscription(
  agencyId: string,
  subId: string
): Promise<void> {
  // 204 with an empty body: `api()` would choke parsing it, so the raw
  // authenticated fetch is the honest tool here.
  const res = await apiFetch(
    `/api/v1/agencies/${agencyId}/webhook-subscriptions/${subId}`,
    { method: "DELETE" }
  );
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || `HTTP ${res.status}`);
  }
}

/** Events the backend dispatcher supports, with the same plain-Spanish labels
 * the web panel uses (dashboard/src/lib/channels.ts → WEBHOOK_EVENTS). */
export const WEBHOOK_EVENTS: { key: string; label: string }[] = [
  { key: "lead.created", label: "Nuevo lead" },
  { key: "lead.qualified", label: "Lead cualificado" },
  { key: "lead.temperature_changed", label: "Cambio de interés del lead" },
  { key: "visit.scheduled", label: "Visita agendada" },
  { key: "visit.completed", label: "Visita realizada" },
  { key: "offer.created", label: "Oferta creada" },
  { key: "offer.signed", label: "Oferta firmada" },
];

export function webhookEventLabel(key: string): string {
  return WEBHOOK_EVENTS.find((e) => e.key === key)?.label ?? key;
}

// ---------------------------------------------------------------------------
// Catálogo (paso 6)
// ---------------------------------------------------------------------------

export type ImportResult = {
  imported: number;
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
};

export function importPropertiesCsv(
  agencyId: string,
  asset: { uri: string; name?: string | null; mimeType?: string | null }
): Promise<ImportResult> {
  const form = new FormData();
  // React Native's FormData takes {uri,name,type} for file parts; the DOM
  // typings only know Blob, hence the cast.
  form.append("file", {
    uri: asset.uri,
    name: asset.name || "catalogo.csv",
    type: asset.mimeType || "text/csv",
  } as unknown as Blob);
  return apiUpload<ImportResult>(`/api/v1/agencies/${agencyId}/properties/import`, form);
}

// ---------------------------------------------------------------------------
// Chat de la web + prueba final (pasos 7 y 9)
// ---------------------------------------------------------------------------

export function webchatSnippetFor(slug: string): string {
  return `<script src="https://www.selai.es/widget.js" data-selai="${slug}" async></script>`;
}

/** Public endpoint (no auth): the same one the website widget talks to, so
 * the final test exercises exactly what a visitor would get. */
export function sendWebChatMessage(
  slug: string,
  sessionId: string,
  text: string
): Promise<{ reply: string }> {
  return api<{ reply: string }>(
    `/webhooks/web/${slug}/message`,
    { method: "POST", body: JSON.stringify({ session_id: sessionId, text }) },
    CHAT_TIMEOUT_MS
  );
}

// ---------------------------------------------------------------------------
// Guías de portales (paso 5) — mismos textos que el panel web
// ---------------------------------------------------------------------------

export const PORTAL_GUIDES: {
  key: string;
  name: string;
  path: string[];
  note: string;
}[] = [
  {
    key: "idealista",
    name: "Idealista",
    path: [
      "Entra en idealista.com/pro con tu usuario profesional",
      "Menú de tu nombre (arriba a la derecha) → «Configuración»",
      "«Datos de la oficina» → apartado «Contacto de los anuncios»",
      "Campo «Email para recibir los contactos»",
    ],
    note: "Idealista suele permitir varios emails separados por comas: añade el tuyo de selai sin quitar el que ya tenías.",
  },
  {
    key: "fotocasa",
    name: "Fotocasa",
    path: [
      "Entra en el Área Profesional de Fotocasa",
      "«Mi cuenta» → «Datos de contacto»",
      "Apartado «Email de recepción de leads»",
    ],
    note: "Fotocasa admite un único email. Si necesitas conservar el tuyo, déjalo y crea una regla de reenvío automático en tu correo.",
  },
  {
    key: "habitaclia",
    name: "Habitaclia",
    path: [
      "Entra en el panel profesional de Habitaclia",
      "«Configuración» → «Datos de la agencia»",
      "Campo «Email de contacto para peticiones»",
    ],
    note: "En Habitaclia el email puede estar además en cada anuncio: si alguno tiene uno propio, cámbialo también.",
  },
];

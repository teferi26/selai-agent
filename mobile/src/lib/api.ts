import * as SecureStore from "expo-secure-store";
import Constants from "expo-constants";

import { isDevAuthEnabled } from "@/lib/auth";

const APP_ENVIRONMENT =
  process.env.EXPO_PUBLIC_APP_ENV ??
  (Constants.expoConfig?.extra?.appEnvironment as string | undefined) ??
  "development";
const CONFIGURED_API_URL = (
  process.env.EXPO_PUBLIC_API_URL ??
  (Constants.expoConfig?.extra?.apiUrl as string | undefined) ??
  ""
).replace(/\/$/, "");

if (!CONFIGURED_API_URL && APP_ENVIRONMENT !== "development") {
  throw new Error(`Missing API URL for ${APP_ENVIRONMENT}.`);
}
if (APP_ENVIRONMENT === "preview" && CONFIGURED_API_URL === "https://api.selai.es") {
  throw new Error("Preview cannot connect to the production API.");
}

// Local development is the only environment allowed to have a fallback. A
// preview or production build without its EAS URL now fails closed instead of
// silently sending test data to real customers.
const BASE = CONFIGURED_API_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// Auth plumbing
//
// In production the Clerk provider registers a token getter here (see
// ClerkTokenBridge in app/_layout.tsx) and every request carries
// `Authorization: Bearer <jwt>`. In dev without a Clerk key, we fall back to
// the backend's X-Dev-Email header.
// ---------------------------------------------------------------------------

type TokenGetter = () => Promise<string | null>;
let tokenGetter: TokenGetter | null = null;

export function registerTokenGetter(fn: TokenGetter | null) {
  tokenGetter = fn;
}

const DEV_EMAIL_KEY = "dev_email";

export async function getDevEmail(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(DEV_EMAIL_KEY);
  } catch {
    return null;
  }
}

export async function setDevEmail(email: string): Promise<void> {
  await SecureStore.setItemAsync(DEV_EMAIL_KEY, email);
}

export async function clearDevEmail(): Promise<void> {
  await SecureStore.deleteItemAsync(DEV_EMAIL_KEY);
}

// ---------------------------------------------------------------------------
// Timeouts
//
// `fetch` has none. On a phone that matters more than anywhere else: a lift,
// a captive wifi that swallows the connection, a dead spot on the motorway —
// the socket never errors, the promise never settles and the screen keeps a
// spinner turning forever with no way back. Every request therefore carries a
// ceiling, and hitting it raises a normal ApiError so each screen can offer
// its retry instead of hanging.
// ---------------------------------------------------------------------------

/** Ceiling for a plain request. Endpoints that legitimately take longer pass
 * their own (see lib/setup-api.ts). */
export const DEFAULT_TIMEOUT_MS = 30_000;
/** Uploads travel over mobile data; a catalog of a few hundred rows needs it. */
export const UPLOAD_TIMEOUT_MS = 120_000;
/** Status carried by a timed-out request. Not a real HTTP code — 408 is the
 * closest thing and no endpoint of ours answers with it. */
export const TIMEOUT_STATUS = 408;
/** Resolving the Clerk token is itself a network call. */
const AUTH_TIMEOUT_MS = 10_000;

export function isTimeout(e: unknown): boolean {
  return e instanceof ApiError && e.status === TIMEOUT_STATUS;
}

async function authHeaders(): Promise<Record<string, string>> {
  if (tokenGetter) {
    // Bounded on purpose: a hanging token refresh would freeze the request
    // before it even left, which looks exactly like a frozen app.
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const token = await Promise.race([
        tokenGetter(),
        new Promise<null>((resolve) => {
          timer = setTimeout(() => resolve(null), AUTH_TIMEOUT_MS);
        }),
      ]);
      if (token) return { Authorization: `Bearer ${token}` };
    } catch {
      // fall through to dev header
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
  if (isDevAuthEnabled) {
    const devEmail = await getDevEmail();
    if (devEmail) return { "X-Dev-Email": devEmail };
  }
  return {};
}

async function fetchWithTimeout(
  path: string,
  init: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(`${BASE}${path}`, { ...init, signal: controller.signal });
  } catch (e) {
    if (controller.signal.aborted) {
      throw new ApiError(TIMEOUT_STATUS, "request_timeout");
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export async function api<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<T> {
  const auth = await authHeaders();
  const res = await fetchWithTimeout(
    path,
    {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...auth,
        ...(init.headers || {}),
      },
    },
    timeoutMs
  );
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Authenticated fetch that hands back the raw `Response`.
 *
 * `api()` always parses JSON, which is wrong for the two endpoints that
 * don't answer with any: the voice preview (`audio/mpeg` bytes) and the
 * webhook-subscription DELETE (204, empty body). Same auth as every other
 * call — this exists so nothing has to rebuild the token plumbing above.
 */
export async function apiFetch(
  path: string,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<Response> {
  const auth = await authHeaders();
  return fetchWithTimeout(
    path,
    { ...init, headers: { ...auth, ...(init.headers || {}) } },
    timeoutMs
  );
}

/** Multipart upload — lets fetch set the Content-Type boundary itself. */
export async function apiUpload<T>(
  path: string,
  form: FormData,
  timeoutMs: number = UPLOAD_TIMEOUT_MS
): Promise<T> {
  const auth = await authHeaders();
  const res = await fetchWithTimeout(
    path,
    { method: "POST", body: form, headers: auth },
    timeoutMs
  );
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// API types
// ---------------------------------------------------------------------------

/** One row of `MeOut.usage` (backend/app/api/me.py). */
export type MeUsage = {
  resource: string;
  used: number;
  limit: number;
};

export type Me = {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
  agency_id: string;
  agency_name: string;
  /** Public identifier of the agency: the web-chat widget snippet, the
   * portals lead address and the public chat endpoint are all built from it
   * (MeOut in backend/app/api/me.py always returns it). */
  agency_slug: string;
  /** `SubscriptionStatus` of the backend: trialing | active | past_due |
   * canceled | unpaid | paused. Null while the agency has no subscription
   * row at all. */
  subscription_status: string | null;
  plan_code: string | null;
  plan_name: string | null;
  monthly_price_eur: number | null;
  trial_ends_at: string | null;
  usage: MeUsage[];
};

export type Channel = "whatsapp" | "email" | "voice" | "sms" | "web";
export type Temperature = "hot" | "warm" | "cold";

/** Backend `ConversationStatus`. `handed_off` means the AI is paused and a
 * human owns the conversation. */
export type ConversationStatus = "active" | "snoozed" | "handed_off" | "closed";

export type InboxItem = {
  conversation_id: string;
  channel: Channel;
  status: ConversationStatus;
  lead_id: string;
  lead_name: string | null;
  lead_temperature: Temperature;
  last_message_at: string;
  last_message_role: string | null;
  last_message_preview: string | null;
  unread: boolean;
  assigned_user_id: string | null;
};

export type MessageRole = "lead" | "agent_ai" | "agent_human" | "system";

export type ConversationMessage = {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
};

export type Lead = {
  id: string;
  full_name: string | null;
  phone_e164: string | null;
  email: string | null;
  status: string;
  temperature: Temperature;
  score: number;
  language: string | null;
  budget_min: number | null;
  budget_max: number | null;
  operation: string | null;
  created_at: string;
};

/** GET /api/v1/agencies/{id}/settings — whitelisted view (see backend
 * app/api/agency_settings.py). Only the fields the app consumes are typed. */
export type AgencySettings = {
  name: string;
  slug: string;
  timezone: string;
  default_language: string;
  settings: {
    voice_id: string | null;
    elevenlabs_voice_id: string | null;
    ai_phone_number: string | null;
    /** Whitelisted by the API (`_EXPOSED_SETTINGS_KEYS` in
     * backend/app/api/agency_settings.py), so the key is always present —
     * null until the owner fills it in on step 1. */
    business_name: string | null;
    setup_checklist: Record<string, boolean>;
    [key: string]: unknown;
  };
};

export type PropertySummary = {
  id: string;
  [key: string]: unknown;
};

/** GET /properties — full `PropertyOut` (backend/app/api/properties.py). */
export type Property = {
  id: string;
  reference: string;
  title: string;
  city: string;
  neighborhood: string | null;
  price: number;
  bedrooms: number | null;
  area_sqm: number | null;
  /** PropertyStatus: draft | available | reserved | sold | rented | archived
   * (backend/app/db/models/property.py). `propertyStatusLabel` in theme.ts
   * translates all six. */
  status: string;
  description: string;
  image_urls: string[];
  created_at: string;
};

/** GET /visits — `VisitOut` (backend/app/api/visits.py). */
export type Visit = {
  id: string;
  scheduled_at: string;
  /** VisitStatus: requested | confirmed | rescheduled | cancelled | no_show |
   * completed (backend/app/db/models/visit.py). `visitStatusLabel` in
   * theme.ts translates all six. */
  status: string;
  lead_name: string | null;
  lead_phone: string | null;
  property_title: string | null;
  property_id: string;
  briefing: string;
  /** IANA name of the agency's timezone. `scheduled_at` is an absolute
   * instant, so the agenda has to group and print the hour with this and not
   * with the phone's own timezone — an agent travelling abroad would
   * otherwise see the visits on the wrong day. */
  timezone: string;
};

/** POST /conversations/{id}/reply — `ReplyOut`. The message it returns is the
 * one already stored by the backend, so the screen can append it as-is. */
export type ManualReply = {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  external_message_id: string | null;
  handed_off: boolean;
};

export type Kpis = {
  leads_total: number;
  leads_new: number;
  leads_qualified: number;
  visits_scheduled: number;
  visits_completed: number;
  handoffs: number;
  avg_first_response_seconds: number | null;
  [key: string]: unknown;
};

// ---------------------------------------------------------------------------
// Endpoint helpers
// ---------------------------------------------------------------------------

export function fetchMe(): Promise<Me> {
  return api<Me>("/api/v1/me");
}

export function fetchInbox(agencyId: string, channel?: string): Promise<InboxItem[]> {
  const qs = new URLSearchParams({ limit: "100" });
  if (channel) qs.set("channel", channel);
  return api<InboxItem[]>(`/api/v1/agencies/${agencyId}/inbox?${qs.toString()}`);
}

export function fetchMessages(
  agencyId: string,
  conversationId: string
): Promise<ConversationMessage[]> {
  return api<ConversationMessage[]>(
    `/api/v1/agencies/${agencyId}/conversations/${conversationId}/messages`
  );
}

export function requestHandoff(
  agencyId: string,
  conversationId: string,
  payload: { user_id?: string; note: string }
): Promise<unknown> {
  return api(`/api/v1/agencies/${agencyId}/conversations/${conversationId}/handoff`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Give the conversation back to the AI after a handoff. */
export function resumeConversation(
  agencyId: string,
  conversationId: string
): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>(
    `/api/v1/agencies/${agencyId}/conversations/${conversationId}/resume`,
    { method: "POST" }
  );
}

/**
 * Answer the lead by hand. The backend sends through the *real* channel and
 * only stores the message if the provider accepted it, so a rejection here
 * means nothing was written either — see `explainReplyError` in
 * app/conversation/[id].tsx for the error codes it can raise.
 */
export function sendReply(
  agencyId: string,
  conversationId: string,
  text: string
): Promise<ManualReply> {
  return api<ManualReply>(
    `/api/v1/agencies/${agencyId}/conversations/${conversationId}/reply`,
    { method: "POST", body: JSON.stringify({ text }) }
  );
}

/** GET /conversations/{id} — who is answering and through which channel. */
export type ConversationState = {
  id: string;
  status: ConversationStatus;
  channel: Channel;
  lead_id: string;
  assigned_user_id: string | null;
};

/**
 * Server-side state of one conversation: whether the AI is still answering
 * and which channel it arrived on.
 *
 * Raises ApiError 404 when the conversation is gone or belongs to another
 * agency — the caller must show that as "ya no está disponible", because
 * `/messages` answers `[]` in both cases and would otherwise be read as an
 * empty-but-real thread.
 */
export function fetchConversationState(
  agencyId: string,
  conversationId: string
): Promise<ConversationState> {
  return api<ConversationState>(
    `/api/v1/agencies/${agencyId}/conversations/${conversationId}`
  );
}

export function fetchLeads(agencyId: string, temperature?: string): Promise<Lead[]> {
  const qs = new URLSearchParams({ limit: "200" });
  if (temperature) qs.set("temperature", temperature);
  return api<Lead[]>(`/api/v1/agencies/${agencyId}/leads?${qs.toString()}`);
}

/** One lead by id. Raises ApiError 404 when it is not this agency's. */
export function fetchLead(agencyId: string, leadId: string): Promise<Lead> {
  return api<Lead>(`/api/v1/agencies/${agencyId}/leads/${leadId}`);
}

export function fetchKpis(agencyId: string, rangeDays = 30): Promise<Kpis> {
  return api<Kpis>(`/api/v1/agencies/${agencyId}/kpis?range_days=${rangeDays}`);
}

export function fetchAgencySettings(agencyId: string): Promise<AgencySettings> {
  return api<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`);
}

/**
 * Subset of the PATCH body the app sends (`AgencySettingsPatch` in
 * backend/app/api/agency_settings.py). `setup_checklist` is merged key by key
 * server-side, so sending one key never clobbers what the web panel ticked.
 *
 * Two screens write here: the setup wizard (the profile half) and
 * "Comportamiento de la IA" (the `ai_*` half). It lives next to the GET so
 * there is one place to keep in sync with the backend's whitelist; the AI
 * types and bounds are in `lib/ai-settings.ts`.
 */
export type AgencySettingsPatch = {
  business_name?: string | null;
  zones?: string[];
  schedule_text?: string | null;
  website?: string | null;
  setup_checklist?: Record<string, boolean>;
  ai_tone?: "cercano" | "formal";
  forbidden_phrases?: string[];
  ai_instructions?: string;
  ai_mode?: "always" | "out_of_hours";
  business_hours?: { start: string; end: string; days: number[] } | null;
  recording_notice?: boolean;
};

export function patchAgencySettings(
  agencyId: string,
  patch: AgencySettingsPatch
): Promise<AgencySettings> {
  return api<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** Only used to know whether the catalog has at least one property. */
export function fetchPropertiesSample(agencyId: string): Promise<PropertySummary[]> {
  return api<PropertySummary[]>(`/api/v1/agencies/${agencyId}/properties?limit=1`);
}

/** The catalog. 500 is the backend's own ceiling for this endpoint; the
 * search box filters over what we already have instead of round-tripping. */
export function fetchProperties(agencyId: string, limit = 500): Promise<Property[]> {
  return api<Property[]>(`/api/v1/agencies/${agencyId}/properties?limit=${limit}`);
}

/** The agenda. `upcoming` bounds the listing at midnight today *in the
 * agency's timezone*, which the phone cannot compute on its own. */
export function fetchVisits(agencyId: string, upcoming = false): Promise<Visit[]> {
  const qs = new URLSearchParams({ limit: "200" });
  if (upcoming) qs.set("upcoming", "true");
  return api<Visit[]>(`/api/v1/agencies/${agencyId}/visits?${qs.toString()}`);
}

export function requestOutboundCall(agencyId: string, leadId: string): Promise<unknown> {
  return api(`/api/v1/agencies/${agencyId}/voice/outbound-call`, {
    method: "POST",
    body: JSON.stringify({ lead_id: leadId }),
  });
}

export function uploadVoiceNote(
  agencyId: string,
  visitId: string,
  form: FormData
): Promise<unknown> {
  return apiUpload(`/api/v1/agencies/${agencyId}/visits/${visitId}/voice-note`, form);
}

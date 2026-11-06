/** Shared logic for the "Puesta en marcha" activation wizard.
 *
 * Pure (client-safe) helpers: both the setup wizard (SetupClient) and the
 * sidebar mini-progress in (app)/layout.tsx compute step completion from
 * the same rules, so they never drift apart.
 *
 * Persistence model: everything lives in settings.setup_checklist (merged
 * key by key by the backend PATCH). Legacy keys are preserved and still
 * count: "call_forwarding", "whatsapp", "whatsapp_requested", "test_call".
 */
import type { AgencySettings, SetupChecklist } from "./agency-settings";

export const SETUP_STEP_KEYS = [
  "agency",
  "voice",
  "phone",
  "whatsapp",
  "portals",
  "properties",
  "webchat",
  "crm",
  "test_call",
] as const;

export type SetupStepKey = (typeof SETUP_STEP_KEYS)[number];

export const SETUP_TOTAL = SETUP_STEP_KEYS.length;

export const SETUP_STEP_TITLES: Record<SetupStepKey, string> = {
  agency: "Tu agencia",
  voice: "La voz de tu IA",
  phone: "El teléfono de tu IA",
  whatsapp: "WhatsApp",
  portals: "Portales inmobiliarios",
  properties: "Tu catálogo",
  webchat: "El chat de tu web",
  crm: "Tu CRM",
  test_call: "Prueba final",
};

/** Which steps are done, given the agency settings + checklist + catalog.
 *
 * Steps that execute a real connection persist their success in the
 * checklist (the wizard PATCHes it on success), so the sidebar can compute
 * progress without extra backend calls. */
export function computeSetupDone(
  s: AgencySettings["settings"],
  checklist: SetupChecklist,
  hasProperties: boolean
): Record<SetupStepKey, boolean> {
  return {
    agency: !!checklist["agency_profile"] || !!s.business_name,
    voice: !!(s.voice_id || s.elevenlabs_voice_id),
    phone: !!s.ai_phone_number,
    whatsapp: !!checklist["whatsapp"],
    portals: !!checklist["portals"],
    properties: hasProperties,
    webchat: !!checklist["webchat"],
    crm: !!checklist["crm"],
    test_call: !!checklist["test_call"],
  };
}

export function countSetupDone(done: Record<SetupStepKey, boolean>): number {
  return SETUP_STEP_KEYS.filter((k) => done[k]).length;
}

/** First step the user still has pending — where the wizard opens. */
export function firstPendingStep(done: Record<SetupStepKey, boolean>): number {
  const idx = SETUP_STEP_KEYS.findIndex((k) => !done[k]);
  return idx === -1 ? SETUP_TOTAL - 1 : idx;
}

// "Puesta en marcha" — shared progress rules for the native wizard.
//
// Mirrors dashboard/src/lib/setup-steps.ts exactly: same 9 step keys, same
// order, same completion rules (SETUP_STEP_KEYS + computeSetupDone), computed
// from the agency settings endpoint + whether the property catalog has
// entries. Web and app therefore always count the same number of steps done.
// If the dashboard wizard changes, update BOTH files.
//
// The configuration itself now happens in the app too (app/setup/[step].tsx);
// this module stays the single source of truth for *what counts as done*.
import { useCallback, useEffect, useRef, useState } from "react";
import { AgencySettings, fetchAgencySettings, fetchPropertiesSample } from "@/lib/api";

export type SetupStepKey =
  | "agency"
  | "voice"
  | "phone"
  | "whatsapp"
  | "portals"
  | "properties"
  | "webchat"
  | "crm"
  | "test_call";

export type SetupStep = {
  key: SetupStepKey;
  title: string;
  description: string;
  done: boolean;
};

/** Order of the wizard. Index + 1 is the number shown as "Paso N de 9" and
 * the `step` route param of app/setup/[step].tsx. */
export const SETUP_STEP_KEYS: SetupStepKey[] = [
  "agency",
  "voice",
  "phone",
  "whatsapp",
  "portals",
  "properties",
  "webchat",
  "crm",
  "test_call",
];

/** Same titles as the web wizard, verbatim, so the owner reads the same names
 * on both screens. Available without loading anything (the wizard header
 * shows them while the data is still in flight). */
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

export const SETUP_WEB_URL = "https://www.selai.es/setup";

// App-only helper copy under each title (honest about what each step does and
// about the waits that don't depend on us).
const STEP_DESCRIPTIONS: Record<SetupStepKey, string> = {
  agency: "Nombre, zonas y horario: lo que tu IA cuenta de vosotros.",
  voice: "La voz con la que atiende las llamadas.",
  phone: "El número propio de tu IA y el desvío de llamadas.",
  whatsapp: "Tu número de WhatsApp Business. Meta tarda 1-2 días en verificar la empresa.",
  portals: "Que los avisos de Idealista, Fotocasa o Habitaclia lleguen a tu IA.",
  properties: "Importa tus inmuebles para que proponga pisos que existen.",
  webchat: "El chat que atiende a quien visita tu página.",
  crm: "Avisos automáticos hacia el programa donde ya trabajas.",
  test_call: "Habla con tu IA y comprueba qué contesta.",
};

const STEP_META: { key: SetupStepKey; title: string; description: string }[] =
  SETUP_STEP_KEYS.map((key) => ({
    key,
    title: SETUP_STEP_TITLES[key],
    description: STEP_DESCRIPTIONS[key],
  }));

export const SETUP_TOTAL = SETUP_STEP_KEYS.length;

/** Same completion rules as the dashboard (computeSetupDone in
 * setup-steps.ts): the `agency_profile` checklist key, or a `business_name`
 * already on file (the API whitelists it, so both signals are real). */
export function computeSetupSteps(
  settings: AgencySettings["settings"],
  hasProperties: boolean
): SetupStep[] {
  const checklist = settings.setup_checklist || {};
  const done: Record<SetupStepKey, boolean> = {
    agency: !!checklist["agency_profile"] || !!settings.business_name,
    voice: !!(settings.voice_id || settings.elevenlabs_voice_id),
    phone: !!settings.ai_phone_number,
    whatsapp: !!checklist["whatsapp"],
    portals: !!checklist["portals"],
    properties: hasProperties,
    webchat: !!checklist["webchat"],
    crm: !!checklist["crm"],
    test_call: !!checklist["test_call"],
  };
  return STEP_META.map((m) => ({ ...m, done: done[m.key] }));
}

/** 1-based number of the first step still pending — where the wizard opens
 * when the user taps "Puesta en marcha" instead of a specific row. */
export function firstPendingStepNumber(steps: SetupStep[] | null): number {
  if (!steps) return 1;
  const idx = steps.findIndex((s) => !s.done);
  return idx === -1 ? SETUP_TOTAL : idx + 1;
}

export type SetupStatus = {
  loading: boolean;
  error: boolean;
  steps: SetupStep[] | null;
  doneCount: number;
  total: number;
  complete: boolean;
  /** Silent re-fetch (keeps current data on screen while it loads). */
  refresh: () => Promise<void>;
};

// Module-level cache so the inbox card and the checklist screen don't refetch
// on every mount. Refreshed explicitly via refresh().
let cachedSteps: SetupStep[] | null = null;
let cachedAgencyId: string | null = null;

/** Drops the cache after the wizard changes something, so the inbox card and
 * the checklist screen recompute instead of showing yesterday's progress. */
export function invalidateSetupCache(): void {
  cachedSteps = null;
  cachedAgencyId = null;
}

export function useSetupStatus(agencyId: string | undefined): SetupStatus {
  const [steps, setSteps] = useState<SetupStep[] | null>(
    agencyId && agencyId === cachedAgencyId ? cachedSteps : null
  );
  const [loading, setLoading] = useState(steps === null);
  const [error, setError] = useState(false);
  /** Espejo de `steps` para leerlo dentro de `load` sin recrear el callback. */
  const stepsRef = useRef<SetupStep[] | null>(steps);

  const load = useCallback(async (opts: { silent?: boolean } = {}) => {
    if (!agencyId) return;
    if (!opts.silent) setLoading(true);
    setError(false);
    try {
      const [settings, properties] = await Promise.all([
        fetchAgencySettings(agencyId),
        fetchPropertiesSample(agencyId),
      ]);
      const computed = computeSetupSteps(settings.settings, properties.length > 0);
      cachedSteps = computed;
      cachedAgencyId = agencyId;
      stepsRef.current = computed;
      setSteps(computed);
    } catch {
      // Un refresco silencioso que falla (al volver del asistente sin
      // cobertura, por ejemplo) no puede borrar el progreso que ya estaba en
      // pantalla: solo es un error cuando no hay nada que enseñar.
      if (!opts.silent || stepsRef.current === null) setError(true);
    } finally {
      setLoading(false);
    }
  }, [agencyId]);

  useEffect(() => {
    if (agencyId && agencyId !== cachedAgencyId) {
      void load();
    } else if (agencyId && cachedSteps) {
      stepsRef.current = cachedSteps;
      setSteps(cachedSteps);
      setLoading(false);
    }
  }, [agencyId, load]);

  const doneCount = steps ? steps.filter((s) => s.done).length : 0;
  const total = SETUP_TOTAL;
  return {
    loading,
    error,
    steps,
    doneCount,
    total,
    complete: steps !== null && doneCount >= total,
    refresh: () => load({ silent: true }),
  };
}

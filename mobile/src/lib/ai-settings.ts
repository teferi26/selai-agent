// ---------------------------------------------------------------------------
// "Comportamiento de la IA": tipos, topes y lecturas del blob de settings.
//
// Espejo del panel web (dashboard/src/lib/agency-settings.ts y
// dashboard/src/app/(app)/settings/ia/IAClient.tsx) y del contrato del backend
// (backend/app/api/agency_settings.py). Los dos clientes tienen que enseñar y
// enviar exactamente lo mismo: lo que el dueño cambia desde el móvil es la
// misma agencia que atiende desde el ordenador.
//
// El GET devuelve estas claves dentro de `settings.settings`, que en el móvil
// está tipado como `[key: string]: unknown` (lib/api.ts). Las funciones de
// lectura de abajo son las únicas que interpretan ese `unknown`: si el backend
// devuelve algo raro, se cae al valor por defecto en vez de romper la pantalla.
// ---------------------------------------------------------------------------
import { AgencySettings } from "@/lib/api";

export type AiTone = "cercano" | "formal";
export type AiMode = "always" | "out_of_hours";

/**
 * Horario de atención del modo "solo fuera de horario".
 *
 * days: 0 = lunes … 6 = domingo. Es la convención de `datetime.weekday()` de
 * Python, la que valida y evalúa el backend (BusinessHours en
 * app/api/agency_settings.py, is_within_business_hours en
 * app/services/conversation_service.py) y la que usa el panel web. Si se
 * cambia la escala en un solo lado, el horario guardado se aplica corrido de
 * día sin que nada lo delate: backend/tests/test_cross_layer_contracts.py
 * compara los dos lados.
 */
export type BusinessHours = {
  /** "HH:MM" (00:00–23:59). */
  start: string;
  /** "HH:MM" (00:00–23:59). */
  end: string;
  days: number[];
};

/** Tope de `ai_instructions`. Espejo de MAX_AI_INSTRUCTIONS_LEN en el backend
 * (= MAX_AI_INSTRUCTIONS_CHARS en app/agent/prompts.py): si se separan, la app
 * deja escribir un texto que el PATCH rechaza con un 422 sin explicación. */
export const MAX_AI_INSTRUCTIONS = 2000;

/** Espejo de MAX_FORBIDDEN_PHRASE_LEN en app/api/agency_settings.py. */
export const MAX_FORBIDDEN_PHRASE = 200;

/** Espejo de MAX_FORBIDDEN_PHRASES en app/api/agency_settings.py. */
export const MAX_FORBIDDEN_PHRASES = 50;

/** Espejo de MIN_FORBIDDEN_PHRASE_CHARS en app/agent/guardrail.py: por debajo
 * de eso el filtro se salta la frase, así que prometer "nunca lo dirá" sobre
 * algo que no se comprueba sería mentir. */
export const MIN_FORBIDDEN_PHRASE = 3;

export const DEFAULT_HOURS: BusinessHours = {
  start: "09:00",
  end: "19:00",
  days: [0, 1, 2, 3, 4],
};

/** Mismas etiquetas que el panel web. `value` es el número de Python. */
export const DAYS: { value: number; short: string; name: string }[] = [
  { value: 0, short: "L", name: "Lunes" },
  { value: 1, short: "M", name: "Martes" },
  { value: 2, short: "X", name: "Miércoles" },
  { value: 3, short: "J", name: "Jueves" },
  { value: 4, short: "V", name: "Viernes" },
  { value: 5, short: "S", name: "Sábado" },
  { value: 6, short: "D", name: "Domingo" },
];

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

// ---------------------------------------------------------------------------
// Lecturas del blob
// ---------------------------------------------------------------------------

type Blob = AgencySettings["settings"];

export function readTone(s: Blob | undefined): AiTone {
  return s?.ai_tone === "formal" ? "formal" : "cercano";
}

export function readPhrases(s: Blob | undefined): string[] {
  const raw = s?.forbidden_phrases;
  return Array.isArray(raw) ? raw.filter((p): p is string => typeof p === "string") : [];
}

export function readInstructions(s: Blob | undefined): string {
  const raw = s?.ai_instructions;
  return typeof raw === "string" ? raw : "";
}

export function readMode(s: Blob | undefined): AiMode {
  return s?.ai_mode === "out_of_hours" ? "out_of_hours" : "always";
}

/** El horario guardado, o el de por defecto si no hay ninguno o está roto.
 * Nunca devuelve una lista de días vacía: el backend exige al menos uno. */
export function readBusinessHours(s: Blob | undefined): BusinessHours {
  const raw = s?.business_hours;
  if (!raw || typeof raw !== "object") return DEFAULT_HOURS;
  const bh = raw as Partial<BusinessHours>;
  const days = Array.isArray(bh.days)
    ? [...new Set(bh.days.filter((d) => Number.isInteger(d) && d >= 0 && d <= 6))].sort(
        (a, b) => a - b
      )
    : [];
  return {
    start: typeof bh.start === "string" && HHMM.test(bh.start) ? bh.start : DEFAULT_HOURS.start,
    end: typeof bh.end === "string" && HHMM.test(bh.end) ? bh.end : DEFAULT_HOURS.end,
    days: days.length > 0 ? days : DEFAULT_HOURS.days,
  };
}

/** El backend lo expone como `raw.get("recording_notice") is not False`, así
 * que ausente significa activado. Aquí se respeta ese mismo criterio. */
export function readRecordingNotice(s: Blob | undefined): boolean {
  return s?.recording_notice !== false;
}

// ---------------------------------------------------------------------------
// Frases prohibidas: por qué NO se puede añadir una
// ---------------------------------------------------------------------------

/** Solo letras y números: es como compara el filtro del backend, que ignora
 * mayúsculas, tildes y puntuación. */
function meaningfulLength(phrase: string): number {
  return phrase.replace(/[^\p{L}\p{N}]/gu, "").length;
}

export type PhraseCheck = {
  /** Si se puede añadir a la lista. */
  accept: boolean;
  /** Por qué NO se ha añadido, cuando merece explicación. null cuando no hay
   * nada que decir: una frase repetida o vacía se descarta en silencio, igual
   * que en el panel web — el usuario ya ve que está en la lista. */
  hint: string | null;
};

/**
 * Decide si una frase prohibida entra en la lista.
 *
 * Se comprueba aquí y no solo en el servidor porque los dos fallos posibles
 * son invisibles: una frase demasiado corta se guarda pero el filtro la ignora
 * (promesa incumplida), y pasarse del tope devuelve un 422 que en pantalla se
 * lee como un "inténtalo de nuevo" que nunca va a funcionar.
 */
export function checkPhrase(raw: string, existing: string[]): PhraseCheck {
  const phrase = raw.trim();
  if (!phrase) return { accept: false, hint: null };
  if (existing.some((p) => p.toLowerCase() === phrase.toLowerCase())) {
    return { accept: false, hint: null };
  }
  if (existing.length >= MAX_FORBIDDEN_PHRASES) {
    return {
      accept: false,
      hint: `Has llegado al máximo de ${MAX_FORBIDDEN_PHRASES} frases. Quita alguna para añadir otra.`,
    };
  }
  if (meaningfulLength(phrase) < MIN_FORBIDDEN_PHRASE) {
    return {
      accept: false,
      hint: `Escribe al menos ${MIN_FORBIDDEN_PHRASE} letras. Una palabra tan corta no se puede vigilar sin bloquear mensajes que no la dicen.`,
    };
  }
  if (phrase.length > MAX_FORBIDDEN_PHRASE) {
    return {
      accept: false,
      hint: `La frase no puede pasar de ${MAX_FORBIDDEN_PHRASE} caracteres.`,
    };
  }
  return { accept: true, hint: null };
}

// ---------------------------------------------------------------------------
// Horas
// ---------------------------------------------------------------------------

/**
 * Las horas que ofrece el selector: cada media hora.
 *
 * El panel web usa un `<input type="time">` y admite cualquier minuto. Para no
 * pisar un horario puesto desde allí (09:45, por ejemplo), el valor actual se
 * inserta en la lista aunque no caiga en la rejilla: abrir el selector y
 * cerrarlo no puede cambiar lo que había guardado.
 */
export function hourOptions(current: string): string[] {
  const grid: string[] = [];
  for (let h = 0; h < 24; h++) {
    grid.push(`${String(h).padStart(2, "0")}:00`);
    grid.push(`${String(h).padStart(2, "0")}:30`);
  }
  if (HHMM.test(current) && !grid.includes(current)) {
    grid.push(current);
    grid.sort();
  }
  return grid;
}

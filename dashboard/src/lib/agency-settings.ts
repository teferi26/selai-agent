/** Types shared by the setup wizard and the voice settings page.
 *
 * Mirror of the FastAPI contracts:
 *   GET/PATCH /api/v1/agencies/{agencyId}/settings
 *   GET       /api/v1/voice/catalog
 *   PUT       /api/v1/agencies/{agencyId}/voice
 *   POST      /api/v1/agencies/{agencyId}/voice-cloning
 */

export type SetupChecklist = Record<string, boolean>;

/** Tope de `ai_instructions`. Espejo de MAX_AI_INSTRUCTIONS_LEN en el backend
 * (app/api/agency_settings.py): si se separan, el panel deja escribir un texto
 * que el PATCH rechaza con un 422 y el dueño solo ve "inténtalo de nuevo". */
export const MAX_AI_INSTRUCTIONS = 2000;

/** Tope de la nota puntual por conversación. Espejo de
 * MAX_CONVERSATION_NOTE_CHARS en backend/app/agent/prompts.py. */
export const MAX_CONVERSATION_NOTE = 500;

/** Longitud máxima de una frase prohibida. Espejo de
 * MAX_FORBIDDEN_PHRASE_LEN en backend/app/api/agency_settings.py. */
export const MAX_FORBIDDEN_PHRASE = 200;

/** Longitud mínima (letras y números) de una frase prohibida. Espejo de
 * MIN_FORBIDDEN_PHRASE_CHARS en backend/app/agent/guardrail.py: por debajo de
 * eso el filtro la ignora, así que el panel no debe dejar añadirla — prometer
 * "nunca lo dirá" sobre algo que no se comprueba es peor que no ofrecerlo. */
export const MIN_FORBIDDEN_PHRASE = 3;

/** GET/POST /agencies/{id}/conversations/{id}/note */
export type ConversationNote = {
  text: string;
  updated_at: string | null;
};

/** Horario de atención del modo "solo fuera de horario".
 *
 * days: 0 = lunes … 6 = domingo. Es la convención de `datetime.weekday()` de
 * Python y es la del backend, que la valida (rechaza cualquier valor fuera de
 * 0-6) y la evalúa con ese mismo significado. El panel usó 1..7 hasta julio de
 * 2026: el backend aceptaba el número y lo interpretaba como el día siguiente,
 * así que el horario guardado se aplicaba corrido un día sin que la UI lo
 * mostrase. Si cambias esta escala, cámbiala también en el backend:
 * backend/tests/test_cross_layer_contracts.py compara los dos lados.
 *
 * start/end en formato "HH:MM". */
export type BusinessHours = {
  start: string;
  end: string;
  days: number[];
};

export type AgencySettings = {
  name: string;
  slug: string;
  timezone: string | null;
  default_language: string | null;
  settings: {
    voice_provider: string | null;
    voice_id: string | null;
    voice_name: string | null;
    elevenlabs_voice_id: string | null;
    elevenlabs_voice_name: string | null;
    ai_phone_number: string | null;
    vapi_assistant_id: string | null;
    setup_checklist: SetupChecklist | null;
    // Perfil comercial de la agencia (paso 1 del asistente de activación).
    // Opcionales: el backend los expone cuando su whitelist los incluye.
    business_name?: string | null;
    zones?: string[] | null;
    schedule_text?: string | null;
    website?: string | null;
    // Comportamiento del agente IA (página /settings/ia). Opcionales porque
    // el backend puede no haberlos poblado todavía.
    ai_tone?: "cercano" | "formal" | null;
    forbidden_phrases?: string[] | null;
    /** Instrucciones libres para el asistente, en todas las conversaciones.
     * Máximo MAX_AI_INSTRUCTIONS caracteres (el backend valida lo mismo en
     * app/api/agency_settings.py: MAX_AI_INSTRUCTIONS_LEN). */
    ai_instructions?: string | null;
    ai_mode?: "always" | "out_of_hours" | null;
    business_hours?: BusinessHours | null;
    recording_notice?: boolean | null;
    // Encuesta de cancelación (/billing). El backend puede ignorarla.
    cancel_feedback?: { reason: string; at: string } | null;
  };
};

export type VoiceCatalogItem = {
  id: string;
  provider: string;
  name: string;
  language: string;
  gender: string;
  description: string;
  preview_url: string | null;
};

export type ApplyVoiceOut = { ok: boolean; applied_to_vapi: boolean };

export type CloneVoiceOut = { voice_id: string; name: string };

export type ImportOut = { imported: number; skipped: number; errors: string[] };

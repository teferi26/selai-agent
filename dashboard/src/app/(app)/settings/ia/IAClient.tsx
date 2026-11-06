// Client half de "Mi agente IA": tono (77), frases prohibidas (53),
// instrucciones libres, modo de atención + horario (52) y aviso de
// grabación (50).
// Todo guarda con PATCH /settings de forma optimista, con "Guardado ✓".
// Excepción: las instrucciones libres son un texto largo y se guardan con un
// botón — guardar en cada tecla mandaría un PATCH por letra.
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import {
  MAX_AI_INSTRUCTIONS,
  MAX_FORBIDDEN_PHRASE,
  MIN_FORBIDDEN_PHRASE,
  type AgencySettings,
  type BusinessHours,
} from "@/lib/agency-settings";

const SUGGESTED_PHRASES = [
  "regalamos la comisión",
  "el precio es negociable seguro",
  "garantizamos la venta en 30 días",
];

// Ejemplos reales del sector: se añaden al final del texto de un clic.
const INSTRUCTION_EXAMPLES = [
  "No des nunca una valoración del piso por escrito: ofrece que pase un agente a valorarlo sin compromiso.",
  "Antes de proponer una visita, pregunta si necesita hipoteca.",
  "Trabajamos toda la zona centro, aunque el cliente pregunte por un barrio concreto.",
  "Los sábados solo hacemos visitas por la mañana.",
  "Si el cliente llama desde fuera de España, avísale de que necesitará el NIE.",
];

// 0 = lunes … 6 = domingo: la convención de `datetime.weekday()` de Python,
// que es la que el backend valida (BusinessHours en app/api/agency_settings.py)
// y con la que decide si está dentro de horario (is_within_business_hours en
// app/services/conversation_service.py). Este panel usaba 1..7 y el backend
// leía el mismo número como un día distinto: "lunes a viernes" se atendía como
// "martes a sábado" y nadie lo veía, porque el GET devuelve el número tal cual
// y el panel lo repintaba con su propia escala. No cambies esta escala en un
// solo lado: backend/tests/test_cross_layer_contracts.py falla si se separan.
const DAYS: { value: number; label: string; name: string }[] = [
  { value: 0, label: "L", name: "Lunes" },
  { value: 1, label: "M", name: "Martes" },
  { value: 2, label: "X", name: "Miércoles" },
  { value: 3, label: "J", name: "Jueves" },
  { value: 4, label: "V", name: "Viernes" },
  { value: 5, label: "S", name: "Sábado" },
  { value: 6, label: "D", name: "Domingo" },
];

const DEFAULT_HOURS: BusinessHours = { start: "09:00", end: "19:00", days: [0, 1, 2, 3, 4] };

type SectionKey = "tone" | "phrases" | "instructions" | "mode" | "recording";

function SavedMark({
  section,
  saving,
  saved,
}: {
  section: SectionKey;
  saving: SectionKey | null;
  saved: SectionKey | null;
}) {
  if (saving === section) {
    return <span className="text-xs text-slate-400">Guardando…</span>;
  }
  if (saved === section) {
    return <span className="text-xs text-emerald-700">Guardado ✓</span>;
  }
  return null;
}

export default function IAClient({
  agencyId,
  agencyName,
  settings,
}: {
  agencyId: string;
  agencyName: string;
  settings: AgencySettings;
}) {
  const s = settings.settings;

  const [tone, setTone] = useState<"cercano" | "formal">(s.ai_tone ?? "cercano");
  const [phrases, setPhrases] = useState<string[]>(s.forbidden_phrases ?? []);
  const [newPhrase, setNewPhrase] = useState("");
  /** Por qué no se ha añadido la última frase escrita. */
  const [phraseHint, setPhraseHint] = useState<string | null>(null);
  // Dos estados para el mismo texto: lo que se está escribiendo y lo último
  // que aceptó el backend. La diferencia es lo que hace visible el botón.
  const [instructions, setInstructions] = useState<string>(s.ai_instructions ?? "");
  const [storedInstructions, setStoredInstructions] = useState<string>(s.ai_instructions ?? "");
  const [mode, setMode] = useState<"always" | "out_of_hours">(s.ai_mode ?? "always");
  const [hours, setHours] = useState<BusinessHours>(s.business_hours ?? DEFAULT_HOURS);
  const [recording, setRecording] = useState<boolean>(s.recording_notice ?? false);

  const [saving, setSaving] = useState<SectionKey | null>(null);
  const [saved, setSaved] = useState<SectionKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** PATCH optimista: el estado ya está pintado cuando llamamos; si falla,
   * el caller revierte con `rollback`. Devuelve si se guardó. */
  async function save(
    section: SectionKey,
    patch: Record<string, unknown>,
    rollback: () => void
  ): Promise<boolean> {
    setSaving(section);
    setError(null);
    setSaved(null);
    try {
      await api<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      setSaved(section);
      return true;
    } catch {
      rollback();
      setError("No se pudo guardar el cambio. Inténtalo de nuevo.");
      return false;
    } finally {
      setSaving(null);
    }
  }

  // ── (77) Tono ──
  function chooseTone(next: "cercano" | "formal") {
    if (next === tone || saving) return;
    const prev = tone;
    setTone(next);
    save("tone", { ai_tone: next }, () => setTone(prev));
  }

  // ── (53) Frases prohibidas ──
  function savePhrases(next: string[]) {
    const prev = phrases;
    setPhrases(next);
    save("phrases", { forbidden_phrases: next }, () => setPhrases(prev));
  }

  function addPhrase(raw: string) {
    const p = raw.trim().toLowerCase();
    if (!p || phrases.some((x) => x.toLowerCase() === p)) {
      setNewPhrase("");
      setPhraseHint(null);
      return;
    }
    // El filtro compara letras y números: una frase que casi no tiene se
    // ignora en el backend, y aquí prometemos que "nunca la dirá". Mejor
    // decirlo antes de guardarla que dejar una promesa que no se cumple.
    if (p.replace(/[^\p{L}\p{N}]/gu, "").length < MIN_FORBIDDEN_PHRASE) {
      setPhraseHint(
        `Escribe al menos ${MIN_FORBIDDEN_PHRASE} letras. Una palabra muy corta no se puede vigilar sin bloquear mensajes que no la dicen.`
      );
      return;
    }
    if (p.length > MAX_FORBIDDEN_PHRASE) {
      setPhraseHint(`La frase no puede pasar de ${MAX_FORBIDDEN_PHRASE} caracteres.`);
      return;
    }
    setNewPhrase("");
    setPhraseHint(null);
    savePhrases([...phrases, raw.trim()]);
  }

  function removePhrase(p: string) {
    savePhrases(phrases.filter((x) => x !== p));
  }

  // ── Instrucciones libres ──
  // Sin rollback: si el PATCH falla, lo último que queremos es borrarle al
  // dueño el texto que acaba de escribir. Se queda en pantalla, sin guardar,
  // y el botón sigue ofreciendo reintentar.
  async function saveInstructions() {
    const next = instructions.trim();
    const ok = await save("instructions", { ai_instructions: next }, () => {});
    if (ok) setStoredInstructions(next);
  }

  // ── (52) Modo de atención ──
  function chooseMode(next: "always" | "out_of_hours") {
    if (next === mode || saving) return;
    const prev = mode;
    setMode(next);
    save(
      "mode",
      { ai_mode: next, business_hours: next === "out_of_hours" ? hours : null },
      () => setMode(prev)
    );
  }

  function saveHours(next: BusinessHours) {
    const prev = hours;
    setHours(next);
    save("mode", { ai_mode: "out_of_hours", business_hours: next }, () => setHours(prev));
  }

  function toggleDay(d: number) {
    const next = hours.days.includes(d)
      ? hours.days.filter((x) => x !== d)
      : [...hours.days, d].sort((a, b) => a - b);
    // El backend exige al menos un día (BusinessHours.days, min_length=1).
    // Sin esta guarda el PATCH da 422 y la UI dice "Inténtalo de nuevo", que
    // es un consejo falso: reintentar no puede funcionar nunca. El botón del
    // último día queda deshabilitado y explica por qué.
    if (next.length === 0) return;
    saveHours({ ...hours, days: next });
  }

  // ── (50) Aviso de grabación ──
  function toggleRecording() {
    const next = !recording;
    setRecording(next);
    save("recording", { recording_notice: next }, () => setRecording(!next));
  }

  return (
    <div className="max-w-3xl">
      <header className="mb-8">
        <h2 className="text-2xl font-semibold">Mi agente IA</h2>
        <p className="text-sm text-slate-500">
          Decide cómo habla tu asistente, qué no puede decir y cuándo atiende.
        </p>
      </header>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* ── (77) Tono ── */}
      <section className="mb-10">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-medium">Tono del asistente</h3>
          <SavedMark section="tone" saving={saving} saved={saved} />
        </div>
        <p className="mb-4 mt-1 text-sm text-slate-500">
          Elige cómo quieres que suene con tus clientes. Se guarda al momento.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {(
            [
              { value: "cercano", title: "Cercano", desc: "Tutea y suena natural, como un compañero de la agencia." },
              { value: "formal", title: "Formal", desc: "Trata de usted y mantiene un tono profesional clásico." },
            ] as const
          ).map((t) => {
            const isSelected = tone === t.value;
            return (
              <button
                key={t.value}
                type="button"
                onClick={() => chooseTone(t.value)}
                className={`rounded-xl border bg-white p-4 text-left transition-colors ${
                  isSelected
                    ? "border-selai-500 ring-1 ring-selai-500"
                    : "border-slate-200 hover:border-selai-300"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{t.title}</span>
                  {isSelected && (
                    <span className="rounded-full bg-selai-50 px-2 py-0.5 text-xs text-selai-700">
                      Tu tono
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-slate-600">{t.desc}</p>
              </button>
            );
          })}
        </div>
        {/* Tarjeta de ejemplo: cambia con el tono elegido */}
        <div className="mt-4 rounded-xl border border-slate-200 bg-white p-4">
          <p className="mb-2 text-xs uppercase tracking-wide text-slate-400">
            Así sonará
          </p>
          <div className="rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-900">
            {tone === "cercano"
              ? `¡Hola! Soy el asistente de ${agencyName}. ¿Buscas comprar o alquilar? Cuéntame qué necesitas y te ayudo.`
              : `Buenos días. Le atiende el asistente de ${agencyName}. ¿En qué puedo ayudarle? Si lo desea, puedo informarle sobre nuestras propiedades disponibles.`}
          </div>
        </div>
      </section>

      {/* ── (53) Frases prohibidas ── */}
      <section className="mb-10">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-medium">Frases prohibidas</h3>
          <SavedMark section="phrases" saving={saving} saved={saved} />
        </div>
        <p className="mb-4 mt-1 text-sm text-slate-500">
          El asistente nunca dirá estas frases, aunque el cliente insista. Revisamos cada
          mensaje antes de enviarlo: si alguna se le escapa, el mensaje no sale, la
          conversación pasa a tu equipo y te avisamos. Da igual mayúsculas, tildes,
          singular o plural y los signos de puntuación. Escribe frases, no palabras
          sueltas muy cortas: cuanto más concreta, menos conversaciones se te pasan al
          equipo sin hacer falta.
        </p>
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          {phrases.length > 0 && (
            <div className="mb-3 flex flex-wrap gap-2">
              {phrases.map((p) => (
                <span
                  key={p}
                  className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-700"
                >
                  «{p}»
                  <button
                    type="button"
                    onClick={() => removePhrase(p)}
                    aria-label={`Quitar «${p}»`}
                    className="text-slate-400 hover:text-red-600"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              addPhrase(newPhrase);
            }}
            className="flex flex-wrap items-center gap-2"
          >
            <input
              value={newPhrase}
              onChange={(e) => {
                setNewPhrase(e.target.value);
                setPhraseHint(null);
              }}
              placeholder={`Ej. "${SUGGESTED_PHRASES[0]}"`}
              maxLength={MAX_FORBIDDEN_PHRASE}
              aria-invalid={phraseHint ? true : undefined}
              aria-describedby={phraseHint ? "phrase-hint" : undefined}
              className="w-full max-w-sm rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
              aria-label="Nueva frase prohibida"
            />
            <button
              type="submit"
              disabled={!newPhrase.trim() || saving === "phrases"}
              className="rounded-md bg-selai-600 px-3 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
            >
              Añadir
            </button>
          </form>
          {phraseHint && (
            <p id="phrase-hint" role="status" className="mt-2 text-sm text-amber-700">
              {phraseHint}
            </p>
          )}
          {/* Sugerencias del sector, precargadas como un clic */}
          {SUGGESTED_PHRASES.some(
            (sug) => !phrases.some((p) => p.toLowerCase() === sug.toLowerCase())
          ) && (
            <div className="mt-4 border-t border-slate-100 pt-3">
              <p className="mb-2 text-xs text-slate-500">
                Sugerencias típicas del sector — toca para añadir:
              </p>
              <div className="flex flex-wrap gap-2">
                {SUGGESTED_PHRASES.filter(
                  (sug) => !phrases.some((p) => p.toLowerCase() === sug.toLowerCase())
                ).map((sug) => (
                  <button
                    key={sug}
                    type="button"
                    onClick={() => addPhrase(sug)}
                    className="rounded-full border border-dashed border-slate-300 px-3 py-1 text-sm text-slate-500 hover:border-selai-400 hover:text-selai-700"
                  >
                    + «{sug}»
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {/* ── Instrucciones libres para el asistente ── */}
      <section className="mb-10">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-medium">Instrucciones para tu asistente</h3>
          <SavedMark section="instructions" saving={saving} saved={saved} />
        </div>
        <p className="mb-4 mt-1 text-sm text-slate-500">
          Cuéntale cómo trabajáis vosotros, con tus palabras. Lo tendrá en cuenta en{" "}
          <strong>todas</strong> las conversaciones, desde ahora y hasta que lo cambies.
          Si solo quieres corregirle en un cliente concreto, hazlo desde la caja de nota
          que hay dentro de esa conversación.
        </p>
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <label htmlFor="ai-instructions" className="sr-only">
            Instrucciones para el asistente
          </label>
          <textarea
            id="ai-instructions"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value.slice(0, MAX_AI_INSTRUCTIONS))}
            rows={6}
            placeholder="Ej. Antes de proponer una visita, pregunta siempre si necesita hipoteca."
            className="w-full resize-y rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
          />
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
            <span
              className={`text-xs ${
                instructions.length > MAX_AI_INSTRUCTIONS - 100
                  ? "text-amber-700"
                  : "text-slate-400"
              }`}
            >
              {instructions.length}/{MAX_AI_INSTRUCTIONS} caracteres
            </span>
            <button
              type="button"
              onClick={saveInstructions}
              disabled={saving === "instructions" || instructions.trim() === storedInstructions}
              className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
            >
              {saving === "instructions" ? "Guardando…" : "Guardar instrucciones"}
            </button>
          </div>

          <div className="mt-4 border-t border-slate-100 pt-3">
            <p className="mb-2 text-xs text-slate-500">
              Ejemplos que suelen funcionar — toca para añadirlos:
            </p>
            <div className="flex flex-col gap-1.5">
              {INSTRUCTION_EXAMPLES.filter((ex) => !instructions.includes(ex)).map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() =>
                    setInstructions((current) =>
                      (current.trim() ? `${current.trim()}\n${ex}` : ex).slice(
                        0,
                        MAX_AI_INSTRUCTIONS
                      )
                    )
                  }
                  className="rounded-lg border border-dashed border-slate-300 px-3 py-2 text-left text-sm text-slate-500 hover:border-selai-400 hover:text-selai-700"
                >
                  + {ex}
                </button>
              ))}
            </div>
          </div>

          <p className="mt-4 text-xs text-slate-500">
            Estas instrucciones ajustan cómo trabaja, no sustituyen a sus normas: seguirá sin
            inventarse pisos ni precios, sin ocultar que es una IA y sin decir ninguna de tus
            frases prohibidas, aunque aquí escribas lo contrario.
          </p>
        </div>
      </section>

      {/* ── (52) Modo de atención ── */}
      <section className="mb-10">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-medium">¿Cuándo atiende tu asistente?</h3>
          <SavedMark section="mode" saving={saving} saved={saved} />
        </div>
        <p className="mb-4 mt-1 text-sm text-slate-500">
          Puede atender siempre, o solo cuando tu equipo no está disponible.
        </p>
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <div className="space-y-2">
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="ai_mode"
                checked={mode === "always"}
                onChange={() => chooseMode("always")}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium">Siempre (24/7)</span>
                <span className="block text-slate-500">
                  El asistente responde a cualquier hora, todos los días.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="ai_mode"
                checked={mode === "out_of_hours"}
                onChange={() => chooseMode("out_of_hours")}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium">Solo fuera de horario</span>
                <span className="block text-slate-500">
                  Dentro de tu horario atiende tu equipo; fuera, entra el asistente.
                </span>
              </span>
            </label>
          </div>

          {mode === "out_of_hours" && (
            <div className="mt-4 border-t border-slate-100 pt-4">
              <p className="mb-3 text-sm font-medium text-slate-800">
                Tu horario de atención (el asistente cubre el resto)
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-sm text-slate-600">
                  De
                  <input
                    type="time"
                    value={hours.start}
                    onChange={(e) => saveHours({ ...hours, start: e.target.value })}
                    className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  />
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-600">
                  a
                  <input
                    type="time"
                    value={hours.end}
                    onChange={(e) => saveHours({ ...hours, end: e.target.value })}
                    className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  />
                </label>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {DAYS.map((d) => {
                  const active = hours.days.includes(d.value);
                  const isLastActiveDay = active && hours.days.length === 1;
                  return (
                    <button
                      key={d.value}
                      type="button"
                      onClick={() => toggleDay(d.value)}
                      disabled={isLastActiveDay}
                      title={isLastActiveDay ? "Tiene que quedar al menos un día" : d.name}
                      aria-pressed={active}
                      className={`h-9 w-9 rounded-full text-sm font-medium transition-colors ${
                        active
                          ? "bg-selai-600 text-white"
                          : "border border-slate-300 text-slate-500 hover:border-selai-400"
                      } ${isLastActiveDay ? "cursor-not-allowed" : ""}`}
                    >
                      {d.label}
                    </button>
                  );
                })}
              </div>
              {hours.days.length === 1 && (
                <p className="mt-2 text-xs text-slate-500">
                  Tiene que quedar al menos un día. Marca otro antes de quitar este.
                </p>
              )}
            </div>
          )}
        </div>
      </section>

      {/* ── (50) Aviso de grabación ── */}
      <section>
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-medium">Aviso de grabación en llamadas</h3>
          <SavedMark section="recording" saving={saving} saved={saved} />
        </div>
        <p className="mb-4 mt-1 text-sm text-slate-500">
          Si lo activas, el asistente avisa al empezar la llamada de que puede ser grabada.
        </p>
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <label className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              checked={recording}
              onChange={toggleRecording}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">
                Avisar de que la llamada puede ser grabada
              </span>
              <span className="mt-1 block text-xs text-slate-500">
                Recomendado: la normativa exige informar de la grabación a quien llama.
              </span>
            </span>
          </label>
        </div>
      </section>
    </div>
  );
}

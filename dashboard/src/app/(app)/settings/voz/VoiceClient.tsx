// Client half of "Voz del agente": voice catalog selection (PUT /voice),
// voice cloning (multipart POST /voice-cloning) and the AI phone number
// (PATCH /settings). Copy in plain Spanish for small agencies.
"use client";

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  AgencySettings,
  ApplyVoiceOut,
  CloneVoiceOut,
  VoiceCatalogItem,
} from "@/lib/agency-settings";
import VoicePreview from "./VoicePreview";

const GENDER_LABEL: Record<string, string> = {
  male: "Masculina",
  female: "Femenina",
  neutral: "Neutra",
};

export default function VoiceClient({
  agencyId,
  settings,
  catalog,
}: {
  agencyId: string;
  settings: AgencySettings;
  catalog: VoiceCatalogItem[];
}) {
  const s = settings.settings;

  // --- Sección 1: catálogo -------------------------------------------------
  const [selectedId, setSelectedId] = useState<string | null>(s.voice_id);
  const [savingVoice, setSavingVoice] = useState<string | null>(null);
  const [savedVoice, setSavedVoice] = useState<string | null>(null);
  const [pendingVapi, setPendingVapi] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  async function chooseVoice(v: VoiceCatalogItem) {
    if (savingVoice) return;
    setSavingVoice(v.id);
    setVoiceError(null);
    setSavedVoice(null);
    try {
      const out = await api<ApplyVoiceOut>(`/api/v1/agencies/${agencyId}/voice`, {
        method: "PUT",
        body: JSON.stringify({ provider: v.provider, voice_id: v.id, voice_name: v.name }),
      });
      setSelectedId(v.id);
      setSavedVoice(v.id);
      setPendingVapi(!out.applied_to_vapi);
    } catch {
      setVoiceError("No se pudo guardar la voz. Inténtalo de nuevo.");
    } finally {
      setSavingVoice(null);
    }
  }

  // --- Sección 2: clonación ------------------------------------------------
  const fileRef = useRef<HTMLInputElement>(null);
  const [cloneName, setCloneName] = useState("");
  const [consent, setConsent] = useState(false);
  const [cloning, setCloning] = useState(false);
  const [cloned, setCloned] = useState<CloneVoiceOut | null>(null);
  const [cloneError, setCloneError] = useState<string | null>(null);

  async function cloneVoice(e: React.FormEvent) {
    e.preventDefault();
    const files = fileRef.current?.files;
    if (!files || files.length === 0) {
      setCloneError("Sube al menos un audio.");
      return;
    }
    setCloning(true);
    setCloneError(null);
    try {
      const fd = new FormData();
      fd.append("name", cloneName);
      fd.append("consent_signed", "true");
      Array.from(files).forEach((f) => fd.append("files", f));
      const out = await api<CloneVoiceOut>(
        `/api/v1/agencies/${agencyId}/voice-cloning`,
        { method: "POST", body: fd }
      );
      setCloned(out);
    } catch {
      setCloneError(
        "No se pudo clonar la voz. Comprueba que los audios se oyen bien e inténtalo de nuevo."
      );
    } finally {
      setCloning(false);
    }
  }

  const activeClonedName = cloned?.name ?? s.elevenlabs_voice_name;

  // --- Sección 3: número ---------------------------------------------------
  const [phone, setPhone] = useState(s.ai_phone_number ?? "");
  const [savedPhone, setSavedPhone] = useState(s.ai_phone_number ?? "");
  const [savingPhone, setSavingPhone] = useState(false);
  const [phoneMsg, setPhoneMsg] = useState<string | null>(null);
  const [phoneError, setPhoneError] = useState<string | null>(null);

  async function savePhone(e: React.FormEvent) {
    e.preventDefault();
    setSavingPhone(true);
    setPhoneMsg(null);
    setPhoneError(null);
    try {
      const out = await api<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`, {
        method: "PATCH",
        body: JSON.stringify({ ai_phone_number: phone.trim() || null }),
      });
      setSavedPhone(out.settings.ai_phone_number ?? "");
      setPhoneMsg("Número guardado ✓");
    } catch {
      setPhoneError("No se pudo guardar el número. Inténtalo de nuevo.");
    } finally {
      setSavingPhone(false);
    }
  }

  return (
    <div className="max-w-3xl">
      <header className="mb-8">
        <h2 className="text-2xl font-semibold">Voz del agente</h2>
        <p className="text-sm text-slate-500">
          Elige cómo suena tu asistente al teléfono y gestiona su número.
        </p>
      </header>

      {/* ── Sección 1: elegir voz ── */}
      <section className="mb-10">
        <h3 className="text-lg font-medium">Elige una voz</h3>
        <p className="mb-4 mt-1 text-sm text-slate-500">
          Haz clic en una voz para que sea la de tu asistente. Se guarda al momento.
        </p>
        {voiceError && (
          <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {voiceError}
          </div>
        )}
        {catalog.length === 0 ? (
          <p className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
            Aún no hay voces disponibles. Escríbenos a hola@selai.es y te ayudamos.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {catalog.map((v) => {
              const isSelected = v.id === selectedId;
              return (
                <div
                  key={v.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => chooseVoice(v)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      chooseVoice(v);
                    }
                  }}
                  className={`cursor-pointer rounded-xl border bg-white p-4 text-left transition-colors ${
                    savingVoice !== null ? "opacity-70" : ""
                  } ${
                    isSelected
                      ? "border-selai-500 ring-1 ring-selai-500"
                      : "border-slate-200 hover:border-selai-300"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{v.name}</span>
                    {isSelected && (
                      <span className="rounded-full bg-selai-50 px-2 py-0.5 text-xs text-selai-700">
                        {savedVoice === v.id ? "Guardada ✓" : "Tu voz"}
                      </span>
                    )}
                    {savingVoice === v.id && (
                      <span className="text-xs text-slate-400">Guardando…</span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-slate-600">{v.description}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                      {v.language}
                    </span>
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                      {GENDER_LABEL[v.gender] ?? v.gender}
                    </span>
                    {v.preview_url && (
                      <audio
                        controls
                        preload="none"
                        src={v.preview_url}
                        className="mt-1 h-8 w-full"
                        onClick={(e) => e.stopPropagation()}
                      />
                    )}
                    {/* Vista previa generada solo para voces de ElevenLabs;
                        para cartesia no mostramos el botón. */}
                    {v.provider === "11labs" && (
                      <VoicePreview agencyId={agencyId} voiceId={v.id} />
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {pendingVapi && (
          <p className="mt-3 text-xs text-slate-500">
            Voz guardada. Se aplicará cuando actives la voz de tu asistente.
          </p>
        )}
      </section>

      {/* ── Sección 2: clonación ── */}
      <section className="mb-10">
        <h3 className="text-lg font-medium">Usa tu propia voz</h3>
        <p className="mb-4 mt-1 text-sm text-slate-500">
          ¿Quieres que el asistente suene como tú? Sube entre 1 y 3 audios de 30 segundos a 2
          minutos leyendo cualquier texto con naturalidad, y crearemos una copia de tu voz.
        </p>

        {activeClonedName && (
          <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            <div className="flex flex-wrap items-center gap-2">
              <span>
                Voz clonada activa: <strong>{activeClonedName}</strong>
              </span>
              {(cloned?.voice_id ?? s.elevenlabs_voice_id) && (
                <VoicePreview
                  agencyId={agencyId}
                  voiceId={(cloned?.voice_id ?? s.elevenlabs_voice_id)!}
                />
              )}
            </div>
          </div>
        )}

        <form
          onSubmit={cloneVoice}
          className="rounded-xl border border-slate-200 bg-white p-5"
        >
          <label className="mb-1 block text-sm font-medium">¿De quién es la voz?</label>
          <input
            required
            minLength={2}
            value={cloneName}
            onChange={(e) => setCloneName(e.target.value)}
            placeholder="Ej. María, la dueña de la agencia"
            className="mb-4 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />

          <label className="mb-1 block text-sm font-medium">Audios (1 a 3)</label>
          <input
            ref={fileRef}
            type="file"
            accept="audio/*"
            multiple
            required
            className="mb-4 block w-full text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-selai-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-selai-700 hover:file:bg-selai-100"
          />

          <label className="mb-4 flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              required
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              Confirmo que soy la persona de la voz o tengo su consentimiento explícito por
              escrito.
            </span>
          </label>

          {cloneError && (
            <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {cloneError}
            </div>
          )}

          <button
            type="submit"
            disabled={cloning || !consent}
            className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
          >
            {cloning ? "Clonando tu voz… puede tardar un poco" : "Clonar mi voz"}
          </button>

          {cloned && (
            <p className="mt-3 text-sm text-emerald-700">
              ¡Listo! Tu voz «{cloned.name}» se ha clonado y ya es la de tu asistente.
            </p>
          )}
        </form>
      </section>

      {/* ── Sección 3: número ── */}
      <section id="numero" className="scroll-mt-8">
        <h3 className="text-lg font-medium">El número de tu IA</h3>
        <p className="mb-4 mt-1 text-sm text-slate-500">
          El número te lo asigna selai al activar el plan de voz. Si aún no lo tienes,
          escríbenos a{" "}
          <a href="mailto:hola@selai.es" className="text-selai-600 underline">
            hola@selai.es
          </a>
          .
        </p>

        <div className="rounded-xl border border-slate-200 bg-white p-5">
          {savedPhone && (
            <p className="mb-4 text-3xl font-semibold tracking-wide text-slate-900">
              {savedPhone}
            </p>
          )}
          <form onSubmit={savePhone} className="flex flex-wrap items-center gap-2">
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+34 900 000 000"
              className="w-56 rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
            <button
              type="submit"
              disabled={savingPhone}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-60"
            >
              {savingPhone ? "Guardando…" : "Guardar"}
            </button>
            {phoneMsg && <span className="text-sm text-emerald-700">{phoneMsg}</span>}
            {phoneError && <span className="text-sm text-red-600">{phoneError}</span>}
          </form>

          <div className="mt-5 border-t border-slate-200 pt-4 text-sm text-slate-600">
            <p className="mb-2 font-medium text-slate-800">Cómo desviar tus llamadas</p>
            <ul className="space-y-1.5">
              <li>
                Desviar todas las llamadas:{" "}
                <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[13px]">
                  **21*{savedPhone || "NÚMERO"}#
                </code>
              </li>
              <li>
                Desviar solo si no contestas:{" "}
                <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[13px]">
                  *61*{savedPhone || "NÚMERO"}#
                </code>
              </li>
              <li>
                Quitar el desvío:{" "}
                <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[13px]">
                  ##21#
                </code>
              </li>
            </ul>
          </div>
        </div>
      </section>
    </div>
  );
}

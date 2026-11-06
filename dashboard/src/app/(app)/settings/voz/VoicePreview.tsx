// Mini-reproductor "Escuchar" para voces de ElevenLabs (provider "11labs").
// Pide el audio al endpoint de preview (POST /voice/preview → audio/mpeg
// binario) a través del proxy /api/backend y lo reproduce con un objectURL.
// Si el backend devuelve 501, mostramos "Vista previa no disponible todavía".
"use client";

import { useEffect, useRef, useState } from "react";

const DEFAULT_TEXT = "Hola, soy el asistente de tu inmobiliaria. ¿En qué puedo ayudarte?";
const MAX_CHARS = 200;

export default function VoicePreview({
  agencyId,
  voiceId,
  provider = "11labs",
}: {
  agencyId: string;
  voiceId: string;
  provider?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(DEFAULT_TEXT);
  const [busy, setBusy] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);

  // Libera el objectURL anterior al generar otro o al desmontar.
  useEffect(() => {
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);

  async function generate(previewText: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/backend/api/v1/agencies/${agencyId}/voice/preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider,
            voice_id: voiceId,
            text: previewText.slice(0, MAX_CHARS),
          }),
        }
      );
      if (res.status === 501) {
        setError("Vista previa no disponible todavía.");
        return;
      }
      if (!res.ok) {
        setError("No se pudo generar la vista previa. Inténtalo de nuevo.");
        return;
      }
      const blob = await res.blob();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      setAudioUrl(url);
    } catch {
      setError("No se pudo generar la vista previa. Inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
          void generate(DEFAULT_TEXT);
        }}
        className="rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-xs text-slate-600 hover:border-selai-300 hover:text-selai-700"
      >
        🔊 Escuchar
      </button>
    );
  }

  return (
    <div
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
      className="mt-2 w-full rounded-md border border-slate-200 bg-slate-50 p-2.5"
    >
      <div className="flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, MAX_CHARS))}
          maxLength={MAX_CHARS}
          placeholder={DEFAULT_TEXT}
          className="flex-1 rounded border border-slate-300 bg-white px-2 py-1 text-xs focus:border-selai-500 focus:outline-none"
          aria-label="Texto de la vista previa"
        />
        <button
          type="button"
          onClick={() => void generate(text.trim() || DEFAULT_TEXT)}
          disabled={busy}
          className="rounded bg-selai-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-selai-700 disabled:opacity-50"
        >
          {busy ? "Generando…" : "Escuchar"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-xs text-slate-400 hover:text-slate-600"
          aria-label="Cerrar vista previa"
        >
          ✕
        </button>
      </div>
      <p className="mt-1 text-right text-[10px] text-slate-400">
        {text.length}/{MAX_CHARS}
      </p>
      {error && <p className="mt-1 text-xs text-amber-700">{error}</p>}
      {audioUrl && !error && (
        <audio controls autoPlay src={audioUrl} className="mt-1 h-8 w-full" />
      )}
    </div>
  );
}

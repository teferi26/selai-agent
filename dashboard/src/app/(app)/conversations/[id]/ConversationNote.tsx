// Nota puntual para la IA en ESTA conversación.
//
// Es el hermano pequeño de "Instrucciones para tu asistente" (/settings/ia):
// aquello vale para siempre y para todos los clientes, esto solo para este
// hilo. La diferencia se explica aquí mismo porque es justo donde el dueño
// duda: "¿lo escribo aquí o allí?".
//
// POST /conversations/{id}/note guarda (y reemplaza) la nota; un cuerpo vacío
// la borra. El agente la lee en su siguiente turno.
"use client";

import { useState } from "react";
import { ErrorState } from "@/components/ui";
import { api } from "@/lib/api";
import { MAX_CONVERSATION_NOTE, type ConversationNote as Note } from "@/lib/agency-settings";

export default function ConversationNote({
  agencyId,
  conversationId,
  initial,
}: {
  agencyId: string;
  conversationId: string;
  /** Nota guardada hoy. null cuando no hemos podido leerla. */
  initial: Note | null;
}) {
  const [text, setText] = useState(initial?.text ?? "");
  const [stored, setStored] = useState(initial?.text ?? "");
  // Desde cuándo está puesta. Una nota vieja que nadie recuerda haber escrito
  // sigue mandando en cada respuesta, así que se dice a la cara.
  const [updatedAt, setUpdatedAt] = useState<string | null>(initial?.updated_at ?? null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = text.trim() !== stored;

  async function save(next: string) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const out = await api<Note>(
        `/api/v1/agencies/${agencyId}/conversations/${conversationId}/note`,
        { method: "POST", body: JSON.stringify({ text: next }) }
      );
      // Nos quedamos con lo que confirma el servidor, no con lo que enviamos:
      // el backend recorta espacios y saltos de línea.
      setStored(out.text);
      setText(out.text);
      setUpdatedAt(out.updated_at);
      setSaved(true);
    } catch {
      // Sin rollback a propósito: el texto escrito se queda en pantalla.
      setError("No se ha podido guardar la nota. Inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium text-slate-900">
          Indicarle algo a la IA en esta conversación
        </h3>
        {busy && <span className="text-xs text-slate-400">Guardando…</span>}
        {!busy && saved && <span className="text-xs text-emerald-700">Guardado ✓</span>}
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Se aplica solo a este cliente, desde su próxima respuesta y en todas las
        siguientes, hasta que la quites. Para cambiar cómo habla con todo el mundo, usa{" "}
        <a href="/settings/ia" className="text-selai-700 underline">
          Mi agente IA
        </a>
        .
      </p>

      <label htmlFor="conversation-note" className="sr-only">
        Nota para la IA en esta conversación
      </label>
      <textarea
        id="conversation-note"
        value={text}
        onChange={(e) => setText(e.target.value.slice(0, MAX_CONVERSATION_NOTE))}
        rows={2}
        placeholder="Ej. El ático de Goya ya está reservado, no se lo ofrezcas."
        className="mt-3 w-full resize-y rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
      />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-slate-400">
          {text.length}/{MAX_CONVERSATION_NOTE}
        </span>
        <div className="flex items-center gap-2">
          {stored && (
            <button
              type="button"
              onClick={() => void save("")}
              disabled={busy}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:border-slate-400 disabled:opacity-60"
            >
              Quitar nota
            </button>
          )}
          <button
            type="button"
            onClick={() => void save(text.trim())}
            disabled={busy || !dirty}
            className="rounded-md bg-selai-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
          >
            Guardar nota
          </button>
        </div>
      </div>

      {stored && !dirty && (
        <p className="mt-2 text-xs text-slate-500">
          La IA la tiene en cuenta en cada respuesta
          {updatedAt ? ` desde el ${new Date(updatedAt).toLocaleString("es-ES")}` : ""} y
          seguirá haciéndolo hasta que la quites. No puede saltarse tus frases prohibidas
          ni sus normas, aunque aquí le pidas lo contrario.
        </p>
      )}

      {error && (
        <ErrorState
          className="mt-3"
          title="No se ha guardado"
          message={error}
          onRetry={() => void save(text.trim())}
          retrying={busy}
        />
      )}
    </div>
  );
}

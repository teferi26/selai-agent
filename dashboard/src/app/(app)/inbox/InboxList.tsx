// Lista de la bandeja con "Cargar más".
//
// El servidor pinta la primera página (rápida y ya autenticada) y a partir de
// ahí este componente va pidiendo las siguientes con el cursor que devuelve el
// backend. Antes la bandeja se cortaba en 50 conversaciones sin decirlo: a
// partir de ahí, sencillamente no existían.
"use client";

import { type InboxItem, api } from "@/lib/api";
import { ErrorState, Spinner } from "@/components/ui";
import Link from "next/link";
import { useState } from "react";

/** Respuesta de /inbox cuando se pide paginada. */
type InboxPage = {
  items: InboxItem[];
  next_cursor: string | null;
};

const TEMP_BADGE: Record<string, string> = {
  hot: "bg-red-100 text-red-700",
  warm: "bg-amber-100 text-amber-700",
  cold: "bg-slate-100 text-slate-600",
};

const CHANNEL_ICON: Record<string, string> = {
  whatsapp: "💬",
  email: "📧",
  voice: "📞",
  sms: "✉️",
  web: "🌐",
};

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

export default function InboxList({
  agencyId,
  initialItems,
  initialCursor,
  query,
  hasFilters,
}: {
  agencyId: string;
  initialItems: InboxItem[];
  initialCursor: string | null;
  /** Filtros activos, ya serializados: las páginas siguientes usan los mismos. */
  query: string;
  hasFilters: boolean;
}) {
  const [items, setItems] = useState(initialItems);
  const [cursor, setCursor] = useState(initialCursor);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  async function loadMore() {
    if (!cursor || loading) return;
    setLoading(true);
    setFailed(false);
    try {
      const params = new URLSearchParams(query);
      params.set("paginated", "true");
      params.set("cursor", cursor);
      const page = await api<InboxPage>(
        `/api/v1/agencies/${agencyId}/inbox?${params.toString()}`
      );
      // Puede llegar un mensaje nuevo mientras paginamos y mover una
      // conversación a la primera página: filtramos por id para no pintarla
      // dos veces en la lista.
      setItems((current) => {
        const seen = new Set(current.map((it) => it.conversation_id));
        return [...current, ...page.items.filter((it) => !seen.has(it.conversation_id))];
      });
      setCursor(page.next_cursor);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  const todayCount = items.filter((it) => isToday(it.last_message_at)).length;
  const hotCount = items.filter((it) => it.lead_temperature === "hot").length;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm text-slate-500">
          {items.length} conversaci{items.length === 1 ? "ón" : "ones"}
          {cursor ? " cargadas" : ""}
        </span>
      </div>

      {/* Resumen diario. Se calcula sobre lo cargado, así que lo decimos. */}
      <div className="mb-4 flex flex-wrap items-center gap-x-2 rounded-lg border border-selai-100 bg-selai-50 px-4 py-2.5 text-sm text-selai-900">
        <span className="font-medium">Hoy:</span>
        <span>
          {todayCount} conversaci{todayCount === 1 ? "ón" : "ones"}
        </span>
        <span aria-hidden className="text-selai-300">
          ·
        </span>
        <span>
          {hotCount} lead{hotCount === 1 ? "" : "s"} caliente{hotCount === 1 ? "" : "s"}
        </span>
        {cursor && (
          <span className="text-selai-500">— sobre las conversaciones cargadas</span>
        )}
      </div>

      <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
        {items.map((it) => (
          <li key={it.conversation_id}>
            <Link
              href={`/conversations/${it.conversation_id}`}
              className="flex items-center gap-4 px-4 py-3 hover:bg-slate-50"
            >
              <span className="text-2xl">{CHANNEL_ICON[it.channel] || "💬"}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium truncate">{it.lead_name || "Sin nombre"}</span>
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${TEMP_BADGE[it.lead_temperature]}`}
                    title={
                      it.lead_temperature === "hot"
                        ? "Caliente: presupuesto definido + búsqueda concreta + urgencia"
                        : undefined
                    }
                  >
                    {it.lead_temperature}
                  </span>
                  {it.unread && <span className="w-2 h-2 rounded-full bg-blue-500" />}
                  {it.status === "handed_off" && (
                    <span className="text-xs text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded">
                      humano
                    </span>
                  )}
                </div>
                <p className="text-sm text-slate-600 truncate">
                  {it.last_message_role !== "lead" && "→ "}
                  {it.last_message_preview}
                </p>
              </div>
              <time className="text-xs text-slate-400 whitespace-nowrap">
                {new Date(it.last_message_at).toLocaleString("es-ES", {
                  dateStyle: "short",
                  timeStyle: "short",
                })}
              </time>
            </Link>
          </li>
        ))}
        {items.length === 0 && (
          <li className="px-4 py-8 text-center text-sm text-slate-500">
            {hasFilters
              ? "Ninguna conversación coincide con los filtros. Prueba a quitarlos."
              : "No hay conversaciones todavía. Cuando llegue el primer WhatsApp aparecerá aquí."}
          </li>
        )}
      </ul>

      {failed && (
        <ErrorState
          className="mt-3"
          title="No hemos podido cargar más conversaciones"
          message="La lista que ves sigue siendo válida."
          onRetry={loadMore}
          retrying={loading}
        />
      )}

      {cursor && (
        <div className="mt-4 flex justify-center">
          <button
            onClick={loadMore}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-60"
          >
            {loading && <Spinner size={16} className="text-selai-600" label={null} />}
            {loading ? "Cargando…" : "Cargar más"}
          </button>
        </div>
      )}

      {!cursor && items.length > 0 && (
        <p className="mt-4 text-center text-xs text-slate-400">
          No hay más conversaciones.
        </p>
      )}
    </div>
  );
}

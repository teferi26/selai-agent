// (166) Llamadas: consume GET /calls, que sí trae grabación, duración y
// resumen (el /inbox que usábamos antes no los tiene y dejaba la lista muda).
import { EmptyState } from "@/components/ui";
import { formatDuration, type CallItem } from "@/lib/api";
import { getAgencyId, serverApi } from "@/lib/server-api";
import Link from "next/link";

const DATE_FORMAT: Intl.DateTimeFormatOptions = {
  dateStyle: "short",
  timeStyle: "short",
};

export default async function CallsPage() {
  const agencyId = await getAgencyId();
  const items = await serverApi<CallItem[]>(
    `/api/v1/agencies/${agencyId}/calls?limit=100`
  );

  return (
    <div className="max-w-4xl">
      <header className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold">Llamadas</h2>
          <p className="mt-0.5 text-sm text-slate-500">
            Cada llamada que ha atendido tu IA, con la grabación y lo que sacó en
            claro.
          </p>
        </div>
        <span className="whitespace-nowrap text-sm text-slate-500">
          {items.length} llamada{items.length === 1 ? "" : "s"}
        </span>
      </header>

      {items.length === 0 ? (
        <EmptyState
          title="Aún no hay ninguna llamada registrada."
          subtitle="Cuando tu IA atienda su primera llamada, aquí verás la grabación, lo que duró y un resumen de lo que habló con el cliente."
          ctaLabel="Conectar el teléfono"
          ctaHref="/setup"
        >
          <Link
            href="/probar"
            className="text-sm text-selai-600 underline hover:text-selai-700"
          >
            Probar mi IA
          </Link>
        </EmptyState>
      ) : (
        <ul className="space-y-3">
          {items.map((call, index) => {
            const duration = formatDuration(call.duration_seconds);
            const who = call.lead_name || call.lead_phone || "Cliente sin identificar";
            return (
              <li
                key={call.call_id || `${call.conversation_id}-${index}`}
                className="rounded-xl border border-slate-200 bg-white p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span aria-hidden>📞</span>
                      <span className="font-medium text-slate-900">{who}</span>
                      {call.lead_name && call.lead_phone && (
                        <a
                          href={`tel:${call.lead_phone}`}
                          className="text-sm text-selai-600 hover:underline"
                        >
                          {call.lead_phone}
                        </a>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {call.ended_at
                        ? new Date(call.ended_at).toLocaleString("es-ES", DATE_FORMAT)
                        : "Sin fecha de fin"}
                      {duration ? ` · ${duration}` : " · duración no disponible"}
                    </p>
                  </div>
                  <Link
                    href={`/conversations/${call.conversation_id}`}
                    className="whitespace-nowrap rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
                  >
                    Ver conversación
                  </Link>
                </div>

                <p className="mt-3 text-sm text-slate-700">
                  {call.transcript_summary || (
                    <span className="italic text-slate-400">
                      Sin resumen de esta llamada.
                    </span>
                  )}
                </p>

                {call.recording_url ? (
                  <audio
                    controls
                    preload="none"
                    src={call.recording_url}
                    className="mt-3 h-9 w-full max-w-md"
                    aria-label={`Grabación de la llamada de ${who}`}
                  />
                ) : (
                  <p className="mt-3 text-xs text-slate-400">
                    Esta llamada no tiene grabación disponible.
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

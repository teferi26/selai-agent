// (167) Agenda de visitas. La IA agenda visitas y escribe un briefing previo
// para el comercial; hasta ahora no había ninguna pantalla que los mostrara.
//
// Cargamos desde el comienzo del día de hoy: es una agenda, no un histórico.
// El corte lo calcula el backend (`upcoming=true`) porque el "hoy" que vale
// es el de la agencia, no el del servidor que renderiza esta página — que en
// producción va en UTC. Por la misma razón, la hora y el día de cada visita
// se pintan con `visit.timezone`.
import { EmptyState } from "@/components/ui";
import { dayKeyIn, timeIn, type VisitItem } from "@/lib/api";
import { getAgencyId, serverApi } from "@/lib/server-api";

const STATUS_LABEL: Record<VisitItem["status"], string> = {
  requested: "Por confirmar",
  confirmed: "Confirmada",
  rescheduled: "Reprogramada",
  cancelled: "Cancelada",
  no_show: "No se presentó",
  completed: "Realizada",
};

const STATUS_STYLE: Record<VisitItem["status"], string> = {
  requested: "bg-amber-50 text-amber-800",
  confirmed: "bg-emerald-50 text-emerald-800",
  rescheduled: "bg-blue-50 text-blue-800",
  cancelled: "bg-slate-100 text-slate-500",
  no_show: "bg-red-50 text-red-700",
  completed: "bg-slate-100 text-slate-600",
};

/** Tope de la petición. Si se llena, se dice en pantalla en vez de dar un
 * recuento que se queda corto sin avisar. */
const LIMIT = 200;

/** Día natural siguiente a una clave "YYYY-MM-DD". Aritmética de calendario
 * pura: sumar 24 horas se equivocaría en los cambios de hora. */
function nextDayKey(key: string): string {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10);
}

function dayLabel(key: string, todayKey: string): string {
  if (key === todayKey) return "Hoy";
  if (key === nextDayKey(todayKey)) return "Mañana";
  const [year, month, day] = key.split("-").map(Number);
  // La clave ya es un día natural: se formatea en UTC para que no se mueva.
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("es-ES", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
}

export default async function VisitasPage() {
  const agencyId = await getAgencyId();

  const visits = await serverApi<VisitItem[]>(
    `/api/v1/agencies/${agencyId}/visits?upcoming=true&limit=${LIMIT}`
  );

  // Todas las visitas son de la misma agencia, así que comparten zona horaria.
  const timeZone = visits[0]?.timezone ?? "Europe/Madrid";
  const todayKey = dayKeyIn(new Date(), timeZone);

  // Agrupación por día conservando el orden ascendente que da el backend.
  const groups: { key: string; items: VisitItem[] }[] = [];
  for (const visit of visits) {
    const key = dayKeyIn(new Date(visit.scheduled_at), visit.timezone || timeZone);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(visit);
    else groups.push({ key, items: [visit] });
  }

  return (
    <div className="max-w-3xl">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold">Visitas</h2>
          <p className="mt-0.5 text-sm text-slate-500">
            Tu agenda a partir de hoy, con tu horario. Los días anteriores no
            aparecen aquí.
          </p>
        </div>
        <span className="whitespace-nowrap text-sm text-slate-500">
          {visits.length >= LIMIT
            ? `Primeras ${LIMIT} visitas`
            : `${visits.length} visita${visits.length === 1 ? "" : "s"}`}
        </span>
      </header>

      {visits.length === 0 ? (
        <EmptyState
          title="No hay ninguna visita agendada."
          subtitle="Cuando tu IA cierre una visita con un cliente, aparecerá aquí con la hora, el teléfono y un resumen de lo que le importa a esa persona."
          ctaLabel="Probar mi IA"
          ctaHref="/probar"
        />
      ) : (
        <div className="space-y-6">
          {groups.map((group) => (
            <section key={group.key}>
              <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
                {dayLabel(group.key, todayKey)}
              </h3>
              <ul className="space-y-2">
                {group.items.map((visit) => {
                  const cancelled = visit.status === "cancelled";
                  return (
                    <li
                      key={visit.id}
                      className={`rounded-xl border border-slate-200 bg-white p-4 ${
                        cancelled ? "opacity-60" : ""
                      }`}
                    >
                      <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
                        <time
                          dateTime={visit.scheduled_at}
                          className="text-lg font-semibold tabular-nums text-slate-900"
                        >
                          {timeIn(
                            new Date(visit.scheduled_at),
                            visit.timezone || timeZone
                          )}
                        </time>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-slate-900">
                              {visit.lead_name || "Cliente sin nombre"}
                            </span>
                            {visit.lead_phone && (
                              <a
                                href={`tel:${visit.lead_phone}`}
                                className="text-sm text-selai-600 hover:underline"
                              >
                                {visit.lead_phone}
                              </a>
                            )}
                            <span
                              className={`rounded px-1.5 py-0.5 text-xs ${
                                STATUS_STYLE[visit.status]
                              }`}
                            >
                              {STATUS_LABEL[visit.status]}
                            </span>
                          </div>
                          <p className="mt-0.5 text-sm text-slate-600">
                            {visit.property_title || (
                              <span className="italic text-slate-400">
                                La propiedad ya no está en el catálogo
                              </span>
                            )}
                          </p>
                        </div>
                      </div>

                      {visit.briefing ? (
                        <details className="mt-3 rounded-md border border-slate-200 bg-slate-50">
                          <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-700">
                            Antes de la visita
                          </summary>
                          <p className="whitespace-pre-wrap px-3 pb-3 text-sm text-slate-700">
                            {visit.briefing}
                          </p>
                        </details>
                      ) : (
                        <p className="mt-3 text-xs text-slate-400">
                          El resumen previo se prepara poco antes de la visita.
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

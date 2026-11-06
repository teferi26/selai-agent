import { getAgencyId, serverApi } from "@/lib/server-api";

type Stage = {
  status: string;
  count: number;
  conversion_from_previous: number | null;
};

const LABELS: Record<string, string> = {
  new: "Nuevos",
  engaged: "Engaged",
  qualified: "Cualificados",
  visit_scheduled: "Visita agendada",
  visited: "Visitados",
  negotiating: "Negociando",
  won: "Cerrados",
};

export default async function FunnelPage() {
  const agencyId = await getAgencyId();
  const stages = await serverApi<Stage[]>(
    `/api/v1/agencies/${agencyId}/funnel?range_days=30`
  );
  const max = Math.max(...stages.map((s) => s.count), 1);

  return (
    <div>
      <header className="mb-6">
        <h2 className="text-2xl font-semibold">Funnel de conversión (30 días)</h2>
      </header>

      <div className="space-y-2 max-w-2xl">
        {stages.map((s) => {
          const pct = (s.count / max) * 100;
          return (
            <div key={s.status} className="flex items-center gap-3">
              <div className="w-40 text-sm text-slate-600">
                {LABELS[s.status] || s.status}
              </div>
              <div className="flex-1 bg-slate-100 rounded h-8 relative overflow-hidden">
                <div
                  className="absolute inset-y-0 left-0 bg-blue-500"
                  style={{ width: `${pct}%` }}
                />
                <div className="absolute inset-0 flex items-center px-3 text-sm font-medium text-white mix-blend-difference">
                  {s.count}
                </div>
              </div>
              <div className="w-20 text-right text-xs text-slate-500">
                {s.conversion_from_previous != null
                  ? `${s.conversion_from_previous}%`
                  : "—"}
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-slate-500 mt-4 max-w-2xl">
        El porcentaje de la derecha es la tasa de paso respecto al paso anterior del funnel.
      </p>
    </div>
  );
}

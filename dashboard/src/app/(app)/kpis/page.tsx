import { type Kpis } from "@/lib/api";
import { getAgencyId, serverApi } from "@/lib/server-api";

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-3xl font-semibold mt-1">{value}</div>
      {hint && <div className="text-xs text-slate-500 mt-1">{hint}</div>}
    </div>
  );
}

export default async function KpisPage() {
  const agencyId = await getAgencyId();
  const k = await serverApi<Kpis>(`/api/v1/agencies/${agencyId}/kpis?range_days=30`);

  const conversion =
    k.leads_new > 0 ? Math.round((k.leads_qualified / k.leads_new) * 100) : 0;
  const respSec = k.avg_first_response_seconds;
  const respHuman =
    respSec == null
      ? "—"
      : respSec < 60
      ? `${Math.round(respSec)} s`
      : `${(respSec / 60).toFixed(1)} min`;

  return (
    <div>
      <header className="mb-6">
        <h2 className="text-2xl font-semibold">Métricas (últimos 30 días)</h2>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="Leads nuevos" value={k.leads_new} hint={`Total histórico: ${k.leads_total}`} />
        <Stat label="Cualificados" value={k.leads_qualified} hint={`Conversión: ${conversion}%`} />
        <Stat label="Visitas agendadas" value={k.visits_scheduled} />
        <Stat label="Visitas completadas" value={k.visits_completed} />
        <Stat label="Handoffs a humano" value={k.handoffs} />
        <Stat label="Tiempo medio respuesta" value={respHuman} />
        <Stat
          label="Tokens IA (input)"
          value={k.agent_input_tokens.toLocaleString("es-ES")}
        />
        <Stat
          label="Tokens IA (output)"
          value={k.agent_output_tokens.toLocaleString("es-ES")}
        />
      </div>
    </div>
  );
}

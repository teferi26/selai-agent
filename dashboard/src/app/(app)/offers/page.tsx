import { getAgencyId, serverApi } from "@/lib/server-api";

type Offer = {
  id: string;
  lead_id: string;
  property_id: string;
  amount_eur: number;
  operation: string;
  status: string;
  pdf_url: string | null;
  sign_url: string | null;
  created_at: string;
  sent_at: string | null;
  signed_at: string | null;
};

const STATUS_BADGE: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700",
  sent: "bg-blue-100 text-blue-700",
  opened: "bg-indigo-100 text-indigo-700",
  signed: "bg-emerald-100 text-emerald-700",
  rejected: "bg-rose-100 text-rose-700",
  expired: "bg-amber-100 text-amber-700",
};

export default async function OffersPage() {
  const agencyId = await getAgencyId();
  const offers = await serverApi<Offer[]>(`/api/v1/agencies/${agencyId}/offers`);

  return (
    <div>
      <header className="mb-6">
        <h2 className="text-2xl font-semibold">Ofertas</h2>
      </header>

      <div className="rounded-lg border border-slate-200 bg-white overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2 text-left">Fecha</th>
              <th className="px-4 py-2 text-right">Importe</th>
              <th className="px-4 py-2 text-left">Operación</th>
              <th className="px-4 py-2 text-left">Estado</th>
              <th className="px-4 py-2 text-left">PDF</th>
              <th className="px-4 py-2 text-left">Enviada</th>
              <th className="px-4 py-2 text-left">Firmada</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {offers.map((o) => (
              <tr key={o.id}>
                <td className="px-4 py-2">
                  {new Date(o.created_at).toLocaleDateString("es-ES")}
                </td>
                <td className="px-4 py-2 text-right font-medium">
                  €{o.amount_eur.toLocaleString()}
                </td>
                <td className="px-4 py-2">{o.operation}</td>
                <td className="px-4 py-2">
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${STATUS_BADGE[o.status]}`}
                  >
                    {o.status}
                  </span>
                </td>
                <td className="px-4 py-2">
                  {o.pdf_url ? (
                    <a
                      className="text-blue-600 hover:underline"
                      href={o.pdf_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Abrir
                    </a>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-4 py-2 text-slate-500">
                  {o.sent_at ? new Date(o.sent_at).toLocaleString("es-ES") : "—"}
                </td>
                <td className="px-4 py-2 text-slate-500">
                  {o.signed_at ? new Date(o.signed_at).toLocaleString("es-ES") : "—"}
                </td>
              </tr>
            ))}
            {offers.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-slate-500">
                  Aún no se han generado ofertas.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import { getAgencyId, serverApi } from "@/lib/server-api";
import ExportLeadsButton from "./ExportLeadsButton";
import LeadsTable, { type LeadsPage } from "./LeadsTable";

export default async function LeadsPage() {
  const agencyId = await getAgencyId();
  // Forma paginada: los mismos leads de siempre, más el cursor para seguir
  // leyendo y el total real (que es lo que el botón de exportar promete).
  const page = await serverApi<LeadsPage>(
    `/api/v1/agencies/${agencyId}/leads?paginated=true`
  );

  return (
    <div>
      <header className="mb-6 flex items-start justify-between gap-4">
        <h2 className="text-2xl font-semibold">Leads</h2>
        <ExportLeadsButton agencyId={agencyId} total={page.total} />
      </header>

      <LeadsTable
        agencyId={agencyId}
        initialItems={page.items}
        initialCursor={page.next_cursor}
        total={page.total}
      />
    </div>
  );
}

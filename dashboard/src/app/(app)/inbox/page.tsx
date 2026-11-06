import { type InboxItem } from "@/lib/api";
import { getAgencyId, serverApi } from "@/lib/server-api";
import InboxFilters from "./InboxFilters";
import InboxList from "./InboxList";

/** Respuesta de /inbox cuando se pide paginada (`paginated=true`). */
type InboxPage = {
  items: InboxItem[];
  next_cursor: string | null;
};

export default async function InboxPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; channel?: string; conv_status?: string }>;
}) {
  const sp = await searchParams;
  const q = sp.q ?? "";
  const channel = sp.channel ?? "";
  const convStatus = sp.conv_status ?? "";

  const agencyId = await getAgencyId();
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (channel) params.set("channel", channel);
  if (convStatus) params.set("conv_status", convStatus);
  const filterQuery = params.toString();

  // Pedimos la forma paginada: los mismos elementos de siempre más el cursor
  // con el que "Cargar más" sigue leyendo.
  const first = await serverApi<InboxPage>(
    `/api/v1/agencies/${agencyId}/inbox?paginated=true${filterQuery ? `&${filterQuery}` : ""}`
  );

  return (
    <div>
      <header className="mb-4">
        <h2 className="text-2xl font-semibold">Bandeja unificada</h2>
      </header>

      <InboxFilters initialQ={q} initialChannel={channel} initialStatus={convStatus} />

      <InboxList
        agencyId={agencyId}
        initialItems={first.items}
        initialCursor={first.next_cursor}
        query={filterQuery}
        hasFilters={!!filterQuery}
      />
    </div>
  );
}

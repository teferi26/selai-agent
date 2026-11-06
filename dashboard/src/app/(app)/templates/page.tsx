// /templates — el server component carga las plantillas; el listado, el
// formulario de creación y los ejemplos viven en TemplatesClient.
import { getAgencyId, serverApi } from "@/lib/server-api";
import TemplatesClient, { type Template } from "./TemplatesClient";

export default async function TemplatesPage() {
  const agencyId = await getAgencyId();
  const rows = await serverApi<Template[]>(`/api/v1/agencies/${agencyId}/templates`);

  return <TemplatesClient agencyId={agencyId} rows={rows} />;
}

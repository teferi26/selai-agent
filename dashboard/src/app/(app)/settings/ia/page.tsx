// "Mi agente IA" — comportamiento del asistente: tono, frases prohibidas,
// modo de atención y aviso de grabación. El server component carga los
// settings; toda la interacción vive en IAClient.
import { getAgencyId, getMe, serverApi } from "@/lib/server-api";
import type { AgencySettings } from "@/lib/agency-settings";
import IAClient from "./IAClient";

export const metadata = { title: "Comportamiento IA" };

export default async function IAPage() {
  const [me, agencyId] = await Promise.all([getMe(), getAgencyId()]);
  const settings = await serverApi<AgencySettings>(
    `/api/v1/agencies/${agencyId}/settings`
  );

  return (
    <IAClient agencyId={agencyId} agencyName={me.agency_name} settings={settings} />
  );
}

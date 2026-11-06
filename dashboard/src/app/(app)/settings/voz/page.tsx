// "Voz del agente" — server component loads the current agency settings and
// the voice catalog in parallel; all interaction lives in VoiceClient.
import { getAgencyId, serverApi } from "@/lib/server-api";
import type { AgencySettings, VoiceCatalogItem } from "@/lib/agency-settings";
import VoiceClient from "./VoiceClient";

export const metadata = { title: "Voz del agente" };

export default async function VoicePage() {
  const agencyId = await getAgencyId();
  const [settings, catalog] = await Promise.all([
    serverApi<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`),
    serverApi<VoiceCatalogItem[]>("/api/v1/voice/catalog"),
  ]);

  return <VoiceClient agencyId={agencyId} settings={settings} catalog={catalog} />;
}

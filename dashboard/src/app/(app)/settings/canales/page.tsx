// "Canales" — post-wizard management of every connection channel: WhatsApp
// (connect/disconnect), the AI phone number, the leads email address and the
// web chat snippet. Live status comes from GET /channels (client-side, so it
// can refresh after connect/disconnect without a full reload).
import { getMe } from "@/lib/server-api";
import CanalesClient from "./CanalesClient";

export const metadata = { title: "Canales" };

export default async function CanalesPage() {
  const me = await getMe();
  return <CanalesClient agencyId={me.agency_id} slug={me.agency_slug} />;
}

// "Prueba tu IA" — text chat against the agency's own agent, using the same
// public web-chat endpoint an embedded website widget would use
// (POST /webhooks/web/{slug}/message, see backend/app/api/web_chat.py).
import { getMe } from "@/lib/server-api";
import ProbarClient from "./ProbarClient";

export const metadata = { title: "Prueba tu IA" };

export default async function ProbarPage() {
  const me = await getMe();
  return <ProbarClient slug={me.agency_slug} agencyName={me.agency_name} />;
}

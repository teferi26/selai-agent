// "Calendario" — conexión de la agencia con Google Calendar. El estado real
// lo pide el cliente (GET /calendar) para poder refrescarse solo mientras la
// ventana de Google está abierta, así que aquí solo resolvemos la agencia.
import { getMe } from "@/lib/server-api";
import CalendarioClient from "./CalendarioClient";

export const metadata = { title: "Calendario" };

export default async function CalendarioPage() {
  const me = await getMe();
  return <CalendarioClient agencyId={me.agency_id} />;
}

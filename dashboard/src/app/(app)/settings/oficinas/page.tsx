// "Oficinas" — alta, renombrado y cierre de sucursales, y a qué oficina
// pertenece cada persona del equipo (plan Agencia).
//
// El listado se carga en el servidor. El GET de oficinas no está limitado por
// plan a propósito: una agencia de Starter o Pro tiene que ver la pantalla y
// entender qué se está vendiendo, no un error.
import { getMe, serverApi } from "@/lib/server-api";
import OficinasClient, {
  type Office,
  type OfficeMember,
  type OfficeProperty,
} from "./OficinasClient";

export const metadata = { title: "Oficinas" };

export default async function OficinasPage() {
  const me = await getMe();
  // El catálogo se carga aquí porque es la mitad de la función que funciona
  // hoy: mientras no haya invitaciones, toda agencia tiene una sola persona, y
  // lo que decide de qué oficina es un contacto es el inmueble que ha ido a
  // ver. Sin esta lista la pantalla dejaría el filtro sin nada que filtrar.
  const [offices, members, properties] = await Promise.all([
    serverApi<Office[]>(`/api/v1/agencies/${me.agency_id}/offices`),
    serverApi<OfficeMember[]>(`/api/v1/agencies/${me.agency_id}/offices/members`),
    serverApi<OfficeProperty[]>(`/api/v1/agencies/${me.agency_id}/offices/properties`),
  ]);

  return (
    <OficinasClient
      agencyId={me.agency_id}
      offices={offices}
      members={members}
      properties={properties}
      planCode={me.plan_code}
      isOwner={me.role === "owner" || me.role === "admin"}
    />
  );
}

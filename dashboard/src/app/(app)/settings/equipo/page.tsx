// /settings/equipo — las personas de la agencia y las invitaciones abiertas.
//
// Es la pantalla que hace verdad el "hasta 2/5/15 personas de tu equipo" de la
// web: hasta ahora la única forma de tener un usuario era ser quien dio de
// alta la agencia. El contador de plazas suma miembros + invitaciones
// pendientes, que es como lo cuenta el backend al invitar.
import { getMe, serverApi } from "@/lib/server-api";
import EquipoClient, {
  type Invitation,
  type MembersResponse,
} from "./EquipoClient";

export const metadata = { title: "Equipo" };

export default async function EquipoPage() {
  const me = await getMe();
  const [team, invitations] = await Promise.all([
    serverApi<MembersResponse>(`/api/v1/agencies/${me.agency_id}/members`),
    serverApi<Invitation[]>(`/api/v1/agencies/${me.agency_id}/invitations`),
  ]);

  return (
    <EquipoClient
      agencyId={me.agency_id}
      currentUserId={me.user_id}
      // Sólo el responsable invita (y el admin de soporte). El panel esconde
      // lo que el backend rechazaría, en vez de enseñar un botón que da 403.
      canInvite={me.role === "owner" || me.role === "admin"}
      team={team}
      invitations={invitations}
    />
  );
}

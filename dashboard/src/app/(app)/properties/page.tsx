// Catalog page — server component loads the data with the authenticated
// server API; the interactive half lives in PropertiesClient.
import { clerkEnabled, getAgencyId, getMe, serverApi } from "@/lib/server-api";
import PropertiesClient, { type Property } from "./PropertiesClient";

export default async function PropertiesPage() {
  // The backend's local dev bypass intentionally has no User row (and thus
  // `/me` returns not-onboarded), but it also grants owner-level mutations.
  // Keep that explicit escape hatch while production always resolves the
  // real role from the authenticated session.
  const shouldLoadRole = clerkEnabled || Boolean(process.env.DEV_USER_EMAIL);
  const [agencyId, me] = await Promise.all([
    getAgencyId(),
    shouldLoadRole ? getMe() : Promise.resolve(null),
  ]);
  // limit alto a propósito: la vista previa del import compara referencias
  // contra este listado, y con el límite por defecto (100) marcaría como
  // nuevas filas que en realidad se van a actualizar.
  const items = await serverApi<Property[]>(
    `/api/v1/agencies/${agencyId}/properties?limit=500`
  );
  return (
    <PropertiesClient
      agencyId={agencyId}
      initialItems={items}
      canApplyDescriptions={
        me === null || me.role === "owner" || me.role === "admin"
      }
    />
  );
}

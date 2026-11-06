// "Puesta en marcha" — the full-screen activation wizard. The server
// component gathers settings + catalog size + voice catalog; every
// interactive step (real connections included) lives in SetupClient.
import { getMe, serverApi } from "@/lib/server-api";
import type { AgencySettings, VoiceCatalogItem } from "@/lib/agency-settings";
import { SETUP_STEP_KEYS, type SetupStepKey } from "@/lib/setup-steps";
import SetupClient from "./SetupClient";

export const metadata = { title: "Puesta en marcha" };

type Property = { id: string };

// El listado se pide con UNA fila de más a propósito. Con el tope por defecto
// (100, backend api/properties.py:51) no se puede distinguir "tiene justo 100"
// de "tiene 4.000": ambos devuelven 100 filas. Decir "más de 100" a quien
// tiene exactamente 100 es la misma mentira de (4) girada del revés — más
// pequeña, pero mentira. Con 101 la pregunta se contesta sola: si vuelven 101
// filas hay más de 100 seguro; si vuelven menos, el número es exacto.
const PROPERTIES_THRESHOLD = 100;
const PROPERTIES_PROBE_LIMIT = PROPERTIES_THRESHOLD + 1;

export default async function SetupPage({
  searchParams,
}: {
  searchParams: Promise<{ step?: string }>;
}) {
  const me = await getMe();
  // Deep link opcional (?step=crm) — p. ej. desde /settings/canales.
  const { step } = await searchParams;
  const initialStepKey: SetupStepKey | null =
    step && (SETUP_STEP_KEYS as readonly string[]).includes(step)
      ? (step as SetupStepKey)
      : null;
  const [settings, properties, catalog] = await Promise.all([
    serverApi<AgencySettings>(`/api/v1/agencies/${me.agency_id}/settings`),
    serverApi<Property[]>(
      `/api/v1/agencies/${me.agency_id}/properties?limit=${PROPERTIES_PROBE_LIMIT}`
    ),
    // El catálogo de voces es global; si fallara, el paso 2 lo explica solo.
    serverApi<VoiceCatalogItem[]>("/api/v1/voice/catalog").catch(
      () => [] as VoiceCatalogItem[]
    ),
  ]);

  // (4) El recuento real, no el de la primera página. GET /properties devuelve
  // como mucho las filas que se le piden, así que una agencia con 4.000 pisos
  // leía "Ya tienes 100 propiedades" — y encima justo en la pantalla que le
  // está diciendo si su catálogo está cargado.
  //
  // El número exacto ya viaja en /me: `usage` lo saca de un COUNT(*) sobre
  // Property (backend services/plan_limits.py `get_usage`), que es lo mismo
  // que devolvería un endpoint de conteo. No hace falta pedir nada nuevo.
  // Sólo falta cuando la agencia no tiene fila de plan (entornos de desarrollo
  // y datos sembrados): ahí se cae a la longitud de la lista, y si topa con el
  // límite el wizard lo dice como "más de 100" en vez de inventarse un número.
  const metered = me.usage.find((u) => u.resource === "properties")?.used ?? null;
  const listedExact = properties.length < PROPERTIES_PROBE_LIMIT;
  // Sin fila de plan y con el listado topado, el número que se enseña es el
  // umbral (100), no las 101 filas que hemos pedido: "más de 100" con un 101
  // al lado sería un número inventado.
  const propertiesCount =
    metered ?? (listedExact ? properties.length : PROPERTIES_THRESHOLD);
  const propertiesCountExact = metered !== null || listedExact;

  return (
    <SetupClient
      agencyId={me.agency_id}
      agencyName={me.agency_name}
      slug={me.agency_slug}
      settings={settings}
      propertiesCount={propertiesCount}
      propertiesCountExact={propertiesCountExact}
      catalog={catalog}
      initialStepKey={initialStepKey}
    />
  );
}

// Post-signup onboarding: create the agency, sign the legal docs, start the
// 14-day trial. The server half fetches the current legal document versions
// so the consent the user signs is pinned to what the server considers
// canonical (stale consents are rejected with 422).
import { redirect } from "next/navigation";
import { serverApi, type Me } from "@/lib/server-api";
import { parsePurchaseIntent, withPurchaseIntent } from "@/lib/purchase-intent";
import OnboardingForm from "./OnboardingForm";

export const metadata = { title: "Crea tu agencia" };

type LegalDoc = {
  slug: string;
  version: string;
  title: string;
  document_hash: string;
};

/** ¿Ya tiene agencia quien está pidiendo el alta?
 *
 * `true` sólo con un /me que responde 200. Un 404 es "todavía no hay agencia"
 * —el caso legítimo de esta pantalla— y cualquier otro fallo (backend caído,
 * token raro) tampoco puede dar por hecho que la tiene: mandar a /setup a
 * ciegas sería rebotar entre dos pantallas rotas. Ante la duda, el formulario;
 * el backend sigue siendo quien corta el alta duplicada.
 */
async function alreadyHasAgency(): Promise<boolean> {
  const me = await serverApi<Me>("/api/v1/me").catch(() => null);
  return me !== null;
}

export default async function OnboardingPage({
  searchParams,
}: {
  searchParams: Promise<{ plan?: string | string[]; interval?: string | string[] }>;
}) {
  const intent = parsePurchaseIntent(await searchParams);
  // (2) Volver a esta URL con la agencia ya creada enseñaba el formulario otra
  // vez, y al enviarlo un "email_already_registered" que no explica nada. Pasa
  // más de lo que parece: el alta tarda, el usuario recarga por impaciencia y
  // el POST anterior sí había terminado. Quien ya tiene agencia continúa en
  // la selección comercial que traía o, si no había ninguna, va a /setup.
  //
  // Ojo: `getMe()` no vale aquí — redirige a /onboarding cuando no hay agencia
  // y montaría un bucle contra esta misma página.
  if (await alreadyHasAgency()) {
    redirect(intent ? withPurchaseIntent("/billing", intent) : "/setup");
  }

  const docs = await serverApi<LegalDoc[]>("/api/v1/legal/documents");
  const versions = Object.fromEntries(docs.map((d) => [d.slug, d.version]));
  const hashes = Object.fromEntries(docs.map((d) => [d.slug, d.document_hash]));
  const titles = Object.fromEntries(docs.map((d) => [d.slug, d.title]));
  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-4 py-12">
      <OnboardingForm
        versions={versions}
        hashes={hashes}
        titles={titles}
        purchaseIntent={intent}
      />
    </main>
  );
}

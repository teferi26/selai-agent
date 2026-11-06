// Layout for the authenticated dashboard surface — keeps the existing sidebar
// shell. Public marketing pages live under (marketing)/ and don't render this.
import type { Metadata } from "next";
import { UserButton } from "@clerk/nextjs";
import {
  DashboardNavigation,
  type SetupProgress,
} from "@/components/dashboard/DashboardNavigation";
import { SupportButton } from "@/components/ui";
import { serverApi, type Me } from "@/lib/server-api";
import type { AgencySettings } from "@/lib/agency-settings";
import { computeSetupDone, countSetupDone, SETUP_TOTAL } from "@/lib/setup-steps";
import LegalConsentGate, {
  type LegalConsentGateData,
  type LegalConsentStatus,
  type LegalDocumentSummary,
} from "./LegalConsentGate";
// Vocabulario de facturación: vive junto a /billing, que es donde se arregla.
import { statusNotice } from "./billing/status";

const clerkEnabled = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export const metadata: Metadata = {
  title: "Dashboard · selai",
  description: "Bandeja unificada y métricas de tu inmobiliaria.",
  robots: { index: false, follow: false },
};

/** /me tolerante a fallos: el layout nunca debe romperse porque el backend
 * esté caído o el usuario aún no tenga agencia. */
async function getMeSafe(): Promise<Me | null> {
  try {
    return await serverApi<Me>("/api/v1/me");
  } catch {
    return null;
  }
}

/** Sidebar mini-progress: same rules as /setup. Never breaks the layout —
 * if anything fails (no agency yet, backend down) we simply show nothing. */
async function getSetupProgress(me: Me | null): Promise<SetupProgress | null> {
  if (me === null) return null;
  try {
    const [settings, properties] = await Promise.all([
      serverApi<AgencySettings>(`/api/v1/agencies/${me.agency_id}/settings`),
      serverApi<{ id: string }[]>(`/api/v1/agencies/${me.agency_id}/properties`),
    ]);
    const done = countSetupDone(
      computeSetupDone(
        settings.settings,
        settings.settings.setup_checklist ?? {},
        properties.length > 0
      )
    );
    return { done, total: SETUP_TOTAL };
  } catch {
    return null;
  }
}

async function getLegalConsentGate(me: Me | null): Promise<LegalConsentGateData | null> {
  if (me === null) return null;
  try {
    const [documents, consentStatus] = await Promise.all([
      serverApi<LegalDocumentSummary[]>("/api/v1/legal/documents"),
      serverApi<LegalConsentStatus>(
        `/api/v1/legal/agencies/${me.agency_id}/consent-status`
      ),
    ]);
    const completeCatalog = ["terms_of_service", "dpa", "privacy_policy"].every(
      (slug) =>
        documents.some(
          (document) => document.slug === slug && Boolean(document.document_hash)
        )
    );
    if (!completeCatalog) return { available: false };
    return { available: true, documents, status: consentStatus };
  } catch {
    // Once /me has identified an agency, a failed legal-status read must not
    // silently unlock the product. The gate offers a retry instead.
    return { available: false };
  }
}

function AccountAndSupport() {
  return (
    <>
      {clerkEnabled && (
        <div className="mt-6 border-t border-slate-200 pt-4">
          <UserButton showName />
        </div>
      )}
      <div className="mt-4 border-t border-slate-100 pt-3">
        <SupportButton variant="sidebar" />
        <a href="/ayuda" className="block rounded px-2 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
          Centro de ayuda
        </a>
        <a
          href="/estado"
          className="mt-2 block px-2 text-[11px] text-slate-400 hover:text-slate-600 hover:underline"
        >
          Estado del sistema
        </a>
      </div>
    </>
  );
}

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await getMeSafe();
  const [progress, legalGate] = await Promise.all([
    getSetupProgress(me),
    getLegalConsentGate(me),
  ]);
  // Aviso honesto cuando la suscripción está caída (o a punto): la IA deja de
  // atender y el usuario tiene que enterarse esté donde esté, no solo si entra
  // en Facturación. Con todo en orden `statusNotice` devuelve null y aquí no
  // se pinta nada.
  const notice = statusNotice(me?.subscription_status);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 md:flex">
      <a
        href="#main-content"
        className="sr-only z-50 rounded bg-white px-4 py-2 text-sm font-semibold text-selai-700 shadow focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        Saltar al contenido
      </a>
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 overflow-y-auto border-r border-slate-200 bg-white px-4 py-6 md:block">
        <h1 className="text-lg font-semibold mb-6">selai</h1>
        <DashboardNavigation progress={progress} />
        <AccountAndSupport />
      </aside>

      <header className="sticky top-0 z-40 flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 md:hidden">
        <a href="/inbox" className="text-lg font-semibold" aria-label="selai, ir a la bandeja">
          selai
        </a>
        <details className="relative">
          <summary className="cursor-pointer list-none rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 [&::-webkit-details-marker]:hidden">
            Menú
          </summary>
          <div className="absolute right-0 mt-2 max-h-[calc(100vh-4.5rem)] w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-lg border border-slate-200 bg-white p-3 shadow-xl">
            <DashboardNavigation progress={progress} />
            <AccountAndSupport />
          </div>
        </details>
      </header>

      <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 p-4 sm:p-6 md:p-8">
        {notice && (
          <div
            role="status"
            className={`mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3 ${
              notice.tone === "danger"
                ? "border-red-200 bg-red-50"
                : "border-amber-200 bg-amber-50"
            }`}
          >
            <div>
              <p
                className={`text-sm font-semibold ${
                  notice.tone === "danger" ? "text-red-800" : "text-amber-900"
                }`}
              >
                {notice.title}
              </p>
              <p className="mt-0.5 text-sm text-slate-700">{notice.body}</p>
            </div>
            {/* Al ancla de los planes: este aviso también se pinta EN
                /billing (es el único sitio donde se arregla), y allí un enlace
                a la propia página no llevaría a ninguna parte. */}
            <a
              href="/billing#planes"
              className="rounded-md bg-selai-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-selai-700"
            >
              Ver planes y pagos
            </a>
          </div>
        )}
        {me && legalGate ? (
          <LegalConsentGate agencyId={me.agency_id} role={me.role} gate={legalGate}>
            {children}
          </LegalConsentGate>
        ) : (
          children
        )}
      </main>
    </div>
  );
}

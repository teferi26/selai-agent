"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export type LegalDocumentSummary = {
  slug: string;
  version: string;
  title: string;
  document_hash: string;
};

export type LegalConsentStatus = {
  agency_id: string;
  owner_user_id: string | null;
  consents: Record<string, { version: string; granted_at: string }>;
  missing: string[];
  stale: Array<{ slug: string; reason?: string }>;
};

export type LegalConsentGateData =
  | {
      available: true;
      documents: LegalDocumentSummary[];
      status: LegalConsentStatus;
    }
  | { available: false };

type Props = {
  agencyId: string;
  role: string;
  gate: LegalConsentGateData;
  children: React.ReactNode;
};

const REQUIRED_SLUGS = ["terms_of_service", "dpa", "privacy_policy"] as const;

export default function LegalConsentGate({ agencyId, role, gate, children }: Props) {
  const router = useRouter();
  const [accepted, setAccepted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsConsent =
    gate.available && (gate.status.missing.length > 0 || gate.status.stale.length > 0);

  if (gate.available && !needsConsent) return children;

  if (!gate.available) {
    return (
      <section className="mx-auto max-w-2xl py-12" aria-labelledby="legal-check-title">
        <p className="text-sm font-semibold text-amber-700">Verificación pendiente</p>
        <h2 id="legal-check-title" className="mt-2 text-2xl font-semibold text-slate-950">
          No podemos comprobar la aceptación legal ahora mismo
        </h2>
        <p className="mt-3 max-w-xl text-sm leading-6 text-slate-600">
          El panel permanecerá protegido hasta que podamos confirmar el estado de los documentos.
        </p>
        <button
          type="button"
          onClick={() => router.refresh()}
          className="mt-6 rounded-md bg-selai-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-selai-700"
        >
          Volver a comprobar
        </button>
      </section>
    );
  }

  const isOwner = role === "owner";
  const documents = gate.documents;
  const docsBySlug = Object.fromEntries(documents.map((doc) => [doc.slug, doc]));

  async function acceptCurrentDocuments() {
    if (!accepted || !isOwner) return;
    setSubmitting(true);
    setError(null);
    try {
      await api(`/api/v1/legal/agencies/${agencyId}/legacy-consent`, {
        method: "POST",
        body: JSON.stringify({
          accepted_terms: true,
          accepted_dpa: true,
          accepted_privacy: true,
          marketing_opt_in: false,
          accepted_versions: Object.fromEntries(
            documents.map((doc) => [doc.slug, doc.version])
          ),
          accepted_hashes: Object.fromEntries(
            documents.map((doc) => [doc.slug, doc.document_hash])
          ),
        }),
      });
      router.refresh();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(
        message.includes("stale_consent")
          ? "Los documentos han cambiado. Vuelve a cargar y revisa la versión actual."
          : "No se pudo registrar la aceptación. Vuelve a intentarlo."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="mx-auto max-w-2xl py-10" aria-labelledby="legal-consent-title">
      <p className="text-sm font-semibold text-selai-700">Acción necesaria</p>
      <h2 id="legal-consent-title" className="mt-2 text-2xl font-semibold text-slate-950">
        Revisa la documentación legal actual
      </h2>
      <p className="mt-3 max-w-xl text-sm leading-6 text-slate-600">
        {isOwner
          ? "Antes de continuar, la persona propietaria debe aceptar las versiones vigentes."
          : "La persona propietaria de la agencia debe aceptar las versiones vigentes antes de que el equipo continúe."}
      </p>

      <div className="mt-7 border-y border-slate-200 py-2">
        {REQUIRED_SLUGS.map((slug) => {
          const doc = docsBySlug[slug];
          if (!doc) return null;
          return (
            <a
              key={slug}
              href={`/api/backend/api/v1/legal/documents/${slug}`}
              target="_blank"
              rel="noreferrer"
              className="flex min-h-12 items-center justify-between gap-4 border-b border-slate-100 px-1 py-3 text-sm font-medium text-slate-800 last:border-b-0 hover:text-selai-700"
            >
              <span>{doc.title}</span>
              <span className="shrink-0 text-xs font-normal text-slate-500">{doc.version}</span>
            </a>
          );
        })}
      </div>

      {isOwner ? (
        <div className="mt-6">
          <label className="flex items-start gap-3 text-sm leading-6 text-slate-700">
            <input
              type="checkbox"
              checked={accepted}
              onChange={(event) => setAccepted(event.target.checked)}
              className="mt-1 h-4 w-4 shrink-0"
            />
            <span>
              He leído y acepto los Términos de Servicio, el Acuerdo de Encargo de
              Tratamiento y la Política de Privacidad indicados arriba.
            </span>
          </label>
          {error && (
            <p role="alert" className="mt-4 text-sm text-red-700">
              {error}
            </p>
          )}
          <button
            type="button"
            disabled={!accepted || submitting}
            onClick={acceptCurrentDocuments}
            className="mt-5 rounded-md bg-selai-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-selai-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "Registrando aceptación…" : "Aceptar y continuar"}
          </button>
        </div>
      ) : (
        <p className="mt-6 border-l-2 border-amber-400 pl-4 text-sm leading-6 text-slate-700">
          Pide a la persona propietaria que entre en selai. El acceso del equipo se reanudará en
          cuanto complete esta aceptación.
        </p>
      )}
    </section>
  );
}

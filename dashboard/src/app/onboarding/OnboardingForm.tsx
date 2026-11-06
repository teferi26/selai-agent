"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useUser } from "@clerk/nextjs";
import { api } from "@/lib/api";
import {
  purchasePlanLabel,
  withPurchaseIntent,
  type PurchaseIntent,
} from "@/lib/purchase-intent";
import { readPendingInvite } from "../invitacion/pending";

const clerkEnabled = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

type Props = {
  versions: Record<string, string>;
  hashes: Record<string, string>;
  titles: Record<string, string>;
  purchaseIntent: PurchaseIntent | null;
};

type ClerkIdentity = {
  clerkFullName: string | null;
  clerkEmail: string | null;
};

function ClerkOnboardingForm(props: Props) {
  const { user } = useUser();
  return (
    <OnboardingFormContent
      {...props}
      clerkFullName={user?.fullName ?? null}
      clerkEmail={
        user?.primaryEmailAddress?.emailAddress ??
        user?.emailAddresses?.[0]?.emailAddress ??
        null
      }
    />
  );
}

export default function OnboardingForm(props: Props) {
  return clerkEnabled ? (
    <ClerkOnboardingForm {...props} />
  ) : (
    <OnboardingFormContent {...props} clerkFullName={null} clerkEmail={null} />
  );
}

function OnboardingFormContent({
  versions,
  hashes,
  titles,
  purchaseIntent,
  clerkFullName,
  clerkEmail,
}: Props & ClerkIdentity) {
  const router = useRouter();

  const [agencyName, setAgencyName] = useState("");
  // `null` means the owner has not edited the field yet, so a Clerk profile
  // that arrives after hydration can be used directly without synchronizing
  // props into state from an effect. An empty string is a real user edit.
  const [fullNameDraft, setFullNameDraft] = useState<string | null>(null);
  const fullName = fullNameDraft ?? clerkFullName ?? "";
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");

  // Clerk can load after mount; session identity always wins and stays
  // read-only, while local development without Clerk keeps an editable field.
  const effectiveEmail = clerkEmail ?? email;

  // Un invitado NO tiene nada que hacer en esta pantalla: aquí se crea una
  // agencia, y él ya tiene sitio en una. Clerk puede dejarle aquí después de
  // registrarse (su `fallbackRedirectUrl` es /onboarding), así que si trae una
  // invitación a medias se le devuelve a ella antes de que rellene nada.
  useEffect(() => {
    const pending = readPendingInvite();
    if (pending) router.replace(`/invitacion/${encodeURIComponent(pending)}`);
  }, [router]);
  const [acceptedLegal, setAcceptedLegal] = useState(false);
  const [marketing, setMarketing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api("/api/v1/onboarding", {
        method: "POST",
        body: JSON.stringify({
          agency_name: agencyName,
          owner_email: effectiveEmail,
          owner_full_name: fullName,
          owner_phone: phone || null,
          // La identidad de Clerk ya NO viaja en el cuerpo: el backend la
          // saca del token de sesión verificado. Mandarla aquí era el agujero.
          accepted_terms: acceptedLegal,
          accepted_dpa: acceptedLegal,
          accepted_privacy: acceptedLegal,
          marketing_opt_in: marketing,
          accepted_versions: versions,
          accepted_hashes: hashes,
        }),
      });
      router.push(purchaseIntent ? withPurchaseIntent("/billing", purchaseIntent) : "/setup");
      router.refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("email_already_registered")) {
        setError("Ese email ya tiene una agencia. Inicia sesión con esa cuenta.");
      } else if (msg.includes("pending_invitation")) {
        // El backend corta el alta de quien tiene una invitación viva: crear
        // aquí una agencia le dejaría fuera del equipo al que le invitaron.
        setError(
          "Tienes una invitación pendiente para unirte a un equipo. Abre el enlace que te enviamos por email en lugar de crear una agencia nueva."
        );
      } else if (msg.includes("stale_consent")) {
        setError("Los documentos legales han cambiado. Recarga la página y vuelve a aceptar.");
      } else if (msg.includes("email_mismatch")) {
        setError(
          "El email de la agencia debe ser el mismo con el que has iniciado sesión."
        );
      } else if (msg.includes("email_claim_missing") || msg.includes("401")) {
        setError(
          "Tu sesión ha caducado o no está completa. Cierra sesión y vuelve a entrar."
        );
      } else {
        setError("No se pudo crear la agencia. Inténtalo de nuevo en un momento.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const legalSlugs = ["terms_of_service", "dpa", "privacy_policy"];

  return (
    <form
      onSubmit={submit}
      className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 shadow-sm"
    >
      <h1 className="text-2xl font-semibold mb-1">Crea tu agencia</h1>
      <p className="text-sm text-slate-500 mb-6">
        14 días de prueba. Sin tarjeta. Tu asistente de voz se aprovisiona solo.
      </p>

      {purchaseIntent && (
        <p className="mb-5 rounded-md border border-selai-100 bg-selai-50 px-3 py-2 text-sm text-selai-800">
          Al crear la agencia revisarás el plan {purchasePlanLabel(purchaseIntent.plan)} con pago{" "}
          {purchaseIntent.interval === "year" ? "anual" : "mensual"}. No se te cobrará nada
          sin que elijas cuándo activarlo.
        </p>
      )}

      {error && (
        <div
          id="onboarding-error"
          role="alert"
          className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
        >
          {error}
        </div>
      )}

      <label htmlFor="agency-name" className="block text-sm font-medium mb-1">
        Nombre de la inmobiliaria
      </label>
      <input
        id="agency-name"
        name="agencyName"
        required
        minLength={2}
        autoComplete="organization"
        value={agencyName}
        onChange={(e) => setAgencyName(e.target.value)}
        placeholder="Inmobiliaria Sol, S.L."
        className="mb-4 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
      />

      <label htmlFor="full-name" className="block text-sm font-medium mb-1">
        Tu nombre
      </label>
      <input
        id="full-name"
        name="fullName"
        required
        minLength={2}
        autoComplete="name"
        value={fullName}
        onChange={(e) => setFullNameDraft(e.target.value)}
        className="mb-4 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
      />

      <label htmlFor="owner-email" className="block text-sm font-medium mb-1">
        Email
      </label>
      <input
        id="owner-email"
        name="email"
        required
        type="email"
        autoComplete="email"
        inputMode="email"
        value={effectiveEmail}
        readOnly={!!clerkEmail}
        onChange={(e) => setEmail(e.target.value)}
        className="mb-4 w-full rounded-md border border-slate-300 px-3 py-2 text-sm read-only:bg-slate-50 read-only:text-slate-500"
      />

      <label htmlFor="owner-phone" className="block text-sm font-medium mb-1">
        Teléfono (opcional)
      </label>
      <input
        id="owner-phone"
        name="phone"
        type="tel"
        autoComplete="tel"
        inputMode="tel"
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
        placeholder="+34 600 000 000"
        className="mb-6 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
      />

      <label className="mb-2 flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          required
          checked={acceptedLegal}
          onChange={(e) => setAcceptedLegal(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          He leído y acepto{" "}
          {legalSlugs.map((slug, i) => (
            <span key={slug}>
              {i > 0 && (i === legalSlugs.length - 1 ? " y " : ", ")}
              <a
                href={`/api/backend/api/v1/legal/documents/${slug}`}
                target="_blank"
                className="underline"
              >
                {titles[slug] ?? slug}
              </a>
            </span>
          ))}
          .
        </span>
      </label>

      <label className="mb-6 flex items-start gap-2 text-sm text-slate-600">
        <input
          type="checkbox"
          checked={marketing}
          onChange={(e) => setMarketing(e.target.checked)}
          className="mt-0.5"
        />
        <span>Quiero recibir novedades del producto (opcional).</span>
      </label>

      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md bg-selai-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
      >
        {submitting ? "Creando tu agencia…" : "Empezar la prueba de 14 días"}
      </button>
    </form>
  );
}

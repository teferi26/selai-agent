// Botón del portal de cliente de Stripe (tarjeta, facturas, cancelar).
//
// La compra y el cambio de plan viven en PlanCards.tsx: dependen del periodo
// mensual/anual y del estado de la suscripción, así que este componente se
// quedó solo con el portal.
"use client";

import { useState } from "react";
import { ErrorState, SupportButton } from "@/components/ui";
import { api } from "@/lib/api";

type Props = { kind: "portal"; agencyId: string; label: string };

export default function BillingActions(props: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      const { portal_url } = await api<{ portal_url: string }>("/api/v1/billing/portal", {
        method: "POST",
        body: JSON.stringify({
          agency_id: props.agencyId,
          return_url: `${window.location.origin}/billing`,
        }),
      });
      if (portal_url) window.location.assign(portal_url);
      else setError("Aún no hay cuenta de facturación. Contrata un plan primero.");
    } catch {
      // Antes decía "contacta con soporte" sin dar ningún medio de hacerlo:
      // ahora el aviso lleva el formulario debajo.
      setError("No se pudo abrir el portal. ¿Ya has contratado un plan?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <button
        onClick={go}
        disabled={busy}
        className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-60"
      >
        {busy ? "Abriendo…" : props.label}
      </button>
      {error && (
        <ErrorState
          className="mt-3"
          title={error}
          message={null}
          onRetry={() => void go()}
          retrying={busy}
        >
          <SupportButton variant="link" label="Reportar un problema" />
        </ErrorState>
      )}
    </div>
  );
}

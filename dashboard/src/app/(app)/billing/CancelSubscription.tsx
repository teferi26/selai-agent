// (73) "Quiero cancelar": antes de abrir el portal de Stripe, una encuesta de
// 1 pregunta con el motivo + recordatorio de que se puede pausar. El motivo se
// guarda con PATCH settings {cancel_feedback} — si el backend lo rechaza como
// clave extra, seguimos al portal igualmente.
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { AgencySettings } from "@/lib/agency-settings";

const REASONS: { value: string; label: string }[] = [
  { value: "precio", label: "El precio no me encaja" },
  { value: "no_lo_uso", label: "No lo estoy usando" },
  { value: "temporada", label: "Me voy de vacaciones / es temporada baja" },
  { value: "otro", label: "Otro motivo" },
];

export default function CancelSubscription({ agencyId }: { agencyId: string }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function continueToPortal() {
    setBusy(true);
    setError(null);
    // Guardamos el motivo, pero nunca bloqueamos el camino al portal.
    if (reason) {
      try {
        await api<AgencySettings>(`/api/v1/agencies/${agencyId}/settings`, {
          method: "PATCH",
          body: JSON.stringify({
            cancel_feedback: { reason, at: new Date().toISOString() },
          }),
        });
      } catch {
        // El backend puede ignorar o rechazar la clave extra: seguimos igual.
      }
    }
    try {
      const { portal_url } = await api<{ portal_url: string }>(
        "/api/v1/billing/portal",
        {
          method: "POST",
          body: JSON.stringify({
            agency_id: agencyId,
            return_url: `${window.location.origin}/billing`,
          }),
        }
      );
      if (portal_url) window.location.assign(portal_url);
      else {
        setError("Aún no hay cuenta de facturación. Contrata un plan primero.");
        setBusy(false);
      }
    } catch {
      setError("No se pudo abrir el portal. ¿Ya has contratado un plan?");
      setBusy(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="text-sm text-slate-500 underline hover:text-slate-700"
      >
        Quiero cancelar
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Cancelar suscripción"
        >
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-semibold">Antes de irte…</h3>
            <p className="mt-1 text-sm text-slate-500">
              Nos ayuda mucho saber el motivo. Es solo una pregunta.
            </p>

            <div className="mt-4 space-y-2">
              {REASONS.map((r) => (
                <label key={r.value} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="cancel_reason"
                    checked={reason === r.value}
                    onChange={() => setReason(r.value)}
                  />
                  {r.label}
                </label>
              ))}
            </div>

            <div className="mt-4 rounded-md border border-selai-100 bg-selai-50 px-3 py-2.5 text-sm text-slate-700">
              ¿Sabías que puedes <strong>pausar</strong> en lugar de cancelar?
              Escríbenos a{" "}
              <a href="mailto:hola@selai.es" className="text-selai-600 underline">
                hola@selai.es
              </a>{" "}
              y lo dejamos listo.
            </div>

            {error && (
              <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </div>
            )}

            <div className="mt-5 flex items-center justify-end gap-3">
              <button
                onClick={() => setOpen(false)}
                disabled={busy}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-60"
              >
                Volver
              </button>
              <button
                onClick={continueToPortal}
                disabled={busy}
                className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
              >
                {busy ? "Abriendo…" : "Continuar al portal"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

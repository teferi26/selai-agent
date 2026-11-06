// (195) "Comprobar mi web" — sustituye el botón de honor del paso 7.
//
// Antes el usuario decía «ya lo he puesto» y le creíamos. Un chat que nunca
// llegó a aparecer en la web del cliente es una baja segura que nadie ve
// venir. Ahora descargamos la web que ha declarado y buscamos el snippet
// con SU slug — y cuando falla decimos cuál de los dos fallos es, porque
// «no lo encontramos» y «el que tienes es de otra agencia» se arreglan de
// formas muy distintas.
//
// Se usa en el paso 7 del asistente y en Ajustes → Canales.
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { WebchatVerifyOut, WebchatVerifyReason } from "@/lib/channels";

const EXPLANATION: Record<WebchatVerifyReason, string> = {
  ok: "",
  widget_not_found:
    "Hemos abierto tu web pero no hemos encontrado el código. Comprueba que lo pegaste antes de </body> y que has guardado y publicado los cambios; si tu web tiene caché, vacíala y vuelve a comprobar.",
  other_slug:
    "Hay un chat de selai en tu web, pero es el de otra agencia. Si copiaste el código de otro sitio, sustitúyelo por el tuyo: si no, los leads de tu web acabarán en otra bandeja.",
  unreachable:
    "No hemos podido abrir esa dirección. Repásala (¿es la web pública?) y comprueba que se ve desde fuera de tu oficina.",
  blocked_url:
    "Esa dirección no es una web pública que podamos abrir. Escribe la dirección completa de tu web, por ejemplo https://www.tuinmobiliaria.es.",
};

type State =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "done"; result: WebchatVerifyOut }
  | { kind: "error" };

export default function WebchatVerify({
  agencyId,
  defaultUrl,
  verifiedUrl,
  onVerified,
}: {
  agencyId: string;
  /** La web que la agencia guardó en el paso 1, si la hay. */
  defaultUrl?: string | null;
  /** Ya comprobada antes (GET /channels), para no obligar a repetirlo. */
  verifiedUrl?: string | null;
  onVerified?: (result: WebchatVerifyOut) => void;
}) {
  const [url, setUrl] = useState(defaultUrl ?? verifiedUrl ?? "");
  const [state, setState] = useState<State>({ kind: "idle" });

  async function check(e: React.FormEvent) {
    e.preventDefault();
    if (state.kind === "checking" || !url.trim()) return;
    setState({ kind: "checking" });
    try {
      const out = await api<WebchatVerifyOut>(
        `/api/v1/agencies/${agencyId}/channels/webchat/verify`,
        { method: "POST", body: JSON.stringify({ url: url.trim() }) }
      );
      setState({ kind: "done", result: out });
      if (out.verified) onVerified?.(out);
    } catch {
      setState({ kind: "error" });
    }
  }

  const alreadyVerified =
    !!verifiedUrl && !(state.kind === "done" && !state.result.verified);

  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <p className="text-sm font-medium text-slate-800">
        Compruébalo de verdad: ¿está el chat en tu web?
      </p>
      <p className="mt-1 text-sm text-slate-600">
        Abrimos tu web y buscamos el código. Tarda unos segundos.
      </p>

      <form onSubmit={check} className="mt-3 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="webchat-verify-url">
          Dirección de tu web
        </label>
        <input
          id="webchat-verify-url"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.tuinmobiliaria.es"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={state.kind === "checking" || !url.trim()}
          className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
        >
          {state.kind === "checking" ? "Comprobando…" : "Comprobar mi web"}
        </button>
      </form>

      {state.kind === "idle" && alreadyVerified && (
        <p className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-sm text-emerald-800">
          Comprobado en {verifiedUrl} ✓
        </p>
      )}

      {state.kind === "done" && state.result.verified && (
        <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          <strong>Comprobado: el chat está en tu web.</strong> Lo hemos
          encontrado en {state.result.url}. A partir de ahora, cualquiera que
          escriba desde ahí aparecerá en tu bandeja.
        </div>
      )}

      {state.kind === "done" && !state.result.verified && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {EXPLANATION[state.result.reason]}
        </div>
      )}

      {state.kind === "error" && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          No hemos podido hacer la comprobación ahora mismo. Inténtalo de nuevo
          en un minuto.
        </div>
      )}
    </div>
  );
}

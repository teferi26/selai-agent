// (189) "Envíame un WhatsApp de prueba" — la única comprobación que
// demuestra que el canal funciona de verdad.
//
// Conectar WhatsApp terminaba en un badge verde y ninguna prueba. La
// agencia descubría que no llegaba nada cuando ya había perdido un lead
// real. Aquí se envía un mensaje de verdad con las credenciales guardadas,
// al móvil que escriba el dueño; y le pedimos que conteste, porque esa
// respuesta es la que prueba la otra mitad (el webhook de entrada).
//
// Se usa en el paso 4 del asistente y en Ajustes → Canales.
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { apiErrorStatus, type WhatsappTestOut } from "@/lib/channels";

type State =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "sent"; phone: string }
  | { kind: "error"; message: string };

/** Errores del backend → una frase que diga qué hacer. */
function messageFor(status: number | null): string {
  if (status === 501)
    return "Todavía no hay ningún WhatsApp conectado. Conéctalo primero y vuelve a probar.";
  if (status === 422)
    return "Ese número no parece un móvil. Escríbelo con prefijo (+34 600 11 22 33) o sin espacios.";
  if (status === 502)
    return "Meta no ha aceptado el envío. Suele ser que el número de destino no está dado de alta en tu app de prueba de Meta, o que el token ha caducado.";
  return "No hemos podido enviarlo ahora mismo. Inténtalo de nuevo en un minuto.";
}

export default function WhatsAppTest({ agencyId }: { agencyId: string }) {
  const [phone, setPhone] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (state.kind === "sending" || !phone.trim()) return;
    setState({ kind: "sending" });
    try {
      const out = await api<WhatsappTestOut>(
        `/api/v1/agencies/${agencyId}/channels/whatsapp/test`,
        { method: "POST", body: JSON.stringify({ to_phone: phone.trim() }) }
      );
      setState({ kind: "sent", phone: out.to_phone });
    } catch (err) {
      setState({ kind: "error", message: messageFor(apiErrorStatus(err)) });
    }
  }

  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <p className="text-sm font-medium text-slate-800">
        Compruébalo: mándate un WhatsApp de prueba
      </p>
      <p className="mt-1 text-sm text-slate-600">
        Escribe tu móvil y te enviamos un mensaje desde el número de tu agencia.
        Si te llega, la salida funciona.
      </p>
      <form onSubmit={send} className="mt-3 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="wa-test-phone">
          Tu número de móvil
        </label>
        <input
          id="wa-test-phone"
          type="tel"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="+34 600 11 22 33"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={state.kind === "sending" || !phone.trim()}
          className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
        >
          {state.kind === "sending" ? "Enviando…" : "Enviar prueba"}
        </button>
      </form>

      {state.kind === "sent" && (
        <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          <strong>Comprobado: revisa tu móvil.</strong> Te hemos enviado
          «Mensaje de prueba de selai» al {state.phone}. Contéstale algo y
          verás la respuesta de tu asistente: eso confirma también la entrada.
        </div>
      )}
      {state.kind === "error" && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {state.message}
        </div>
      )}
    </div>
  );
}

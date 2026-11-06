// (168) Intervención humana: "Tomar el control" (POST /handoff) y el
// compositor de respuesta manual (POST /reply), que envía por el canal real.
//
// Optimista con rollback: el mensaje se pinta al instante; si el envío falla
// desaparece y se explica por qué. Cuando el servidor ya devuelve el mensaje
// (llega en `knownMessageIds` tras el refresh) retiramos la copia local para
// no duplicar la burbuja.
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ErrorState } from "@/components/ui";
import { api, type ManualReply } from "@/lib/api";

type Channel = "whatsapp" | "email" | "voice" | "sms" | "web";

const CHANNEL_LABEL: Record<Channel, string> = {
  whatsapp: "WhatsApp",
  email: "correo",
  voice: "una llamada",
  sms: "SMS",
  web: "el chat de la web",
};

/** Canales por los que el backend sabe enviar hoy (el resto responde 501). */
const REPLYABLE: Channel[] = ["whatsapp", "email"];

type Pending = {
  tmpId: string;
  text: string;
  status: "sending" | "sent";
  realId?: string;
};

const MAX_LENGTH = 4000;

export default function ReplyComposer({
  agencyId,
  conversationId,
  channel,
  handedOff,
  knownMessageIds,
}: {
  agencyId: string;
  conversationId: string;
  /** null cuando no hemos podido leer el estado de la conversación. */
  channel: Channel | null;
  handedOff: boolean;
  /** Ids de los mensajes que ya pinta el servidor. */
  knownMessageIds: string[];
}) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [takenOver, setTakenOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inControl = handedOff || takenOver;
  const canReply = channel !== null && REPLYABLE.includes(channel);
  const known = new Set(knownMessageIds);
  const visible = pending.filter((p) => !(p.realId && known.has(p.realId)));

  async function takeOver() {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/agencies/${agencyId}/conversations/${conversationId}/handoff`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      setTakenOver(true);
      router.refresh();
    } catch {
      setError("No se pudo tomar el control. Inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    const body = text.trim();
    if (!body) return;

    const tmpId = `tmp-${Date.now()}`;
    setPending((list) => [...list, { tmpId, text: body, status: "sending" }]);
    setText("");
    setError(null);
    setBusy(true);
    try {
      const sent = await api<ManualReply>(
        `/api/v1/agencies/${agencyId}/conversations/${conversationId}/reply`,
        { method: "POST", body: JSON.stringify({ text: body }) }
      );
      setPending((list) =>
        list.map((p) =>
          p.tmpId === tmpId ? { ...p, status: "sent", realId: sent.id } : p
        )
      );
      // El envío implica handoff en el backend: reflejarlo aquí también.
      setTakenOver(true);
      router.refresh();
    } catch (err) {
      // Rollback: fuera la burbuja optimista y devolvemos el texto al campo.
      setPending((list) => list.filter((p) => p.tmpId !== tmpId));
      setText(body);
      setError(explain(err, channel));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4">
      {visible.map((p) => (
        <div key={p.tmpId} className="ml-auto mb-3 max-w-[85%] rounded-lg px-3 py-2">
          <div
            className={`rounded-lg bg-emerald-50 px-3 py-2 text-emerald-900 ${
              p.status === "sending" ? "opacity-60" : ""
            }`}
          >
            <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">
              Agente · {p.status === "sending" ? "enviando…" : "enviado"}
            </div>
            <div className="whitespace-pre-wrap">{p.text}</div>
          </div>
        </div>
      ))}

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        {!inControl && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-slate-600">
              Ahora mismo responde la IA. Si escribes tú, la IA deja de contestar a
              este cliente.
            </p>
            <button
              type="button"
              onClick={takeOver}
              disabled={busy}
              className="whitespace-nowrap rounded-md border border-selai-300 px-3 py-1.5 text-sm font-medium text-selai-700 hover:bg-selai-50 disabled:opacity-60"
            >
              {busy ? "Tomando el control…" : "Tomar el control"}
            </button>
          </div>
        )}

        {canReply ? (
          <>
            <label htmlFor="manual-reply" className="sr-only">
              Escribe tu respuesta
            </label>
            <textarea
              id="manual-reply"
              value={text}
              onChange={(e) => setText(e.target.value.slice(0, MAX_LENGTH))}
              rows={3}
              placeholder={`Escribe aquí y se envía por ${
                CHANNEL_LABEL[channel as Channel]
              }…`}
              className="w-full resize-y rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
            />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs text-slate-400">
                {text.length}/{MAX_LENGTH}
              </span>
              <button
                type="button"
                onClick={send}
                disabled={busy || text.trim().length === 0}
                className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
              >
                {busy ? "Enviando…" : "Enviar"}
              </button>
            </div>
          </>
        ) : (
          <p className="text-sm text-slate-600">
            {channel === null
              ? "No hemos podido saber por qué canal llegó esta conversación, así que no podemos enviar la respuesta desde aquí."
              : `Esta conversación llegó por ${CHANNEL_LABEL[channel]} y todavía no podemos responder desde selai por ese canal. Puedes tomar el control para que la IA no conteste y hablar tú con el cliente.`}
          </p>
        )}

        {/* El texto vuelve al campo tras un fallo, así que "Reintentar" es
            literalmente reenviar lo mismo. */}
        {error && (
          <ErrorState
            className="mt-3"
            title="No se ha enviado"
            message={error}
            onRetry={() => void send()}
            retrying={busy}
          />
        )}
      </div>
    </div>
  );
}

/** Mensajes de error honestos a partir de lo que devuelve el backend.
 * En todos estos casos el backend no ha guardado nada, así que el texto
 * vuelve al campo y aquí se explica qué ha pasado de verdad. */
function explain(err: unknown, channel: Channel | null): string {
  const raw = err instanceof Error ? err.message : "";
  if (raw.includes("lead_without_whatsapp"))
    return "No tenemos el teléfono de este cliente, así que no se puede enviar por WhatsApp.";
  if (raw.includes("lead_without_email"))
    return "No tenemos el correo de este cliente, así que no se puede enviar por email.";
  if (raw.includes("channel_not_supported"))
    return "Todavía no podemos responder por este canal desde selai.";
  if (raw.includes("whatsapp_window_expired"))
    return (
      "WhatsApp no deja escribir a un cliente pasadas 24 horas desde su último " +
      "mensaje, así que este no ha salido. Llámale, o espera a que te escriba " +
      "él para poder contestarle por aquí."
    );
  if (raw.includes("channel_not_connected"))
    return channel === "email"
      ? "Tu correo todavía no está conectado en selai, así que el mensaje no ha salido. Puedes conectarlo en Puesta en marcha."
      : "Tu WhatsApp todavía no está conectado en selai, así que el mensaje no ha salido. Puedes conectarlo en Puesta en marcha.";
  if (raw.includes("send_failed"))
    return "El mensaje no ha salido: el proveedor lo ha rechazado. No se ha guardado nada.";
  return "No se ha podido enviar el mensaje. Inténtalo de nuevo.";
}

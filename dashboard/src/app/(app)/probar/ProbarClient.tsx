// Chat de prueba contra el agente real de la agencia. Cada sesión de prueba
// usa un session_id propio, igual que haría un visitante del widget web.
// Llama a api.selai.es DIRECTAMENTE (CORS lo permite): a través del proxy de
// Vercel todas las agencias compartirían la misma IP y el límite del chat
// público (20/min por IP) las bloquearía entre sí.
"use client";

import { useEffect, useRef, useState } from "react";

const API_BASE = (process.env.NEXT_PUBLIC_PUBLIC_API_URL ?? "https://api.selai.es")
  .trim()
  .replace(/\/+$/, "");

type ChatMessage = { role: "user" | "assistant"; text: string };

function newSessionId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Math.random().toString(36).slice(2)}${Date.now()}`;
}

export default function ProbarClient({
  slug,
  agencyName,
}: {
  slug: string;
  agencyName: string;
}) {
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/webhooks/web/${slug}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, text }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const out = (await res.json()) as { reply: string };
      setMessages((m) => [
        ...m,
        { role: "assistant", text: out.reply || "(sin respuesta)" },
      ]);
    } catch {
      setError("No se pudo enviar el mensaje. Inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setSessionId(newSessionId());
    setMessages([]);
    setError(null);
  }

  return (
    <div className="max-w-2xl">
      <header className="mb-4">
        <h2 className="text-2xl font-semibold">Prueba tu IA</h2>
        <p className="text-sm text-slate-500">
          Habla por chat con el asistente de {agencyName}, igual que haría un cliente en tu
          web. Pídele una visita, pregunta por un piso… y mira cómo responde.
        </p>
      </header>

      <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
        ⚠️ Esto consume tu agente real, como si fueras un cliente: la conversación
        aparecerá en tu bandeja y puede crear un lead de prueba.
      </div>

      <div className="rounded-xl border border-slate-200 bg-white">
        <div className="h-96 space-y-3 overflow-y-auto p-4">
          {messages.length === 0 && (
            <p className="pt-8 text-center text-sm text-slate-400">
              Escribe abajo para empezar. Por ejemplo: «Hola, busco un piso de 3
              habitaciones en el centro».
            </p>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${
                  m.role === "user"
                    ? "rounded-br-sm bg-selai-600 text-white"
                    : "rounded-bl-sm bg-slate-100 text-slate-800"
                }`}
              >
                {m.text}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-sm bg-slate-100 px-3.5 py-2 text-sm text-slate-400">
                Escribiendo…
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {error && (
          <div className="border-t border-red-100 bg-red-50 px-4 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <form onSubmit={send} className="flex items-center gap-2 border-t border-slate-200 p-3">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Escribe como si fueras un cliente…"
            maxLength={4000}
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
            aria-label="Mensaje para el asistente"
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-50"
          >
            Enviar
          </button>
        </form>
      </div>

      {messages.length > 0 && (
        <button
          onClick={reset}
          className="mt-3 text-xs text-slate-400 underline hover:text-slate-600"
        >
          Empezar una conversación nueva
        </button>
      )}
    </div>
  );
}

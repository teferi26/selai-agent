// Embedded test chat for the final wizard step. Same real endpoint as
// /probar (POST /webhooks/web/{slug}/message), called DIRECTLY on
// api.selai.es — through the Vercel proxy every agency would share one IP
// and the public chat's per-IP budget would lock them out of each other.
// Notifies the parent on the first reply so the wizard can mark the
// "prueba final" step as done automatically.
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

export default function WizardChat({
  slug,
  onFirstReply,
}: {
  slug: string;
  onFirstReply?: () => void;
}) {
  const [sessionId] = useState(newSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gotReply = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
      if (!gotReply.current) {
        gotReply.current = true;
        onFirstReply?.();
      }
    } catch {
      setError("No se pudo enviar el mensaje. Inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <div className="h-72 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <p className="pt-6 text-center text-sm text-slate-400">
            Escribe como si fueras un cliente. Por ejemplo: «Hola, busco un piso de 3
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
  );
}

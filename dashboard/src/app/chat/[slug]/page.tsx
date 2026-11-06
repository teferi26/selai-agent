// Public web chat for an agency — the page the website widget (public/
// widget.js) opens in its iframe, also reachable directly at /chat/{slug}.
// Standalone (no dashboard sidebar, no auth): talks straight to the public
// backend endpoint POST {API}/webhooks/web/{slug}/message (CORS allows the
// www.selai.es origin). The session id persists in sessionStorage so a
// visitor keeps their conversation while browsing the agency site.
"use client";

import { use, useEffect, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_PUBLIC_API_URL ?? "https://api.selai.es";
const SEND_TIMEOUT_MS = 30_000;

type ChatMessage = { role: "user" | "assistant"; text: string };
type RetryMessage = { id: string; text: string };

function newMessageId(): string {
  try {
    if ("randomUUID" in crypto) return crypto.randomUUID();
  } catch {
    // A restrictive embedded browser may not expose Web Crypto.
  }
  return `msg-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

function getSessionId(slug: string): string {
  const key = `selai-chat-session-${slug}`;
  try {
    const existing = window.sessionStorage.getItem(key);
    if (existing) return existing;
    const fresh =
      "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Math.random().toString(36).slice(2)}${Date.now()}`;
    window.sessionStorage.setItem(key, fresh);
    return fresh;
  } catch {
    // sessionStorage bloqueado (cookies de terceros, modo privado): sesión
    // efímera en memoria.
    return `${Math.random().toString(36).slice(2)}${Date.now()}`;
  }
}

export default function PublicChatPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const sessionIdRef = useRef<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryMessage, setRetryMessage] = useState<RetryMessage | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, busy]);

  useEffect(() => {
    function closeEmbeddedChat(event: KeyboardEvent) {
      if (event.key === "Escape" && window.parent !== window) {
        window.parent.postMessage({ type: "selai:close" }, "*");
      }
    }
    window.addEventListener("keydown", closeEmbeddedChat);
    return () => window.removeEventListener("keydown", closeEmbeddedChat);
  }, []);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    await submitMessage({ id: newMessageId(), text }, true);
  }

  async function submitMessage(message: RetryMessage, appendUserMessage: boolean) {
    if (busy) return;
    if (!sessionIdRef.current) sessionIdRef.current = getSessionId(slug);
    setError(null);
    setRetryMessage(null);
    if (appendUserMessage) {
      setMessages((m) => [...m, { role: "user", text: message.text }]);
    }
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/webhooks/web/${encodeURIComponent(slug)}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          message_id: message.id,
          text: message.text,
        }),
        signal: AbortSignal.timeout(SEND_TIMEOUT_MS),
      });
      if (!res.ok) throw new Error(String(res.status));
      const out = (await res.json()) as { reply: string };
      setMessages((m) => [
        ...m,
        { role: "assistant", text: out.reply || "(sin respuesta)" },
      ]);
    } catch {
      setError("No se pudo enviar el mensaje. Inténtalo de nuevo en un momento.");
      setRetryMessage(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-dvh flex-col bg-white">
      <header className="flex items-center gap-2.5 border-b border-slate-200 bg-selai-600 px-4 py-3 text-white">
        <span
          aria-hidden
          className="flex h-8 w-8 items-center justify-center rounded-full bg-white/15 text-base"
        >
          💬
        </span>
        <div>
          <p className="text-sm font-semibold leading-tight">
            Chatea con nosotros
          </p>
          <p className="text-[11px] leading-tight text-white/75">
            Respuesta al momento, a cualquier hora
          </p>
        </div>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-slate-100 px-3.5 py-2 text-sm text-slate-800">
              ¡Hola! Soy el asistente de la inmobiliaria. Pregúntame por cualquier
              piso, precio o zona, o pide una visita cuando quieras.
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${
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
        <div
          className="flex items-center justify-between gap-3 border-t border-red-100 bg-red-50 px-4 py-2 text-sm text-red-700"
          role="alert"
        >
          <span>{error}</span>
          {retryMessage && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void submitMessage(retryMessage, false)}
              className="shrink-0 rounded-md border border-red-300 px-2.5 py-1 font-medium hover:bg-red-100 disabled:opacity-50"
            >
              Reintentar
            </button>
          )}
        </div>
      )}

      {/* (234) Aviso de primera capa del art. 13 RGPD + art. 50 del AI Act,
          antes de que la IA pida un teléfono o un email. Va encima del campo
          de escritura a propósito: tiene que estar a la vista antes del
          primer mensaje, no enterrado al final de la página. */}
      <p className="border-t border-slate-100 px-3 pt-2 text-center text-[11px] text-slate-500">
        Chat atendido por IA ·{" "}
        <a
          href="https://www.selai.es/privacidad"
          target="_blank"
          rel="noreferrer"
          className="underline hover:text-slate-700"
        >
          Política de privacidad
        </a>
      </p>

      <form
        onSubmit={send}
        className="flex items-center gap-2 border-t border-slate-200 p-3"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Escribe tu mensaje…"
          maxLength={4000}
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
          aria-label="Tu mensaje"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-50"
        >
          Enviar
        </button>
      </form>

      <p className="border-t border-slate-100 px-3 py-1.5 text-center text-[10px] text-slate-400">
        Asistente impulsado por{" "}
        <a
          href="https://www.selai.es"
          target="_blank"
          rel="noreferrer"
          className="underline hover:text-slate-600"
        >
          selai
        </a>
      </p>
      {/* El aviso legal vive encima del formulario, no aquí: en móvil este
          pie queda por debajo del teclado y no se leería nunca. */}
    </div>
  );
}

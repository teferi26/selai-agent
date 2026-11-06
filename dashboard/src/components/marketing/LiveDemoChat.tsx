"use client";

// Demo de la landing (backlog #157).
//
// Habla de verdad con el chat público de una inmobiliaria de demostración:
//   POST {NEXT_PUBLIC_PUBLIC_API_URL}/webhooks/web/{slug}/message
//
// OJO con el encuadre: al otro lado NO hay un bot que sepa de selai, hay el
// mismo agente comercial que atendería a los leads de una agencia (busca en su
// catálogo, cualifica, agenda visitas). Por eso la demo pide al visitante que
// escriba como escribiría su cliente. Si le preguntas por el precio de selai,
// el agente hablará de pisos — sería la respuesta correcta para un lead y la
// equivocada para esta página.
//
// Reglas de honestidad:
//   · sin agencia de demostración configurada NO se llama a nada y se dice
//     desde el primer momento que es una demo guiada;
//   · cada burbuja lleva etiqueta de origen (bienvenida fija / IA real / guion);
//   · el guion nunca se inventa pisos, precios ni huecos de agenda: explica
//     qué haría la IA con el catálogo de la agencia delante.
import { useEffect, useRef, useState } from "react";

import { trackFunnel } from "./analytics";
import { Reveal } from "./animated";
import { ISend } from "./icons";
import { SelaiLogo } from "./SelaiLogo";
import { Container, EyebrowH2, Section } from "./ui";

const API_BASE = (process.env.NEXT_PUBLIC_PUBLIC_API_URL ?? "https://api.selai.es")
  .trim()
  .replace(/\/+$/, "");
// Sin slug configurado no hay agencia de demostración a la que llamar: no
// inventamos un "demo" por defecto que devolvería 404 en cada mensaje mientras
// la cabecera presume de estar en línea.
const DEMO_SLUG = (process.env.NEXT_PUBLIC_DEMO_AGENCY_SLUG ?? "").trim();
const LIVE_ENABLED = DEMO_SLUG.length > 0 && API_BASE.length > 0;
const TIMEOUT_MS = 10_000;
/** Tope del backend (`WebMessage.text`). */
const MAX_CHARS = 4000;

/** De dónde salió cada burbuja. Se muestra al usuario, no es telemetría. */
type Source = "intro" | "live" | "script";

type Msg =
  | { who: "ai" | "user"; t: string; src?: Source }
  /** Aviso de la propia web, no del asistente. */
  | { who: "note"; t: string; src?: undefined };

const LABELS: Record<Source, string> = {
  intro: "Mensaje de bienvenida, siempre el mismo",
  live: "Respuesta real de la IA, ahora mismo",
  script: "Demo guiada: esto lo hemos escrito nosotros, no la IA",
};

const SUGGESTIONS = [
  "Hola, he visto el anuncio del piso",
  "Busco 3 habitaciones con ascensor",
  "¿Cuánto piden por él?",
  "¿Puedo verlo el jueves por la tarde?",
  "Quiero hablar con una persona",
];

const WELCOME =
  "Hola, soy el asistente virtual de la inmobiliaria de demostración: una IA, no una persona. Escríbeme como si acabaras de ver uno de nuestros anuncios.";

/** El backend exige un session_id de 8 a 64 caracteres. */
function newSessionId(): string {
  try {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  } catch {
    // crypto no disponible: seguimos al fallback
  }
  return `demo-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

/**
 * Respuestas del modo guion. No simulan a la IA contestando: explican qué
 * haría la IA con el catálogo y la agenda de una agencia de verdad delante.
 * Nada de pisos, precios ni huecos inventados.
 */
function scriptedAnswer(q: string): string {
  const lower = q.toLowerCase();
  if (/visit|ver\s|verlo|verla|cita|quedar|jueves|viernes|lunes|martes|mi[eé]rcoles|s[aá]bado|domingo|tarde|ma[ñn]ana/.test(lower)) {
    return "Con tu agenda conectada, aquí miraría los huecos libres de tu equipo, te propondría dos que encajen y dejaría la visita cerrada en el calendario, con recordatorio para el cliente y para el agente.";
  }
  if (/precio|cuesta|cu[aá]nto|vale|rebaj|negoci|hipotec|financi|cuota|entrada/.test(lower)) {
    return "Con tu catálogo cargado, aquí daría el precio exacto del inmueble del anuncio y mandaría la ficha con fotos; si preguntas por la cuota, la calcula con una calculadora, no a ojo. Lo que nunca hace es inventarse precios ni prometer rebajas.";
  }
  if (/human|persona|agente|comercial|hablar con/.test(lower)) {
    return "Aquí avisaría al agente de guardia y se apartaría de la conversación, dejándole un resumen de tres líneas para que retome sin hacerte repetir nada. Basta con pedirlo.";
  }
  if (/busc|quier|necesit|habitacion|ascensor|zona|barrio|piso|casa|chalet|[aá]tico|alquil|compr/.test(lower)) {
    return "Aquí buscaría en el catálogo de tu agencia y te enseñaría dos o tres que encajen, con precio, metros y zona, y te iría preguntando lo justo (presupuesto, plazos, financiación) para dejarle el trabajo hecho al agente.";
  }
  if (/vend|tasa|valora|mi piso|mi casa|propietari/.test(lower)) {
    return "Si quien escribe es propietario, aquí lo trataría como una captación: recogería zona, tipo, precio en mente y urgencia, y avisaría al equipo al momento. No tasa el inmueble: eso lo hace una persona.";
  }
  return "En modo guion no puedo consultar catálogo ni agenda, así que no me lo invento. Con tu agencia conectada, esta pregunta la contestaría con tus inmuebles reales y tus horarios reales — puedes probarlo tú en el panel durante los 14 días gratis.";
}

type LiveResult =
  | { kind: "reply"; text: string }
  | { kind: "rate_limited" }
  | { kind: "unavailable" };

/**
 * Llama al chat público de la agencia de demostración. Nunca lanza: el que
 * llama decide qué enseñar. Corta a los TIMEOUT_MS para que no se quede
 * colgada esperando.
 */
async function askLiveAgent(sessionId: string, text: string): Promise<LiveResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}/webhooks/web/${encodeURIComponent(DEMO_SLUG)}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, text }),
      signal: controller.signal,
    });
    // 429 = hemos escrito muy seguido. La IA sigue viva, así que no
    // degradamos la demo entera por esto.
    if (res.status === 429) return { kind: "rate_limited" };
    if (!res.ok) return { kind: "unavailable" };
    const out = (await res.json()) as { reply?: unknown };
    const reply = typeof out.reply === "string" ? out.reply.trim() : "";
    return reply ? { kind: "reply", text: reply } : { kind: "unavailable" };
  } catch {
    // Red caída, CORS, timeout, JSON inválido: da igual, tenemos guion.
    return { kind: "unavailable" };
  } finally {
    clearTimeout(timer);
  }
}

export function LiveDemoChat() {
  const [msgs, setMsgs] = useState<Msg[]>([{ who: "ai", t: WELCOME, src: "intro" }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // Una vez que la IA real falla, dejamos de esperar 10 s en cada mensaje.
  const [degraded, setDegraded] = useState(!LIVE_ENABLED);
  // ¿Hemos llegado a ver una respuesta real? Hasta entonces no presumimos de
  // estar en línea.
  const [confirmedLive, setConfirmedLive] = useState(false);
  const sessionRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [msgs, busy]);

  const send = async (text?: string) => {
    const fromInput = text === undefined;
    const q = (text ?? input).trim().slice(0, MAX_CHARS);
    if (!q || busy) return;
    if (fromInput) setInput("");
    setMsgs((m) => [...m, { who: "user", t: q }]);
    setBusy(true);

    if (!sessionRef.current) sessionRef.current = newSessionId();
    const live: LiveResult = degraded
      ? { kind: "unavailable" }
      : await askLiveAgent(sessionRef.current, q);

    if (live.kind === "rate_limited") {
      setMsgs((m) => [
        ...m,
        {
          who: "note",
          t: "Vas muy rápido para una demo pública. Espera un minuto y sigue escribiendo.",
        },
      ]);
      setBusy(false);
      return;
    }

    if (live.kind === "reply") {
      setConfirmedLive(true);
      setMsgs((m) => [...m, { who: "ai", t: live.text, src: "live" }]);
    } else {
      if (!degraded) setDegraded(true);
      setMsgs((m) => [...m, { who: "ai", t: scriptedAnswer(q), src: "script" }]);
    }
    setBusy(false);
    trackFunnel("live_demo_send", { modo: live.kind === "reply" ? "live" : "script" });
  };

  const status = degraded
    ? { text: "demo guiada", dot: "bg-amber-500", tone: "text-amber-600", pulse: false }
    : confirmedLive
      ? { text: "IA respondiendo en directo", dot: "bg-emerald-500", tone: "text-emerald-500", pulse: true }
      : { text: "escribe y probamos con la IA real", dot: "bg-ink-300", tone: "text-ink-500", pulse: false };

  const canSend = input.trim().length > 0 && !busy;

  return (
    <Section className="py-24 md:py-32">
      <Container>
        <Reveal>
          <EyebrowH2
            eyebrow="Demo"
            title="Habla con la IA como si fueras tu propio cliente."
            sub="Escríbele lo que te escribiría alguien que acaba de ver uno de tus anuncios. Al otro lado contesta la IA de una inmobiliaria de demostración, con un catálogo de ejemplo: la misma que atendería a tus leads. Cuando no está disponible verás una demo guiada, y lo pone debajo de cada mensaje."
          />
        </Reveal>
        <Reveal delay={0.1}>
          <div className="mt-10 mx-auto max-w-[760px] bg-white rounded-3xl ring-1 ring-ink-900/5 shadow-card overflow-hidden">
            <div className="flex items-center gap-3 px-5 py-4 border-b border-ink-900/5 bg-paper/50">
              <div className="w-9 h-9 rounded-xl bg-selai-500 flex items-center justify-center">
                <SelaiLogo dark className="text-[12px]" />
              </div>
              <div className="min-w-0">
                <div className="font-medium text-[14px] text-ink-900 truncate">
                  Inmobiliaria de demostración
                </div>
                <div className={`text-[11px] font-mono flex items-center gap-1.5 ${status.tone}`}>
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${status.dot} ${
                      status.pulse ? "animate-pulse" : ""
                    }`}
                  />
                  {status.text}
                </div>
              </div>
            </div>
            <div
              ref={scrollRef}
              className="px-5 py-5 h-[360px] overflow-y-auto space-y-3 bg-gradient-to-b from-white to-selai-50/30"
            >
              {msgs.map((m, i) =>
                m.who === "note" ? (
                  <p
                    key={i}
                    role="status"
                    className="text-center text-[12px] text-ink-500 px-6 py-2"
                  >
                    {m.t}
                  </p>
                ) : (
                  <div
                    key={i}
                    className={`flex flex-col ${m.who === "user" ? "items-end" : "items-start"}`}
                  >
                    <div
                      className={`max-w-[78%] px-4 py-2.5 rounded-2xl text-[14px] leading-relaxed whitespace-pre-wrap ${
                        m.who === "user"
                          ? "bg-ink-900 text-white rounded-tr-sm"
                          : "bg-white ring-1 ring-ink-900/5 text-ink-900 rounded-tl-sm shadow-sm"
                      }`}
                    >
                      {m.t}
                    </div>
                    {m.src && (
                      <div
                        className={`mt-1 px-1 text-[10.5px] font-mono ${
                          m.src === "live"
                            ? "text-emerald-600"
                            : m.src === "script"
                              ? "text-amber-600"
                              : "text-ink-300"
                        }`}
                      >
                        {LABELS[m.src]}
                      </div>
                    )}
                  </div>
                ),
              )}
              {busy && (
                <div className="flex justify-start items-center gap-2">
                  <div className="bg-white ring-1 ring-ink-900/5 rounded-2xl rounded-tl-sm px-4 py-3 flex gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-ink-300 dot" />
                    <span className="w-1.5 h-1.5 rounded-full bg-ink-300 dot" />
                    <span className="w-1.5 h-1.5 rounded-full bg-ink-300 dot" />
                  </div>
                  <span className="text-[11px] text-ink-500 font-mono" role="status">
                    escribiendo…
                  </span>
                </div>
              )}
            </div>
            <div className="px-5 py-3 border-t border-ink-900/5 bg-paper/40">
              <div className="flex flex-wrap gap-1.5 mb-3">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => void send(s)}
                    disabled={busy}
                    className="min-h-11 text-[12px] px-2.5 py-1.5 rounded-full bg-selai-100 text-selai-700 hover:bg-selai-200 disabled:opacity-50 transition"
                  >
                    {s}
                  </button>
                ))}
              </div>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void send();
                }}
                className="flex gap-2"
              >
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Escribe como escribiría tu cliente…"
                  maxLength={MAX_CHARS}
                  className="flex-1 px-4 py-3 rounded-xl bg-white ring-1 ring-ink-900/10 focus:ring-selai-400 text-[14px] outline-none"
                  aria-label="Tu mensaje para la demo"
                />
                <button
                  type="submit"
                  disabled={!canSend}
                  className="px-4 rounded-xl bg-accent text-white disabled:opacity-40 hover:bg-[#FF6A45]"
                  aria-label="Enviar"
                >
                  <ISend size={16} />
                </button>
              </form>
            </div>
          </div>
        </Reveal>
      </Container>
    </Section>
  );
}

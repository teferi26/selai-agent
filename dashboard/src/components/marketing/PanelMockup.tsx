// "Así es por dentro" (backlog #23) — two crisp HTML/CSS mockups of the real
// panel inside a fake browser window: the conversation inbox and the setup
// checklist. No external images, all server-rendered markup.
import { Reveal } from "./animated";
import { ICheck } from "./icons";
import { Container, EyebrowH2, Section } from "./ui";

function BrowserFrame({
  url,
  children,
}: {
  url: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl bg-white ring-1 ring-ink-900/10 shadow-card overflow-hidden">
      {/* Browser chrome */}
      <div className="flex items-center gap-3 px-4 py-2.5 bg-paper border-b border-ink-900/5">
        <div className="flex gap-1.5" aria-hidden>
          <span className="w-2.5 h-2.5 rounded-full bg-[#FF5F57]" />
          <span className="w-2.5 h-2.5 rounded-full bg-[#FEBC2E]" />
          <span className="w-2.5 h-2.5 rounded-full bg-[#28C840]" />
        </div>
        <div className="flex-1 mx-2 px-3 py-1 rounded-md bg-white ring-1 ring-ink-900/5 font-mono text-[11px] text-ink-500 truncate">
          {url}
        </div>
      </div>
      {children}
    </div>
  );
}

const CONVERSATIONS: ReadonlyArray<{
  initials: string;
  name: string;
  channel: string;
  channelColor: string;
  preview: string;
  time: string;
  tag?: string;
  tagColor?: string;
  unread?: boolean;
}> = [
  {
    initials: "MG",
    name: "Marta García",
    channel: "WhatsApp",
    channelColor: "bg-wa/15 text-emerald-700",
    preview: "Perfecto, el jueves a las 18:00 me va genial para ver el piso de Benalúa 👍",
    time: "hace 2 min",
    tag: "Visita agendada",
    tagColor: "bg-emerald-100 text-emerald-700",
    unread: true,
  },
  {
    initials: "JR",
    name: "Jorge Ruiz",
    channel: "Llamada",
    channelColor: "bg-selai-100 text-selai-700",
    preview: "Resumen IA: busca 3 hab. en Playa de San Juan, presupuesto 240.000 €, hipoteca preaprobada.",
    time: "hace 18 min",
    tag: "Lead caliente",
    tagColor: "bg-accent/15 text-accent",
  },
  {
    initials: "AL",
    name: "Anna Lefèvre",
    channel: "Email",
    channelColor: "bg-selai-100 text-selai-700",
    preview: "Merci ! ¿El ático de la calle Castaños sigue disponible? (conversación en francés)",
    time: "hace 1 h",
  },
  {
    initials: "PS",
    name: "Pablo Sanz",
    channel: "Idealista",
    channelColor: "bg-[#E1F5D9] text-[#3E7C22]",
    preview: "La IA respondió en 15 s: ficha del piso enviada + 2 alternativas en la misma zona.",
    time: "hace 3 h",
  },
] as const;

function InboxMockup() {
  return (
    <BrowserFrame url="app.selai.es/inbox">
      <div className="p-4 md:p-5 bg-white">
        <div className="flex items-center justify-between mb-3">
          <div className="font-display font-bold text-[15px] text-ink-900">Bandeja de entrada</div>
          <span className="rounded-full bg-selai-100 text-selai-700 text-[11px] font-medium px-2.5 py-1">
            4 conversaciones · 1 sin leer
          </span>
        </div>
        <ul className="space-y-2">
          {CONVERSATIONS.map((c) => (
            <li
              key={c.name}
              className={`rounded-xl p-3 ring-1 ${
                c.unread ? "ring-selai-200 bg-selai-50/60" : "ring-ink-900/5 bg-white"
              }`}
            >
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded-full bg-ink-900 text-white flex items-center justify-center text-[12px] font-bold flex-shrink-0">
                  {c.initials}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[13px] font-semibold text-ink-900">{c.name}</span>
                    <span
                      className={`text-[10px] font-medium px-1.5 py-0.5 rounded-md ${c.channelColor}`}
                    >
                      {c.channel}
                    </span>
                    {c.tag && (
                      <span
                        className={`text-[10px] font-medium px-1.5 py-0.5 rounded-md ${c.tagColor}`}
                      >
                        {c.tag}
                      </span>
                    )}
                    <span className="ml-auto text-[10px] text-ink-500 font-mono flex-shrink-0">
                      {c.time}
                    </span>
                  </div>
                  <p className="mt-1 text-[12px] text-ink-500 leading-snug line-clamp-2">
                    {c.preview}
                  </p>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </BrowserFrame>
  );
}

const CHECKLIST: ReadonlyArray<{ label: string; time: string; done: boolean; current?: boolean }> = [
  { label: "Datos de tu agencia", time: "2 min", done: true },
  { label: "Elige la voz de tu asistente", time: "2 min", done: true },
  { label: "Activa el número de tu IA", time: "1 clic", done: true },
  { label: "Conecta WhatsApp", time: "15 min + Meta", done: false, current: true },
  { label: "Reenvía tus portales", time: "5 min", done: false },
  { label: "Sube tu catálogo (CSV)", time: "10 min", done: false },
  { label: "Pon el chat en tu web", time: "2 min", done: false },
  { label: "Conecta tu CRM", time: "opcional", done: false },
  { label: "Prueba tu asistente", time: "2 min", done: false },
];

function ChecklistMockup() {
  const done = CHECKLIST.filter((s) => s.done).length;
  const pct = Math.round((done / CHECKLIST.length) * 100);
  return (
    <BrowserFrame url="app.selai.es/setup">
      <div className="p-4 md:p-5 bg-white">
        <div className="flex items-center justify-between mb-2">
          <div className="font-display font-bold text-[15px] text-ink-900">Puesta en marcha</div>
          <span className="font-mono text-[11px] text-selai-600">
            {done} de {CHECKLIST.length} · {pct}%
          </span>
        </div>
        <div
          className="h-1.5 rounded-full bg-ink-900/5 mb-4 overflow-hidden"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Progreso de la puesta en marcha"
        >
          <div className="h-full rounded-full bg-selai-500" style={{ width: `${pct}%` }} />
        </div>
        <ul className="space-y-2">
          {CHECKLIST.map((s) => (
            <li
              key={s.label}
              className={`flex items-center gap-3 rounded-xl px-3 py-2.5 ring-1 ${
                s.current
                  ? "ring-selai-300 bg-selai-50/70"
                  : s.done
                    ? "ring-ink-900/5 bg-white"
                    : "ring-ink-900/5 bg-paper/60"
              }`}
            >
              <span
                className={`w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 ${
                  s.done
                    ? "bg-selai-500 text-white"
                    : s.current
                      ? "ring-2 ring-selai-400 bg-white"
                      : "ring-1 ring-ink-900/10 bg-white"
                }`}
                aria-hidden
              >
                {s.done && <ICheck size={12} />}
              </span>
              <span
                className={`text-[13px] flex-1 ${
                  s.done ? "text-ink-500 line-through decoration-ink-300" : "text-ink-900 font-medium"
                }`}
              >
                {s.label}
              </span>
              <span className="font-mono text-[10px] text-ink-500 flex-shrink-0">{s.time}</span>
              {s.current && (
                <span className="text-[10px] font-medium text-white bg-accent rounded-md px-2 py-0.5 flex-shrink-0">
                  Siguiente
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </BrowserFrame>
  );
}

export function InsideSelai() {
  return (
    <Section id="por-dentro" className="py-24 md:py-32 bg-paper">
      <Container>
        <Reveal>
          <EyebrowH2
            center
            eyebrow="Así es por dentro"
            title="Un panel que entiendes a la primera."
            sub="Todas tus conversaciones en una bandeja y un asistente de activación que te lleva de cero a funcionando, botón a botón. Esto es lo que ves al entrar."
          />
        </Reveal>
        <div className="mt-12 grid lg:grid-cols-2 gap-6 items-start">
          <Reveal delay={0.05}>
            <InboxMockup />
            <p className="mt-3 text-[13px] text-ink-500 text-center">
              La bandeja: WhatsApp, llamadas, email y portales en un solo sitio, con el resumen de
              la IA.
            </p>
          </Reveal>
          <Reveal delay={0.12}>
            <ChecklistMockup />
            <p className="mt-3 text-[13px] text-ink-500 text-center">
              El asistente de activación: el panel te va marcando cada paso hasta que tu IA
              atiende sola.
            </p>
          </Reveal>
        </div>
      </Container>
    </Section>
  );
}

import { Reveal } from "./animated";
import { ICalendar, ICheck, IHouse } from "./icons";
import { Container, EyebrowH2, Pill, Section } from "./ui";

export function ProblemSolution() {
  return (
    <Section id="por-que-selai" className="py-24 md:py-32">
      <Container>
        <Reveal>
          <EyebrowH2
            eyebrow="Por qué selai"
            title="Un lead que espera sigue buscando."
            sub="No es por mala atención. Es porque nadie puede responder WhatsApp, email y web simultáneamente — a las 23:47 un martes."
          />
        </Reveal>

        <div className="mt-14 grid md:grid-cols-2 gap-5">
          {/* Problem */}
          <Reveal>
            <div className="relative bg-ink-900 grain text-selai-50 rounded-3xl p-7 md:p-10 h-full overflow-hidden">
              <div className="flex items-center justify-between mb-6">
                <Pill tone="dark">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
                  Sin selai
                </Pill>
                <span className="font-mono text-[11px] text-selai-100/50">Mar 23:47</span>
              </div>
              <div className="bg-[#0B141A] rounded-2xl p-4 ring-1 ring-white/5">
                <div className="bg-white/8 rounded-lg rounded-tl-sm px-3.5 py-2 max-w-[85%] text-[14px]">
                  Hola, sigue disponible el de Plaza Nueva?
                  <div className="text-[10px] text-white/40 text-right mt-1 font-mono">
                    23:47 ✓✓
                  </div>
                </div>
                <div className="text-center my-6 text-[11px] text-white/30 font-mono uppercase tracking-wider">
                  — 14 horas de silencio —
                </div>
                <div className="bg-white/8 rounded-lg rounded-tl-sm px-3.5 py-2 max-w-[85%] text-[14px] opacity-60">
                  Vale, gracias. Voy a llamar a Idealista.
                  <div className="text-[10px] text-white/40 text-right mt-1 font-mono">
                    13:51 ✓
                  </div>
                </div>
              </div>
              <p className="mt-6 text-[15px] text-selai-100/70 leading-relaxed">
                Sin una respuesta fuera de horario, ese contacto puede seguir preguntando a otras
                agencias antes de que tu equipo vuelva a abrir.
              </p>
            </div>
          </Reveal>

          {/* Solution */}
          <Reveal delay={0.1}>
            <div className="relative bg-paper rounded-3xl p-7 md:p-10 h-full ring-1 ring-ink-900/5 overflow-hidden">
              <div className="flex items-center justify-between mb-6">
                <Pill tone="selai">
                  <span className="w-1.5 h-1.5 rounded-full bg-selai-500 animate-pulse" />
                  Con selai
                </Pill>
                <span className="font-mono text-[11px] text-ink-500">Mar 23:47:12</span>
              </div>
              <div className="space-y-2">
                <div className="bg-white rounded-lg rounded-tl-sm px-3.5 py-2 max-w-[85%] text-[14px] text-ink-900 ring-1 ring-ink-900/5">
                  Hola, sigue disponible el de Plaza Nueva?
                </div>
                <div className="flex items-center gap-2 text-[10px] text-ink-500 font-mono">
                  <span className="w-1 h-1 rounded-full bg-selai-500 animate-pulse" />
                  selai responde · 12s
                </div>
                <div className="bg-selai-500 text-white rounded-lg rounded-tr-sm px-3.5 py-2 max-w-[88%] ml-auto text-[14px]">
                  ¡Hola Marta! Sí, Plaza Nueva 14, 3º — 245k. ¿Te paso ficha y agendamos visita
                  mañana?
                </div>
                <div className="bg-white rounded-2xl p-3 ring-1 ring-ink-900/5 ml-auto max-w-[88%] flex gap-3">
                  <div className="w-12 h-12 rounded-lg bg-gradient-to-br from-selai-200 to-selai-100 flex items-center justify-center">
                    <IHouse size={20} className="text-selai-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-semibold text-ink-900 truncate">
                      Plaza Nueva, 14 — 3º
                    </div>
                    <div className="text-[11px] text-ink-500">3 hab · 92m² · terraza</div>
                    <div className="text-[12px] font-mono text-selai-600 mt-0.5">245.000 €</div>
                  </div>
                </div>
                <div className="bg-white rounded-2xl p-3 ring-1 ring-selai-200 ml-auto max-w-[88%] flex items-center gap-3">
                  <div className="w-9 h-9 rounded-lg bg-selai-100 flex items-center justify-center">
                    <ICalendar size={16} className="text-selai-600" />
                  </div>
                  <div className="flex-1">
                    <div className="text-[12px] font-semibold text-ink-900">Visita confirmada</div>
                    <div className="text-[11px] text-ink-500 font-mono">
                      Mié 10:30 · agente: Pablo
                    </div>
                  </div>
                  <ICheck size={18} className="text-selai-500" />
                </div>
              </div>
              <p className="mt-6 text-[15px] text-ink-500 leading-relaxed">
                El mismo lead tiene{" "}
                <span className="text-ink-900 font-medium">visita agendada antes del desayuno</span>
                .
              </p>
            </div>
          </Reveal>
        </div>
      </Container>
    </Section>
  );
}

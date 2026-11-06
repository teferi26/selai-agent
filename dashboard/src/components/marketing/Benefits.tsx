import type { ComponentType, ReactNode } from "react";

import { Counter, Reveal } from "./animated";
import { IBolt, IInfinity, ITranslate, ITrend } from "./icons";
import { Container, EyebrowH2, Section } from "./ui";

type IconCmp = ComponentType<{ size?: number; className?: string }>;

function BenefitCard({
  icon: I,
  value,
  label,
  sub,
  color,
  delay = 0,
}: {
  icon: IconCmp;
  value: ReactNode;
  label: string;
  sub: string;
  color: string;
  delay?: number;
}) {
  return (
    <Reveal delay={delay}>
      <div className="group relative bg-white rounded-3xl p-6 md:p-7 ring-1 ring-ink-900/5 shadow-card h-full overflow-hidden transition-all duration-300 hover:-translate-y-1.5 hover:shadow-soft">
        <div
          className={`absolute -top-12 -right-12 w-40 h-40 rounded-full ${color} blur-2xl opacity-40 transition-opacity duration-500 group-hover:opacity-70`}
        />
        <div className="relative">
          <div
            className={`w-11 h-11 rounded-xl ${color} flex items-center justify-center mb-5 transition-transform duration-300 group-hover:scale-110 group-hover:rotate-3`}
          >
            <I size={20} className="text-ink-900" />
          </div>
          <div className="font-display font-bold text-[44px] md:text-[52px] leading-none text-ink-900">
            {value}
          </div>
          <div className="mt-3 font-medium text-[15px] text-ink-900">{label}</div>
          <div className="mt-1 text-[13px] text-ink-500">{sub}</div>
        </div>
      </div>
    </Reveal>
  );
}

export function Benefits() {
  return (
    <Section className="py-24 md:py-32">
      <Container>
        <Reveal>
          <EyebrowH2
            eyebrow="Resultados"
            title="Los números, no el storytelling."
            sub="Lo que selai hace desde el primer día — y lo compruebas tú mismo en las métricas de tu panel durante los 14 días de prueba."
          />
        </Reveal>
        <div className="mt-12 grid grid-cols-2 lg:grid-cols-4 gap-4">
          <BenefitCard
            icon={IBolt}
            value={
              <>
                <Counter to={15} />s
              </>
            }
            label="Objetivo de respuesta"
            sub="medido en tu propio panel"
            color="bg-amber-100"
          />
          <BenefitCard
            icon={ITrend}
            value={
              <>
                1
              </>
            }
            label="Bandeja para todos los canales"
            sub="conversaciones y contexto en un sitio"
            color="bg-emerald-100"
            delay={0.1}
          />
          <BenefitCard
            icon={IInfinity}
            value="24/7"
            label="Sin descanso, sin festivos"
            sub="ni domingos, ni Navidad"
            color="bg-selai-100"
            delay={0.2}
          />
          <BenefitCard
            icon={ITranslate}
            value={<Counter to={5} />}
            label="Idiomas nativos"
            sub="ES · CA · EN · FR · DE"
            color="bg-rose-100"
            delay={0.3}
          />
        </div>
      </Container>
    </Section>
  );
}

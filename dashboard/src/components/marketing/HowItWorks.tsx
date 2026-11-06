"use client";

// 3 cards with SVG path-drawing animations on viewport entry. Client island
// because each card uses its own IntersectionObserver to toggle the
// `.in-view` class that triggers `.draw` keyframes.
import type { ReactNode } from "react";

import { SignupCTA } from "./analytics";
import { Reveal, useStepInView } from "./animated";
import { IArrow } from "./icons";
import { Container, EyebrowH2, Section } from "./ui";

function StepCard({
  n,
  time,
  title,
  body,
  children,
  delay = 0,
}: {
  n: string;
  time: string;
  title: string;
  body: string;
  children: ReactNode;
  delay?: number;
}) {
  const [ref, inView] = useStepInView<HTMLDivElement>();
  return (
    <div
      ref={ref}
      style={{
        opacity: inView ? 1 : 0,
        transform: inView ? "translateY(0)" : "translateY(24px)",
        transition: `all 0.8s cubic-bezier(0.16,1,0.3,1) ${delay}s`,
      }}
    >
      <div className="group bg-white rounded-3xl p-7 ring-1 ring-ink-900/5 shadow-card h-full flex flex-col transition-all duration-300 hover:-translate-y-1.5 hover:shadow-soft">
        <div className="flex items-center justify-between gap-3 mb-5">
          <span className="font-mono text-[11px] text-selai-600 bg-selai-100 px-2 py-1 rounded-md">
            PASO {n}
          </span>
          <span className="font-mono text-[11px] text-ink-500">{time}</span>
        </div>
        <div className={`relative h-[180px] mb-6 ${inView ? "in-view" : ""}`}>{children}</div>
        <h3 className="font-display font-bold text-[24px] leading-tight text-ink-900 mb-2">
          {title}
        </h3>
        <p className="text-[15px] text-ink-500 leading-relaxed">{body}</p>
      </div>
    </div>
  );
}

const StepGfx1 = () => (
  <svg viewBox="0 0 280 180" className="w-full h-full">
    <defs>
      <linearGradient id="g1" x1="0" x2="1">
        <stop offset="0" stopColor="#5B61F0" />
        <stop offset="1" stopColor="#FF7A59" />
      </linearGradient>
    </defs>
    <circle cx="140" cy="90" r="22" fill="white" stroke="#5B61F0" strokeWidth="1.5" />
    <text
      x="140"
      y="95"
      textAnchor="middle"
      fontFamily="Fraunces"
      fontWeight="700"
      fontSize="14"
      fill="#5B61F0"
    >
      selai
    </text>
    {[
      { x: 40, y: 40, l: "WA", c: "#25D366" },
      { x: 240, y: 40, l: "@", c: "#3B82F6" },
      { x: 40, y: 140, l: "☎", c: "#A855F7" },
      { x: 240, y: 140, l: "◐", c: "#FF7A59" },
    ].map((n, i) => (
      <g key={i}>
        <path
          d={`M ${n.x} ${n.y} L 140 90`}
          stroke="url(#g1)"
          strokeWidth="1.5"
          className="draw"
          style={{ animationDelay: `${i * 0.15}s` }}
          pathLength={1}
        />
        <circle cx={n.x} cy={n.y} r="18" fill={n.c} fillOpacity="0.12" stroke={n.c} strokeWidth="1.2" />
        <text x={n.x} y={n.y + 5} textAnchor="middle" fontSize="14" fill={n.c} fontWeight="700">
          {n.l}
        </text>
      </g>
    ))}
  </svg>
);

const StepGfx2 = () => (
  <svg viewBox="0 0 280 180" className="w-full h-full">
    {[0, 1, 2, 3, 4].map((i) => (
      <g key={i} transform={`translate(${30 + i * 8} ${30 + i * 6})`}>
        <rect width="160" height="22" rx="6" fill="white" stroke="#EAEEFF" strokeWidth="1" />
        <circle cx="11" cy="11" r="4" fill="#5B61F0" fillOpacity={0.2 + i * 0.15} />
        <rect x="22" y="7" width={80 - i * 10} height="3" rx="1.5" fill="#1A1B33" fillOpacity="0.3" />
        <rect x="22" y="13" width={50 - i * 5} height="2" rx="1" fill="#1A1B33" fillOpacity="0.15" />
      </g>
    ))}
    <defs>
      <marker id="arr" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
        <path d="M0,0 L6,3 L0,6 z" fill="#5B61F0" />
      </marker>
    </defs>
    <path
      d="M 200 80 L 240 80"
      stroke="#5B61F0"
      strokeWidth="1.5"
      markerEnd="url(#arr)"
      className="draw"
      pathLength={1}
      style={{ animationDelay: "0.4s" }}
    />
    <g transform="translate(232 60)">
      <rect width="40" height="40" rx="10" fill="#5B61F0" />
      <path
        d="M 8 20 Q 8 12 16 12 Q 24 12 24 20 Q 32 20 32 28 Q 32 36 24 36 L 12 36 Q 4 36 4 28 Q 4 22 8 20 Z"
        fill="white"
        fillOpacity="0.95"
      />
      <circle cx="14" cy="22" r="1.5" fill="#5B61F0" />
      <circle cx="22" cy="22" r="1.5" fill="#5B61F0" />
    </g>
    <text x="252" y="125" textAnchor="middle" fontSize="9" fill="#4A4F66" fontFamily="JetBrains Mono">
      tu catálogo
    </text>
  </svg>
);

const StepGfx3 = () => (
  <svg viewBox="0 0 280 180" className="w-full h-full">
    <line x1="20" y1="90" x2="260" y2="90" stroke="#EAEEFF" strokeWidth="2" />
    <line
      x1="20"
      y1="90"
      x2="260"
      y2="90"
      stroke="#5B61F0"
      strokeWidth="2"
      className="draw"
      pathLength={1}
    />
    {[
      { x: 40, l: "Lead", sub: "00:00" },
      { x: 110, l: "Responde", sub: "00:15", hl: true },
      { x: 180, l: "Califica", sub: "01:20" },
      { x: 250, l: "Visita ✓", sub: "02:00", hl: true },
    ].map((e, i) => (
      <g key={i}>
        <circle cx={e.x} cy="90" r={e.hl ? 6 : 4} fill={e.hl ? "#FF7A59" : "#5B61F0"} />
        {e.hl && <circle cx={e.x} cy="90" r="10" fill="#FF7A59" fillOpacity="0.2" className="animate-pulse" />}
        <text x={e.x} y="70" textAnchor="middle" fontSize="11" fill="#0B0D17" fontWeight="600">
          {e.l}
        </text>
        <text x={e.x} y="115" textAnchor="middle" fontSize="9" fill="#4A4F66" fontFamily="JetBrains Mono">
          {e.sub}
        </text>
      </g>
    ))}
  </svg>
);

export function HowItWorks() {
  return (
    <Section
      id="como-funciona"
      className="py-24 md:py-32 bg-gradient-to-b from-paper via-selai-50/40 to-paper"
    >
      <Container>
        <Reveal>
          <EyebrowH2
            eyebrow="Cómo funciona"
            title="Tres pasos. Sin técnicos, sin meses de implementación."
          />
        </Reveal>
        <div className="mt-14 grid md:grid-cols-3 gap-5">
          <StepCard
            n="01"
            time="el primer día"
            title="Activas tus canales"
            body="Te guiamos paso a paso dentro del panel, botón a botón: voz, número de teléfono, WhatsApp, portales, catálogo y chat de tu web. Cada paso con su tiempo real."
          >
            <StepGfx1 />
          </StepCard>
          <StepCard
            n="02"
            time="tú decides"
            title="selai habla como tu agencia"
            body="Eliges el tono (cercano o formal), marcas las frases que nunca debe decir y subes tu catálogo. Con eso responde como lo haría tu equipo — y lo ajustas cuando quieras desde el panel."
            delay={0.1}
          >
            <StepGfx2 />
          </StepCard>
          <StepCard
            n="03"
            time="desde el primer día"
            title="Atiende cada lead"
            body="Responde, califica, agenda visitas y pasa el cliente al humano con todo el contexto. 24/7, sin festivos."
            delay={0.2}
          >
            <StepGfx3 />
          </StepCard>
        </div>
        <Reveal delay={0.25}>
          <p className="mt-8 text-[14px] text-ink-500">
            <span className="font-mono text-selai-600">Operativa el primer día.</span> WhatsApp al
            100% cuando Meta verifique tu empresa (1-2 días). Sin técnicos y sin coste de
            instalación.
          </p>
          <div className="mt-5">
            <SignupCTA origen="como_funciona" variant="primary" size="md">
              Empezar la prueba gratis
              <IArrow size={14} />
            </SignupCTA>
          </div>
        </Reveal>
      </Container>
    </Section>
  );
}

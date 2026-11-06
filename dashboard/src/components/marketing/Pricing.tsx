"use client";

import { useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

import { SignupCTA } from "./analytics";
import { Reveal } from "./animated";
import { BillingFAQ } from "./BillingFAQ";
import { BOOK_HREF, HAS_CALCOM } from "./booking";
import { IArrow, ICheck } from "./icons";
import { PlanPicker } from "./PlanPicker";
import { annualMonthly, annualSavings, annualTotal, fmtEur } from "./pricing-data";
import { PricingTable } from "./PricingTable";
import { TrustBlock } from "./TrustBlock";
import { Container, EyebrowH2, Pill, Section } from "./ui";

// 3D mouse-tilt wrapper. Pure CSS transform driven by pointer position;
// disables itself on touch devices and respects reduced-motion via the
// global media-query in globals.css.
function TiltCard({
  children,
  intensity = 8,
  className = "",
}: {
  children: ReactNode;
  intensity?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [tilt, setTilt] = useState({ x: 0, y: 0, lift: 0 });

  const handleMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== "mouse") return;
    // Respect reduced motion: the global CSS kills transitions but not the
    // JS-driven transform itself, so skip the tilt entirely.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width;
    const py = (e.clientY - rect.top) / rect.height;
    setTilt({ x: (px - 0.5) * intensity * 2, y: (py - 0.5) * -intensity * 2, lift: 6 });
  };

  const reset = () => setTilt({ x: 0, y: 0, lift: 0 });

  const style: CSSProperties = {
    transform: `perspective(1100px) rotateY(${tilt.x}deg) rotateX(${tilt.y}deg) translateZ(${tilt.lift}px)`,
    transition: "transform 0.25s cubic-bezier(0.16,1,0.3,1)",
    transformStyle: "preserve-3d",
    willChange: "transform",
  };

  return (
    <div
      ref={ref}
      onPointerMove={handleMove}
      onPointerLeave={reset}
      onPointerDown={reset}
      style={style}
      className={className}
    >
      {children}
    </div>
  );
}

type Tier = {
  // Must match the billing plan codes (backend seed_plans.py).
  id: "starter" | "pro" | "agencia";
  name: string;
  price: number | null;
  desc: string;
  summary: string;
  features: string[];
  cta: string;
  recommended?: boolean;
};

// NB: names, prices and limits mirror the real billing plans (backend
// seed_plans.py + Stripe). If one changes, change both.
const TIERS: ReadonlyArray<Tier> = [
  {
    id: "starter",
    name: "Starter",
    price: 99,
    desc: "Para agencias pequeñas que quieren dejar de perder leads de noche.",
    summary: "Responde por escrito (WhatsApp, email y chat de tu web) las 24 horas.",
    features: [
      "WhatsApp + email + chat en tu web",
      "Hasta 2 personas de tu equipo · 100 propiedades",
      "Recuerda a cada cliente aunque cambie de canal",
      "Recoge los contactos de Idealista, Fotocasa y Habitaclia",
      "Te pasa la conversación cuando el cliente quiere hablar contigo",
      "Todas las conversaciones en una sola bandeja",
      "Soporte por email",
    ],
    cta: "Empezar 14 días gratis",
  },
  {
    id: "pro",
    name: "Pro",
    price: 199,
    desc: "El más elegido: todos los canales, incluidas las llamadas, para equipos que no quieren dejar consultas sin atender.",
    summary: "Todo lo del Starter y además contesta el teléfono con voz natural.",
    features: [
      "Todos los canales: WhatsApp, email, SMS, llamadas y web",
      "600 minutos de llamadas al mes incluidos",
      "Hasta 5 personas de tu equipo · 500 propiedades",
      "Plantillas de mensajes a tu medida (recordatorios de visita, seguimientos…)",
      "Tus leads en tu CRM: export CSV y avisos automáticos (nativo Inmovilla/Witei en camino)",
      "Atiende en 5 idiomas (español, catalán, inglés, francés y alemán)",
      "Ofertas en PDF listas para firmar",
      "Te acompañamos en la puesta en marcha, en videollamada",
    ],
    cta: "Empezar 14 días gratis",
    recommended: true,
  },
  {
    id: "agencia",
    name: "Agencia",
    price: 399,
    desc: "Para grupos y franquicias: volumen alto, varias oficinas y soporte prioritario.",
    summary: "Todo lo del Pro, pensado para varias oficinas y mucho volumen.",
    features: [
      "Todo lo del plan Pro",
      "Hasta 15 personas de tu equipo · 2.000 propiedades",
      "1.500 minutos de llamadas al mes",
      "Clona la voz de tu mejor comercial",
      "Cada contacto va solo a la oficina que le corresponde",
      "Comparte contactos entre tus oficinas",
      "Compárate con otras agencias del sector (datos anónimos)",
      "Soporte prioritario",
    ],
    cta: "Empezar 14 días gratis",
  },
];

export function Pricing() {
  // Mensual por defecto (n36): el hero, la tabla y el FAQ hablan de 99/199/399
  // €/mes. Arrancar en Anual enseñaba 79,20/159,20/319,20 como primer precio
  // grande y el visitante veía dos verdades distintas en la misma página.
  const [annual, setAnnual] = useState(false);
  return (
    <Section id="precios" className="py-24 md:py-32">
      <Container>
        <Reveal>
          <EyebrowH2
            center
            eyebrow="Precios"
            title="Empieza por 99 €. Cambia cuando quieras."
            sub="Sin permanencia, sin setup, sin sorpresas. Cancela en 1 clic."
          />
        </Reveal>
        <Reveal delay={0.05}>
          <div className="mt-8 flex items-center justify-center gap-3">
            <span className={`text-[13px] font-medium ${!annual ? "text-ink-900" : "text-ink-500"}`}>
              Mensual
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={annual}
              onClick={() => setAnnual(!annual)}
              className="relative w-12 h-11 flex-shrink-0 rounded-xl"
              aria-label="Cambiar entre facturación mensual y anual"
            >
              <span
                aria-hidden="true"
                className={`absolute inset-x-0 top-1/2 h-6 -translate-y-1/2 rounded-full transition ${
                  annual ? "bg-selai-500" : "bg-ink-900/20"
                }`}
              >
                <span
                  className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${
                    annual ? "left-[26px]" : "left-0.5"
                  }`}
                />
              </span>
            </button>
            <span className={`text-[13px] font-medium ${annual ? "text-ink-900" : "text-ink-500"}`}>
              Anual
            </span>
            <Pill tone="accent" className="ml-1">
              −20%
            </Pill>
          </div>
        </Reveal>
        <Reveal delay={0.08}>
          <div className="mt-8 rounded-2xl bg-selai-100 ring-1 ring-selai-200 px-5 py-3.5 flex flex-wrap items-center justify-center gap-x-2.5 gap-y-1 text-center">
            {[
              "14 días gratis",
              "Sin tarjeta",
              "Sin coste de instalación",
              "Cancela cuando quieras",
            ].map((t, i) => (
              <span key={t} className="flex items-center gap-2.5 text-[14px] font-medium text-selai-700">
                {i > 0 && <span className="text-selai-700/40" aria-hidden>·</span>}
                {t}
              </span>
            ))}
          </div>
        </Reveal>
        <div className="mt-10 grid md:grid-cols-3 gap-5">
          {TIERS.map((t, i) => (
            <Reveal key={t.id} delay={i * 0.08}>
              <TiltCard
                className={`relative rounded-3xl p-7 md:p-8 h-full flex flex-col ${
                  t.recommended
                    ? "bg-ink-900 text-selai-50 ring-2 ring-selai-500 shadow-glow"
                    : "bg-white ring-1 ring-ink-900/5 shadow-card hover:shadow-soft"
                }`}
              >
                {t.recommended && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                    <Pill tone="accent">Recomendado</Pill>
                  </div>
                )}
                <div className="font-display font-bold text-[24px] mb-1">{t.name}</div>
                <div className={`text-[14px] mb-5 ${t.recommended ? "text-selai-100/70" : "text-ink-500"}`}>
                  {t.desc}
                </div>
                <div className="mb-2">
                  <div className="flex items-baseline gap-1.5">
                    {t.price ? (
                      <>
                        <span className="font-display font-bold text-[52px] leading-none">
                          {annual ? fmtEur(annualMonthly(t.price)) : t.price}
                        </span>
                        <span className={`text-[15px] ${t.recommended ? "text-selai-100/70" : "text-ink-500"}`}>
                          €/mes
                        </span>
                      </>
                    ) : (
                      <span className="font-display font-bold text-[40px] leading-none">A medida</span>
                    )}
                  </div>
                  {t.price && annual && (
                    <div
                      className={`mt-1.5 text-[13px] ${
                        t.recommended ? "text-selai-100/80" : "text-ink-500"
                      }`}
                    >
                      {fmtEur(annualMonthly(t.price))} €/mes · facturados{" "}
                      {fmtEur(annualTotal(t.price))} €/año
                      <span
                        className={`block font-medium ${
                          t.recommended ? "text-emerald-300" : "text-emerald-600"
                        }`}
                      >
                        Ahorras {fmtEur(annualSavings(t.price))} €/año
                      </span>
                    </div>
                  )}
                  <div
                    className={`mt-1.5 text-[13px] font-medium ${
                      t.recommended ? "text-selai-100/80" : "text-ink-500"
                    }`}
                  >
                    precio final · sin permanencia
                  </div>
                </div>
                <p
                  className={`mb-5 text-[14px] leading-relaxed ${
                    t.recommended ? "text-selai-100/90" : "text-ink-700"
                  }`}
                >
                  {t.summary}
                </p>
                <ul className="space-y-2.5 flex-1 mb-6">
                  {t.features.map((f) => (
                    <li
                      key={f}
                      className={`flex items-start gap-3 text-[14px] ${
                        t.recommended ? "text-selai-100/90" : "text-ink-700"
                      }`}
                    >
                      <div
                        className={`mt-0.5 w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0 ${
                          t.recommended ? "bg-selai-500/30 text-selai-100" : "bg-selai-100 text-selai-600"
                        }`}
                      >
                        <ICheck size={11} />
                      </div>
                      {f}
                    </li>
                  ))}
                </ul>
                <SignupCTA
                  origen={`precios_${t.id}`}
                  plan={t.id}
                  interval={annual ? "year" : "month"}
                  variant={t.recommended ? "primary" : "ghost"}
                  size="md"
                  className="w-full"
                >
                  {t.cta}
                  <IArrow size={14} />
                </SignupCTA>
              </TiltCard>
            </Reveal>
          ))}
        </div>
        <Reveal delay={0.25}>
          <div className="mt-8 text-center">
            <p className="text-[14px] font-medium text-ink-900">
              Al acabar los 14 días eliges plan o tu cuenta se pausa. Nunca se cobra solo.
            </p>
            <div className="mt-4">
              <TrustBlock />
            </div>
          </div>
        </Reveal>

        {/* Comparativa detallada (colapsable) */}
        <PricingTable />

        {/* Mini-selector de plan */}
        <Reveal delay={0.05}>
          <div className="mt-14">
            <PlanPicker />
          </div>
        </Reveal>

        {/* FAQ de facturación */}
        <Reveal delay={0.05}>
          <div className="mt-16">
            <BillingFAQ />
          </div>
        </Reveal>

        <Reveal delay={0.3}>
          <div className="mt-10 text-center space-y-2">
            <p className="text-[14px] text-ink-700">
              Puesta en marcha guiada desde el panel desde el primer día. Sin técnicos ni obra;
              WhatsApp queda operativo cuando Meta verifica tu empresa, normalmente en 1-2 días.
            </p>
            <p className="text-[13px] text-ink-500 font-mono">
              Precio final · Sin permanencia · Cancela cuando quieras desde Facturación · RGPD ·
              Datos en la UE
            </p>
            {/* (n20) Sin promesa de pago por exceso: no existe ese cobro. Lo
                que existe de verdad es el aviso al 80% y la ampliación de
                plan con prorrateo. */}
            <p className="text-[12px] text-ink-500">
              Sin extras ocultos: si te acercas al límite de tu plan te avisamos (al 80%) y
              puedes ampliarlo en un clic, pagando solo la diferencia.
            </p>
            <p className="text-[13px] text-ink-500">
              ¿Varias oficinas, condiciones especiales o una integración a medida?{" "}
              <a
                href={BOOK_HREF}
                {...(HAS_CALCOM ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                className="underline text-selai-600"
              >
                Hablemos
              </a>
            </p>
          </div>
        </Reveal>
      </Container>
    </Section>
  );
}

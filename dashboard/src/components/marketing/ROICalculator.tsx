"use client";

// Break-even calculator. It uses only customer inputs and the real annual
// price; it deliberately makes no claim about leads recovered or sales won.
import { useMemo, useState } from "react";

import { Reveal } from "./animated";
import { ITrend } from "./icons";
import { annualTotal, PLAN_PRICES } from "./pricing-data";
import { Container, EyebrowH2, Pill, Section } from "./ui";

const SELAI_ANNUAL_COST = annualTotal(PLAN_PRICES.pro);

function fmtEur(n: number): string {
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(n);
}

function fmtEurPrecise(n: number): string {
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

function fmtNumber(n: number): string {
  return new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 }).format(n);
}

type Slider = {
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
  value: number;
  setValue: (v: number) => void;
  format: (v: number) => string;
};

function Range({ s }: { s: Slider }) {
  const pct = ((s.value - s.min) / (s.max - s.min)) * 100;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <label className="text-[14px] font-medium text-ink-900">{s.label}</label>
        <span className="font-mono text-[16px] font-semibold text-selai-600 tabular">
          {s.format(s.value)}
        </span>
      </div>
      <input
        type="range"
        min={s.min}
        max={s.max}
        step={s.step}
        value={s.value}
        onChange={(e) => s.setValue(Number(e.target.value))}
        className="w-full h-11 rounded-full appearance-none cursor-pointer accent-selai-500"
        style={{
          background: `linear-gradient(to right, #5B61F0 0%, #5B61F0 ${pct}%, rgba(11,13,23,0.08) ${pct}%, rgba(11,13,23,0.08) 100%)`,
          backgroundPosition: "center",
          backgroundRepeat: "no-repeat",
          backgroundSize: "100% 0.5rem",
        }}
        aria-label={s.label}
      />
      <div className="text-[11px] text-ink-500 mt-1.5">{s.hint}</div>
    </div>
  );
}

export function ROICalculator() {
  const [leads, setLeads] = useState(250);
  const [commission, setCommission] = useState(7000);

  const result = useMemo(() => {
    const annualLeads = leads * 12;
    const salesToBreakEven = SELAI_ANNUAL_COST / commission;
    const costPerLead = SELAI_ANNUAL_COST / annualLeads;
    const yearsPerSale = commission / SELAI_ANNUAL_COST;
    return {
      annualLeads,
      salesToBreakEven,
      costPerLead,
      yearsPerSale,
    };
  }, [leads, commission]);

  const sliders: Slider[] = [
    {
      label: "Leads que recibes al mes",
      hint: "WhatsApp + email + Idealista/Fotocasa + web. Solo los que entran, no los que contestas.",
      min: 50,
      max: 1000,
      step: 10,
      value: leads,
      setValue: setLeads,
      format: (v) => `${fmtNumber(v)} / mes`,
    },
    {
      label: "Comisión media por venta cerrada",
      hint: "La comisión neta que usarías para medir si la inversión se compensa.",
      min: 2000,
      max: 25000,
      step: 500,
      value: commission,
      setValue: setCommission,
      format: (v) => fmtEur(v),
    },
  ];

  return (
    <Section
      id="calculadora-roi"
      className="py-24 md:py-32 bg-gradient-to-b from-paper via-selai-50/40 to-paper"
    >
      <Container>
        <Reveal>
          <EyebrowH2
            center
            eyebrow="Punto de equilibrio"
            title="¿Qué tendría que aportar selai para compensar su coste?"
            sub="Introduce tus cifras. La cuenta muestra el umbral económico del plan Pro anual, sin asumir conversiones ni prometer ventas."
          />
        </Reveal>
        <Reveal delay={0.1}>
          <div className="mt-12 grid lg:grid-cols-[1fr_1fr] gap-6 max-w-5xl mx-auto">
            {/* Sliders */}
            <div className="bg-white rounded-3xl p-7 md:p-9 ring-1 ring-ink-900/5 shadow-card">
              <div className="text-[12px] uppercase tracking-[0.18em] text-ink-500 font-mono mb-6">
                Tus datos
              </div>
              <div className="space-y-7">
                {sliders.map((s) => (
                  <Range key={s.label} s={s} />
                ))}
              </div>
            </div>

            {/* Results */}
            <div className="relative bg-ink-900 grain rounded-3xl p-7 md:p-9 text-selai-50 overflow-hidden">
              <div className="absolute -top-20 -right-20 w-72 h-72 rounded-full bg-selai-500/30 blur-3xl pointer-events-none" />
              <div className="absolute bottom-0 left-0 w-56 h-56 rounded-full bg-accent/20 blur-3xl pointer-events-none" />
              <div className="relative">
                <div className="flex items-center gap-3 mb-6">
                  <Pill tone="dark">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    Punto de equilibrio
                  </Pill>
                </div>

                <div className="space-y-6">
                  <div>
                    <div className="text-[12px] text-selai-100/60 font-mono uppercase tracking-wider mb-1.5">
                      Coste por lead recibido
                    </div>
                    <div className="font-display font-bold text-[40px] md:text-[52px] leading-none text-selai-50 tabular">
                      {fmtEurPrecise(result.costPerLead)}
                    </div>
                  </div>

                  <div className="h-px bg-white/10" />

                  <div>
                    <div className="text-[12px] text-selai-100/60 font-mono uppercase tracking-wider mb-1.5">
                      Ventas adicionales para cubrir el año
                    </div>
                    <div className="font-display font-bold text-[44px] md:text-[64px] leading-none text-accent tabular">
                      {result.salesToBreakEven.toLocaleString("es-ES", {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </div>
                    <div className="mt-2 text-[13px] text-selai-100/70">
                      con {fmtNumber(result.annualLeads)} leads al año y una comisión de{" "}
                      {fmtEur(commission)}
                    </div>
                  </div>

                  <div className="h-px bg-white/10" />

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-[11px] text-selai-100/60 font-mono uppercase tracking-wider mb-1">
                        selai te cuesta
                      </div>
                      <div className="font-mono font-semibold text-[18px] text-selai-100">
                        {fmtEurPrecise(SELAI_ANNUAL_COST)}/año
                      </div>
                      <div className="text-[10px] text-selai-100/50 font-mono mt-0.5">
                        plan Pro con pago anual
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-selai-100/60 font-mono uppercase tracking-wider mb-1">
                        Comisión usada
                      </div>
                      <div className="font-display font-bold text-[28px] text-emerald-300 leading-none tabular">
                        {fmtEur(commission)}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="mt-6 rounded-2xl bg-white/5 ring-1 ring-white/10 px-4 py-3">
                  <p className="text-[14px] text-selai-100/90 leading-relaxed">
                    Una venta adicional con una comisión de {fmtEur(commission)} equivaldría a{" "}
                    <span className="font-semibold text-emerald-300 tabular">
                      {result.yearsPerSale >= 1
                        ? `${result.yearsPerSale.toLocaleString("es-ES", { maximumFractionDigits: 1 })} años`
                        : `${Math.round(result.yearsPerSale * 12)} meses`}
                    </span>{" "}
                    de suscripción anual al precio actual. Esta equivalencia no afirma que selai
                    vaya a generar esa venta.
                  </p>
                </div>

                <div className="mt-5 flex items-start gap-2 text-[11px] text-selai-100/60 leading-relaxed">
                  <ITrend size={14} className="text-emerald-300 mt-0.5 flex-shrink-0" />
                  <p>
                    Cálculo: coste anual del plan dividido por tu comisión media. El coste por lead
                    divide ese mismo precio entre tus leads anuales. No se aplican porcentajes de
                    conversión externos ni se estiman ingresos futuros.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </Reveal>
      </Container>
    </Section>
  );
}

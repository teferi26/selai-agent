"use client";

// "¿Qué plan me toca?" — two-question mini selector (backlog #18).
// Pure client state, no persistence: answer two questions with buttons and
// get a one-line recommendation + CTA.
import { useEffect, useRef, useState } from "react";

import { SignupCTA, trackFunnel } from "./analytics";
import { IArrow } from "./icons";
import { annualMonthly, fmtEur, PLAN_PRICES } from "./pricing-data";
import type { PlanId } from "./pricing-data";

type TeamSize = "1-2" | "3-5" | "6+";

function recommend(team: TeamSize, phone: boolean): { plan: PlanId; name: string; why: string } {
  if (team === "6+") {
    return {
      plan: "agencia",
      name: "Agencia",
      why: "con 6 o más personas necesitáis los 15 puestos, los 1.500 minutos de llamada y el reparto por oficinas del plan Agencia.",
    };
  }
  if (team === "3-5") {
    return {
      plan: "pro",
      name: "Pro",
      why: phone
        ? "cubre hasta 5 personas y además contesta el teléfono con voz natural (600 minutos al mes incluidos)."
        : "cubre hasta 5 personas con todos los canales; el teléfono queda incluido por si cambiáis de idea.",
    };
  }
  if (phone) {
    return {
      plan: "pro",
      name: "Pro",
      why: "es el único plan (junto a Agencia) que contesta también el teléfono, con 600 minutos al mes incluidos.",
    };
  }
  return {
    plan: "starter",
    name: "Starter",
    why: "para 1-2 personas que quieren cubrir WhatsApp, email y el chat de la web sin pagar por el teléfono.",
  };
}

function OptionBtn({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`min-h-11 px-4 py-2.5 rounded-xl text-[14px] font-medium ring-1 transition ${
        active
          ? "bg-selai-500 text-white ring-selai-500"
          : "bg-white text-ink-700 ring-ink-900/10 hover:bg-selai-50 hover:ring-selai-200"
      }`}
    >
      {label}
    </button>
  );
}

export function PlanPicker() {
  const [team, setTeam] = useState<TeamSize | null>(null);
  const [phone, setPhone] = useState<boolean | null>(null);

  const rec = team !== null && phone !== null ? recommend(team, phone) : null;

  // Embudo (backlog #156): el selector no tiene botón de enviar — "enviarlo"
  // es contestar las dos preguntas. Emitimos el evento cada vez que sale una
  // recomendación nueva, no en cada render.
  const lastTracked = useRef<string | null>(null);
  useEffect(() => {
    if (team === null || phone === null) return;
    const key = `${team}|${phone}`;
    if (lastTracked.current === key) return;
    lastTracked.current = key;
    trackFunnel("plan_picker_submit", {
      plan: recommend(team, phone).plan,
      equipo: team,
      telefono: phone,
    });
  }, [team, phone]);

  return (
    <div className="mx-auto max-w-[640px] rounded-3xl bg-white ring-1 ring-ink-900/5 shadow-card p-7 md:p-8">
      <div className="text-[12px] uppercase tracking-[0.18em] text-ink-500 font-mono mb-1.5">
        ¿Qué plan me toca?
      </div>
      <p className="text-[14px] text-ink-500 mb-6">
        Dos preguntas y te lo decimos. Sin email, sin trucos.
      </p>

      <div className="space-y-5">
        <div>
          <div className="text-[14px] font-medium text-ink-900 mb-2.5">
            1 · ¿Cuántas personas sois en la agencia?
          </div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Tamaño del equipo">
            {(["1-2", "3-5", "6+"] as const).map((t) => (
              <OptionBtn key={t} label={t} active={team === t} onClick={() => setTeam(t)} />
            ))}
          </div>
        </div>
        <div>
          <div className="text-[14px] font-medium text-ink-900 mb-2.5">
            2 · ¿Quieres que conteste también el teléfono?
          </div>
          <div
            className="flex flex-wrap gap-2"
            role="group"
            aria-label="Atención telefónica con voz"
          >
            <OptionBtn label="Sí" active={phone === true} onClick={() => setPhone(true)} />
            <OptionBtn label="No, solo escrito" active={phone === false} onClick={() => setPhone(false)} />
          </div>
        </div>
      </div>

      <div
        className="grid transition-all duration-500"
        style={{ gridTemplateRows: rec ? "1fr" : "0fr" }}
        aria-live="polite"
      >
        <div className="overflow-hidden">
          {rec && (
            <div className="mt-6 rounded-2xl bg-selai-50 ring-1 ring-selai-200 p-5">
              <p className="text-[15px] text-ink-900 leading-relaxed">
                Te toca el plan <span className="font-bold">{rec.name}</span> (
                {PLAN_PRICES[rec.plan]} €/mes, o {fmtEur(annualMonthly(PLAN_PRICES[rec.plan]))}{" "}
                €/mes si pagas al año): {rec.why}
              </p>
              <div className="mt-4">
                <SignupCTA
                  origen={`plan_picker_${rec.plan}`}
                  plan={rec.plan}
                  interval="month"
                  variant="primary"
                  size="md"
                >
                  Probar {rec.name} gratis 14 días
                  <IArrow size={14} />
                </SignupCTA>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

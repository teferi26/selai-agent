"use client";

import { useState } from "react";
import type { ComponentType } from "react";

import { Reveal } from "./animated";
import { ChannelCard } from "./ChannelCard";
import { IChat, ICheck, IHouse, IMail, IPhone, IWhats } from "./icons";
import { Container, EyebrowH2, Section } from "./ui";

type IconCmp = ComponentType<{ size?: number; className?: string }>;

type TabId = "whatsapp" | "email" | "voice" | "web" | "idealista";

const TABS: ReadonlyArray<{ id: TabId; label: string; icon: IconCmp; color: string }> = [
  { id: "whatsapp", label: "WhatsApp", icon: IWhats, color: "text-emerald-500" },
  { id: "email", label: "Email", icon: IMail, color: "text-blue-500" },
  { id: "voice", label: "Voz", icon: IPhone, color: "text-violet-500" },
  { id: "web", label: "Web chat", icon: IChat, color: "text-accent" },
  { id: "idealista", label: "Idealista", icon: IHouse, color: "text-selai-500" },
];

const CONTENT: Record<TabId, { title: string; body: string; bullets: string[] }> = {
  whatsapp: {
    title: "El canal donde tus clientes ya escriben.",
    body: "La conexión oficial de WhatsApp para empresas (Meta). selai responde al momento con naturalidad, envía la ficha y las fotos del piso, agenda visitas y te deja el lead cualificado en tu bandeja.",
    bullets: ["Conexión oficial de Meta", "Plantillas listas para usar", "Fichas y fotos de tus pisos"],
  },
  email: {
    title: "Cero leads perdidos en spam.",
    body: "Tu agencia tiene su propia dirección de email en selai: reenvíale los avisos y consultas, y selai responde con el tono de tu mejor comercial y te lo deja todo en la bandeja.",
    bullets: ["Dirección propia de tu agencia", "Detecta intención y urgencia", "Respuesta en segundos, 24/7"],
  },
  voice: {
    title: "Atiende llamadas a las 3 a.m.",
    body: "Número propio para tu IA, o desvías el tuyo. selai contesta, entiende, califica y, si hace falta una persona, avisa a tu equipo y deja el resumen del lead en tu bandeja.",
    bullets: ["Voz natural en español y más idiomas", "Contesta al momento, 24/7", "Resumen del lead en tu bandeja"],
  },
  web: {
    title: "Cada visita web, una conversación.",
    body: "Pegas una línea de código (te la damos con botón de copiar) y tu web tiene un chat atendido por tu asistente: responde dudas, propone pisos de tu catálogo y captura el contacto sin formularios.",
    bullets: ["1 línea de código", "Conoce tu catálogo entero", "Captura el contacto sin formularios"],
  },
  idealista: {
    title: "Responde a Idealista sin abrir Idealista.",
    body: "Reenvías los avisos de leads del portal a la dirección única de tu agencia (5 minutos, desde el panel) y cada contacto entra en selai al instante, ya atendido por la IA.",
    bullets: ["Idealista, Fotocasa, Habitaclia", "Reenvío automático, al instante", "Export CSV y avisos a tu CRM"],
  },
};

export function ChannelsTabs() {
  const [active, setActive] = useState<TabId>("whatsapp");
  const c = CONTENT[active];
  const cardKind = active === "idealista" ? "web" : active;
  return (
    <Section className="py-24 md:py-32 bg-ink-900 grain text-selai-50">
      <Container>
        <Reveal>
          <EyebrowH2
            dark
            eyebrow="Canales"
            title="Un solo cerebro. Cinco bocas."
            sub="Cada canal con sus reglas y su tono. Y todo en una sola bandeja para tu equipo."
          />
        </Reveal>
        <div className="mt-12">
          <div className="flex gap-2 overflow-x-auto no-scrollbar pb-2 -mx-2 px-2">
            {TABS.map((t) => {
              const I = t.icon;
              const on = active === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => setActive(t.id)}
                  className={`min-h-11 flex-shrink-0 inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-[14px] font-medium transition ${
                    on
                      ? "bg-selai-500 text-white shadow-soft"
                      : "bg-white/5 text-selai-100/70 hover:bg-white/10 ring-1 ring-white/10"
                  }`}
                >
                  <I size={16} className={on ? "text-white" : t.color} />
                  {t.label}
                </button>
              );
            })}
          </div>
          <div
            key={active}
            className="mt-6 grid lg:grid-cols-[0.95fr_1.05fr] gap-8 bg-white/5 ring-1 ring-white/10 rounded-3xl p-7 md:p-10 animate-rise"
          >
            <div>
              <h3
                className="font-display font-bold text-[32px] md:text-[40px] leading-tight text-selai-50"
                style={{ textWrap: "balance" } as React.CSSProperties}
              >
                {c.title}
              </h3>
              <p className="mt-4 text-[16px] text-selai-100/75 leading-relaxed">{c.body}</p>
              <ul className="mt-6 space-y-2">
                {c.bullets.map((b) => (
                  <li key={b} className="flex items-center gap-3 text-[14px] text-selai-100/90">
                    <div className="w-5 h-5 rounded-full bg-selai-500/30 flex items-center justify-center">
                      <ICheck size={12} />
                    </div>
                    {b}
                  </li>
                ))}
              </ul>
            </div>
            <div className="bg-ink-800/60 rounded-2xl p-6 flex items-center justify-center min-h-[280px] ring-1 ring-white/5">
              <ChannelCard kind={cardKind} />
            </div>
          </div>
        </div>
      </Container>
    </Section>
  );
}

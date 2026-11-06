import type { Metadata } from "next";

import { Pricing } from "@/components/marketing/Pricing";
import {
  annualMonthly,
  annualTotal,
  BILLING_FAQ,
  fmtEur,
  PLAN_PRICES,
} from "@/components/marketing/pricing-data";
import { Container, Pill, Section } from "@/components/marketing/ui";

// NB: al publicar esta página hay que añadir "/precios" (junto a /seguridad
// y /ayuda) a las rutas públicas del proxy (isPublic). Lo hace el
// orquestador — no tocar proxy.ts desde aquí.

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://selai.es";

export const metadata: Metadata = {
  title: "Precios — desde 99 €/mes, sin permanencia",
  description:
    "Precios de selai: Starter 99 €, Pro 199 € y Agencia 399 €/mes (20% de descuento si pagas al año). 14 días gratis sin tarjeta, sin permanencia y puesta en marcha guiada.",
  alternates: { canonical: "/precios" },
  openGraph: {
    type: "website",
    locale: "es_ES",
    url: `${SITE_URL}/precios`,
    siteName: "selai",
    title: "Precios · selai — desde 99 €/mes, sin permanencia",
    description:
      "Starter 99 €, Pro 199 € y Agencia 399 €/mes. 14 días gratis sin tarjeta, sin permanencia y puesta en marcha guiada.",
  },
};

// JSON-LD: Product con las 3 ofertas reales + FAQPage con las preguntas de
// facturación que se muestran en esta página (backlog #125/#129).
function jsonLd() {
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Product",
        "@id": `${SITE_URL}/precios#product`,
        name: "selai — asistente de IA para inmobiliarias",
        description:
          "Asistente de IA que atiende WhatsApp, email, web y teléfono de tu inmobiliaria 24/7. Tres planes sin permanencia con 14 días de prueba gratis.",
        brand: { "@type": "Brand", name: "selai" },
        offers: (
          [
            ["Starter", PLAN_PRICES.starter],
            ["Pro", PLAN_PRICES.pro],
            ["Agencia", PLAN_PRICES.agencia],
          ] as const
        ).map(([name, price]) => ({
          "@type": "Offer",
          name: `Plan ${name}`,
          price: String(price),
          priceCurrency: "EUR",
          url: `${SITE_URL}/precios`,
          availability: "https://schema.org/InStock",
          description: `${price} €/mes con facturación mensual, o ${fmtEur(annualMonthly(price))} €/mes (${fmtEur(annualTotal(price))} €/año) con facturación anual. Sin permanencia.`,
        })),
      },
      {
        "@type": "FAQPage",
        mainEntity: BILLING_FAQ.map(([q, a]) => ({
          "@type": "Question",
          name: q,
          acceptedAnswer: { "@type": "Answer", text: a },
        })),
      },
    ],
  };
}

export default function PreciosPage() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd()) }}
      />
      <Section className="pt-32 md:pt-40 pb-0">
        <Container>
          <div className="max-w-3xl mx-auto text-center">
            <Pill tone="selai" className="mb-5">
              Precios
            </Pill>
            <h1
              className="font-display font-bold text-[44px] md:text-[64px] leading-[1.04] text-ink-900"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              Desde 99 €/mes. Sin permanencia.
            </h1>
            <p
              className="mt-6 text-[17px] text-ink-500 leading-relaxed"
              style={{ textWrap: "pretty" } as React.CSSProperties}
            >
              Tres planes claros, con 14 días de prueba gratis sin tarjeta y puesta en marcha
              guiada desde el primer día. WhatsApp queda operativo cuando Meta verifica tu empresa,
              normalmente en 1-2 días. Si pagas al año, un 20% menos. Y si no te encaja, cancelas
              en 1 clic.
            </p>
          </div>
        </Container>
      </Section>

      {/* Parrilla + toggle anual + comparativa + selector + FAQ de facturación */}
      <div className="-mt-16 md:-mt-24">
        <Pricing />
      </div>
    </>
  );
}

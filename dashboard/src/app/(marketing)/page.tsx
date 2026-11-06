import type { Metadata } from "next";

import { AboutUs } from "@/components/marketing/AboutUs";
import { Benefits } from "@/components/marketing/Benefits";
import { ChannelsTabs } from "@/components/marketing/ChannelsTabs";
import { CTABook } from "@/components/marketing/CTABook";
import { FAQ } from "@/components/marketing/FAQ";
import { FAQ_ITEMS } from "@/components/marketing/faq-items";
import { Hero } from "@/components/marketing/Hero";
import { HowItWorks } from "@/components/marketing/HowItWorks";
import { LazyLiveDemoChat, LazyROICalculator } from "@/components/marketing/LazySections";
import { LogoStrip } from "@/components/marketing/LogoStrip";
import { InsideSelai } from "@/components/marketing/PanelMockup";
import { Pricing } from "@/components/marketing/Pricing";
import {
  annualMonthly,
  annualTotal,
  fmtEur,
  PLAN_PRICES,
} from "@/components/marketing/pricing-data";
import { ProblemSolution } from "@/components/marketing/ProblemSolution";
import { SetupSteps } from "@/components/marketing/SetupSteps";
import { Testimonial } from "@/components/marketing/Testimonial";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://selai.es";

export const metadata: Metadata = {
  title: "Atiende cada lead en 15 segundos · IA para inmobiliarias",
  description:
    "selai responde a los leads de tu inmobiliaria por WhatsApp, email, web y teléfono. Desde 99 €/mes, puesta en marcha guiada y 14 días gratis sin tarjeta. Hecho en Alicante, RGPD.",
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    locale: "es_ES",
    url: SITE_URL,
    siteName: "selai",
    title: "selai — Atiende cada lead en 15 segundos",
    description:
      "Asistente de IA conversacional para inmobiliarias en España. WhatsApp, email, web y teléfono. 24/7, con tu catálogo, tu tono y puesta en marcha guiada.",
  },
  twitter: {
    card: "summary_large_image",
    title: "selai — Atiende cada lead en 15 segundos",
    description:
      "Asistente de IA 24/7 para inmobiliarias en España. WhatsApp, email, web y teléfono.",
  },
};

// JSON-LD: Organization + SoftwareApplication + FAQPage. Inlined into <head>
// via the page so Google can pick it up at crawl time.
function jsonLd() {
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${SITE_URL}#organization`,
        name: "selai",
        url: SITE_URL,
        logo: `${SITE_URL}/icon-512.png`,
        contactPoint: {
          "@type": "ContactPoint",
          email: "hola@selai.es",
          contactType: "sales",
          areaServed: "ES",
          availableLanguage: ["es", "ca", "en", "fr", "de"],
        },
      },
      {
        "@type": "SoftwareApplication",
        "@id": `${SITE_URL}#software`,
        name: "selai",
        applicationCategory: "BusinessApplication",
        operatingSystem: "Web",
        description:
          "Asistente de IA conversacional para inmobiliarias que atiende WhatsApp, email, web chat y teléfono 24/7.",
        // Ofertas reales: precio mensual + variante anual con 20% de descuento.
        offers: (
          [
            ["Starter", PLAN_PRICES.starter],
            ["Pro", PLAN_PRICES.pro],
            ["Agencia", PLAN_PRICES.agencia],
          ] as const
        ).map(([name, price]) => ({
          "@type": "Offer",
          name,
          price: String(price),
          priceCurrency: "EUR",
          url: `${SITE_URL}/precios`,
          availability: "https://schema.org/InStock",
          priceSpecification: [
            {
              "@type": "UnitPriceSpecification",
              price: String(price),
              priceCurrency: "EUR",
              billingIncrement: 1,
              unitText: "mes (facturación mensual)",
            },
            {
              "@type": "UnitPriceSpecification",
              // Máquina: punto decimal y dos cifras (79.20). Humano: coma.
              price: annualMonthly(price).toFixed(2),
              priceCurrency: "EUR",
              billingIncrement: 1,
              unitText: `mes (facturación anual: ${fmtEur(annualTotal(price))} €/año)`,
            },
          ],
        })),
      },
      {
        "@type": "FAQPage",
        mainEntity: FAQ_ITEMS.map(([q, a]) => ({
          "@type": "Question",
          name: q,
          acceptedAnswer: { "@type": "Answer", text: a },
        })),
      },
    ],
  };
}

export default function MarketingHome() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd()) }}
      />
      <Hero />
      <LogoStrip />
      <ProblemSolution />
      <HowItWorks />
      <InsideSelai />
      <SetupSteps />
      <Benefits />
      <ChannelsTabs />
      <LazyLiveDemoChat />
      <Testimonial />
      <LazyROICalculator />
      <Pricing />
      <FAQ />
      <AboutUs />
      <CTABook />
    </>
  );
}

// Pricing data shared between client components (Pricing, PricingTable,
// PlanPicker) and server-side metadata / JSON-LD on /precios. Kept out of any
// "use client" module so server imports get the real values.
//
// NB: prices mirror the real billing plans (backend seed_plans.py + Stripe).
// If one changes, change both.

export const ANNUAL_DISCOUNT = 0.2;

export type PlanId = "starter" | "pro" | "agencia";

export const PLAN_PRICES: Record<PlanId, number> = {
  starter: 99,
  pro: 199,
  agencia: 399,
};

// La fórmula es EXACTAMENTE la del backend (sync_stripe_plans.annual_amount_
// cents): 12 meses con el descuento, redondeado en céntimos. Antes la web
// redondeaba primero el mensual (99 → 79 €/mes → 948 €/año) y Stripe cobraba
// los 950,40 € reales: dos euros y medio de más sobre lo anunciado como
// exacto. En precios, la web copia al cobro — nunca al revés.
// test_cross_layer_contracts.py compara ambas fórmulas número a número.

/** Total billed per year on the annual plan — what Stripe actually charges. */
export function annualTotal(monthly: number): number {
  return Math.round(monthly * 12 * (1 - ANNUAL_DISCOUNT) * 100) / 100;
}

/** Monthly equivalent when billed annually (may carry cents: 79,20). */
export function annualMonthly(monthly: number): number {
  return Math.round((annualTotal(monthly) / 12) * 100) / 100;
}

/** Euros saved per year vs. paying month to month. */
export function annualSavings(monthly: number): number {
  return Math.round((monthly * 12 - annualTotal(monthly)) * 100) / 100;
}

/** "79,20" / "950,40" / "99" — números de dinero en formato español. */
export function fmtEur(value: number): string {
  return value.toLocaleString("es-ES", {
    minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
    maximumFractionDigits: 2,
  });
}

// ---------------------------------------------------------------------------
// Feature comparison table (backlog #17). Values in plain Spanish; `true` →
// check, `false` → em dash, string → literal cell.
// ---------------------------------------------------------------------------

export type ComparisonCell = boolean | string;

export type ComparisonRow = {
  label: string;
  starter: ComparisonCell;
  pro: ComparisonCell;
  agencia: ComparisonCell;
};

export type ComparisonGroup = {
  title: string;
  rows: ReadonlyArray<ComparisonRow>;
};

export const COMPARISON_GROUPS: ReadonlyArray<ComparisonGroup> = [
  {
    title: "Canales",
    rows: [
      { label: "Responde WhatsApp y email", starter: true, pro: true, agencia: true },
      { label: "Chat en tu web", starter: true, pro: true, agencia: true },
      { label: "Contesta el teléfono con voz natural", starter: false, pro: true, agencia: true },
      { label: "Responde SMS", starter: false, pro: true, agencia: true },
      {
        label: "Recoge contactos de Idealista, Fotocasa y Habitaclia",
        starter: true,
        pro: true,
        agencia: true,
      },
    ],
  },
  {
    title: "Límites",
    rows: [
      { label: "Personas de tu equipo", starter: "2", pro: "5", agencia: "15" },
      { label: "Propiedades en tu catálogo", starter: "100", pro: "500", agencia: "2.000" },
      { label: "Minutos de llamada al mes", starter: "—", pro: "600", agencia: "1.500" },
      { label: "Idiomas que atiende", starter: "5 idiomas", pro: "5 idiomas", agencia: "5 idiomas" },
    ],
  },
  {
    title: "Inteligencia",
    rows: [
      {
        label: "Plantillas de mensajes a tu medida",
        starter: false,
        pro: true,
        agencia: true,
      },
      { label: "Tu propia voz clonada", starter: false, pro: false, agencia: true },
      {
        label: "Recuerda a cada cliente aunque cambie de canal",
        starter: true,
        pro: true,
        agencia: true,
      },
      { label: "Ofertas en PDF listas para firmar", starter: false, pro: true, agencia: true },
    ],
  },
  {
    title: "Equipo y oficinas",
    rows: [
      {
        label: "Tus leads en tu CRM: export CSV y avisos automáticos (nativo Inmovilla/Witei en camino)",
        starter: false,
        pro: true,
        agencia: true,
      },
      {
        label: "Varias oficinas: cada contacto va a la que le corresponde",
        starter: false,
        pro: false,
        agencia: true,
      },
      {
        label: "Informes y comparativa con otras agencias (datos anónimos)",
        starter: false,
        pro: false,
        agencia: true,
      },
    ],
  },
  {
    title: "Acompañamiento",
    rows: [
      {
        label: "Puesta en marcha guiada en videollamada",
        starter: false,
        pro: true,
        agencia: true,
      },
      {
        label: "Soporte",
        starter: "Por email",
        pro: "Por email",
        agencia: "Prioritario",
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// FAQ de facturación (backlog #90) — shown under the pricing grid and reused
// as FAQPage JSON-LD on /precios.
// ---------------------------------------------------------------------------

export const BILLING_FAQ: ReadonlyArray<readonly [string, string]> = [
  [
    "¿Recibo justificante de cada pago?",
    "Sí. Stripe te envía por email el justificante de cada cobro y los tienes todos descargables desde la sección Facturación de tu panel. Al contratar puedes añadir el NIF de tu agencia para que figure en ellos.",
  ],
  [
    "¿El precio que veo es el que pago?",
    "Sí. 99, 199 o 399 €/mes es el importe exacto que se cobra: no se añade nada más al pasar por caja.",
  ],
  [
    "¿Cómo se paga?",
    "Con tarjeta, a través de Stripe (la misma pasarela que usan miles de empresas). No guardamos tu tarjeta en nuestros servidores: la gestiona Stripe de forma segura.",
  ],
  [
    "¿Qué pasa cuando acaban los 14 días de prueba?",
    "Eliges plan y pones la tarjeta, o tu cuenta se pausa. Nunca se cobra solo: como no pedimos tarjeta para probar, es imposible que te llevemos un cargo sin que tú lo decidas.",
  ],
  [
    "¿Puedo cambiar de plan o cancelar a mitad de mes?",
    "Puedes subir o bajar de plan cuando quieras desde Facturación; el cambio se prorratea. Si cancelas, sigues teniendo servicio hasta el final del periodo ya pagado y no se te cobra nada más.",
  ],
  [
    "¿Hay descuento si pago el año entero?",
    `Sí, un 20%: el plan Pro pasa de 199 a ${fmtEur(annualMonthly(PLAN_PRICES.pro))} €/mes ` +
      `(facturados ${fmtEur(annualTotal(PLAN_PRICES.pro))} €/año; ahorras ` +
      `${fmtEur(annualSavings(PLAN_PRICES.pro))} €/año). Starter queda en ` +
      `${fmtEur(annualMonthly(PLAN_PRICES.starter))} €/mes y Agencia en ` +
      `${fmtEur(annualMonthly(PLAN_PRICES.agencia))} €/mes.`,
  ],
];

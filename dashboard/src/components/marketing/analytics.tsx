"use client";

// Analítica de embudo de la landing (backlog #156).
//
// Usamos Vercel Web Analytics, que es *cookieless*: no escribe cookies ni
// identificadores persistentes en el navegador del visitante, solo agrega
// visitas y eventos. Por eso el aviso de CookieNotice puede seguir diciendo
// "solo cookies técnicas" sin mentir.
//
// Regla: la analítica NUNCA puede romper la página. Todo va envuelto en
// try/catch y cualquier fallo se ignora en silencio.
import type { ReactNode } from "react";

import { track } from "@vercel/analytics";

import type { PurchaseInterval, PurchasePlanCode } from "@/lib/purchase-intent";
import { withPurchaseIntent } from "@/lib/purchase-intent";
import { btnClass } from "./ui";
import type { BtnSize, BtnVariant } from "./ui";

/** Eventos del embudo. Lista cerrada para que los nombres no se dispersen. */
export type FunnelEvent =
  /** Clic en cualquier CTA que lleva a /sign-up. Prop: `origen`. */
  | "signup_click"
  /** La parrilla de precios entra en pantalla. Prop: `pagina`. */
  | "pricing_view"
  /** Se despliega la comparativa detallada de planes. */
  | "pricing_compare_open"
  /** El selector de plan completa sus dos preguntas. Props: plan/equipo/telefono. */
  | "plan_picker_submit"
  /** Clic en un CTA de reservar demo. Props: `origen`, `tipo`. */
  | "demo_book_click"
  /** Mensaje enviado en la demo de la landing. Prop: `modo`. */
  | "live_demo_send";

export type FunnelProps = Record<string, string | number | boolean>;

export function trackFunnel(event: FunnelEvent, props?: FunnelProps): void {
  try {
    track(event, props);
  } catch {
    // Sin analítica se sigue navegando igual.
  }
}

type CTAProps = {
  children: ReactNode;
  /** De dónde sale el clic: "hero", "header", "precios_pro"… */
  origen: string;
  variant?: BtnVariant;
  size?: BtnSize;
  className?: string;
  plan?: PurchasePlanCode;
  interval?: PurchaseInterval;
};

/**
 * CTA a /sign-up con el evento `signup_click` etiquetado por origen.
 * Renderiza exactamente el mismo <a> que `Btn`, así que es intercambiable.
 */
export function SignupCTA({
  children,
  origen,
  variant = "primary",
  size = "md",
  className = "",
  plan,
  interval,
}: CTAProps) {
  const intent = plan && interval ? { plan, interval } : null;
  return (
    <a
      href={withPurchaseIntent("/sign-up", intent)}
      className={btnClass(variant, size, className)}
      onClick={() =>
        trackFunnel("signup_click", {
          origen,
          ...(intent ? { plan: intent.plan, interval: intent.interval } : {}),
        })
      }
    >
      {children}
    </a>
  );
}

/**
 * CTA para reservar demo. `tipo` distingue el enlace real de Cal.com del
 * mailto de respaldo, para saber por dónde llegan las peticiones.
 */
export function BookCTA({
  children,
  href,
  origen,
  tipo,
  external = false,
  variant = "primary",
  size = "md",
  className = "",
}: Omit<CTAProps, "origen" | "plan" | "interval"> & {
  href: string;
  origen: string;
  tipo: "calcom" | "email";
  external?: boolean;
}) {
  return (
    <a
      href={href}
      className={btnClass(variant, size, className)}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      onClick={() => trackFunnel("demo_book_click", { origen, tipo })}
    >
      {children}
    </a>
  );
}

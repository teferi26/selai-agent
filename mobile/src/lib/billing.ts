// Vocabulario de facturación en el móvil.
//
// Es una copia deliberada del que usa el panel
// (dashboard/src/app/(app)/billing/status.ts): los dos leen los mismos
// `SubscriptionStatus` del backend (backend/app/db/models/billing.py) y el
// cliente tiene que oír exactamente lo mismo en el teléfono y en el
// ordenador. Si allí se añade un estado, aquí también.
//
// Lo que NO se copia es la parrilla de planes ni el checkout: el pago vive en
// Stripe a través del panel web, y fingir aquí un botón de comprar sería
// mentir. Desde el móvil se informa y se abre el panel.

// La dirección del panel vive en `lib/web.ts`: la comparten esta pantalla y
// el enlace de alta del login, y una sola copia evita que apunten a sitios
// distintos al cambiar de entorno. Se re-exporta para no tocar a quien ya la
// importaba desde aquí.
export { PANEL_BILLING_URL } from "@/lib/web";

export const STATUS_LABEL: Record<string, string> = {
  trialing: "En periodo de prueba",
  active: "Suscripción activa",
  past_due: "Pago pendiente",
  canceled: "Suscripción cancelada",
  unpaid: "Factura sin pagar",
  // La cuenta se pausa cuando la prueba termina sin tarjeta
  // (lifecycle.trial_expiry_sweep) y Stripe usa el mismo valor para las
  // suscripciones que él pausa.
  paused: "Cuenta en pausa",
  incomplete: "Pago sin terminar",
  incomplete_expired: "Pago sin terminar",
};

/** Nunca devolvemos el valor crudo: si Stripe estrena un estado, el dueño
 * vería inglés técnico en su pantalla de dinero. */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Sin suscripción";
  return STATUS_LABEL[status] ?? "Estado no disponible";
}

export type StatusNotice = {
  tone: "danger" | "warning";
  title: string;
  body: string;
};

/** Aviso honesto para los estados en los que el servicio está caído o a punto
 * de estarlo. null cuando no hay nada que arreglar. */
export function statusNotice(status: string | null | undefined): StatusNotice | null {
  switch (status) {
    case "paused":
      return {
        tone: "danger",
        title: "Tu cuenta está en pausa",
        body:
          "La IA ha dejado de atender: no responde WhatsApp, ni email, ni llamadas. " +
          "Elige un plan en el panel y vuelve a estar operativo en un minuto.",
      };
    case "canceled":
      return {
        tone: "danger",
        title: "Tu suscripción está cancelada",
        body:
          "La IA ha dejado de atender a tus clientes. Tus datos siguen aquí: " +
          "en cuanto contrates un plan vuelve a funcionar todo.",
      };
    case "unpaid":
      return {
        tone: "danger",
        title: "Hay una factura sin pagar",
        body:
          "La IA ha dejado de atender a tus clientes. Actualiza el método de pago " +
          "y el servicio se reactiva.",
      };
    case "past_due":
      return {
        tone: "warning",
        title: "No hemos podido cobrar tu última factura",
        body:
          "Seguimos atendiendo con normalidad, pero conviene revisar la tarjeta: " +
          "si el cobro no sale, el servicio se interrumpe.",
      };
    default:
      return null;
  }
}

/**
 * Días completos que quedan de prueba, redondeando hacia arriba.
 *
 * null cuando no hay fecha o no se entiende; 0 significa que la prueba
 * termina hoy o ya ha terminado — quien lo pinte debe distinguir los dos
 * casos en el texto, no en el número.
 */
export function trialDaysLeft(
  trialEndsAt: string | null | undefined,
  now: Date = new Date()
): number | null {
  if (!trialEndsAt) return null;
  const end = new Date(trialEndsAt).getTime();
  if (Number.isNaN(end)) return null;
  const ms = end - now.getTime();
  if (ms <= 0) return 0;
  return Math.ceil(ms / 86_400_000);
}

/** Frase que acompaña a los días de prueba. */
export function trialSentence(days: number | null): string | null {
  if (days === null) return null;
  if (days === 0) return "Tu prueba gratuita ha terminado.";
  if (days === 1) return "Te queda 1 día de prueba gratuita.";
  return `Te quedan ${days} días de prueba gratuita.`;
}

export const USAGE_LABEL: Record<string, string> = {
  agents: "Agentes",
  properties: "Propiedades",
  voice_minutes: "Minutos de voz",
};

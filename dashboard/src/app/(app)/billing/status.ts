// Vocabulario del estado de la suscripción, en lenguaje llano.
//
// Vive aquí (y no en lib/) porque es lenguaje de facturación, pero lo importan
// dos sitios: la página /billing y el aviso del layout del panel, que tiene que
// avisar en TODAS las pantallas cuando la IA ha dejado de atender.
//
// Los valores son los de `SubscriptionStatus` del backend
// (backend/app/db/models/billing.py): trialing, active, past_due, canceled,
// unpaid, paused. Sin `paused` la UI pintaba el literal crudo "paused".

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
  // Estos dos NO son estados nuestros: llegan tal cual de Stripe en la
  // respuesta de /change-plan (billing.py devuelve `remote["status"]`).
  // Sin ellos la UI enseñaba el literal en inglés "incomplete".
  incomplete: "Pago sin terminar",
  incomplete_expired: "Pago sin terminar",
};

/** ¿Sabemos decir este estado en castellano? Quien vaya a pintarlo dentro de
 * una frase debería preguntarlo antes: es preferible callar el estado a soltar
 * una palabra en inglés en mitad de un aviso de dinero. */
export function isKnownStatus(status: string | null | undefined): boolean {
  return !!status && status in STATUS_LABEL;
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Sin suscripción";
  // Nunca devolvemos el valor crudo: si Stripe (o el backend) estrena un
  // estado, el usuario vería inglés técnico en su pantalla de facturación.
  return STATUS_LABEL[status] ?? "Estado no disponible";
}

/** Estados en los que el backend acepta cambiar el precio de la suscripción
 * que ya existe: misma lista que `CHANGEABLE_STATUSES` en
 * backend/app/api/billing.py:256. Fuera de ella /change-plan contesta 409
 * siempre, así que ni lo intentamos — preguntar por gusto solo añade un viaje
 * y un 409 en la cara del cliente. */
const CHANGEABLE = new Set(["trialing", "active", "past_due"]);

export function canChangePlan(status: string | null | undefined): boolean {
  return !!status && CHANGEABLE.has(status);
}

/** Qué se le puede ofrecer al cliente en la parrilla de planes.
 *
 * - "switch": Stripe tiene una suscripción viva y editable (active,
 *   past_due). NUNCA se abre un checkout aquí: crearía una SEGUNDA
 *   suscripción y la agencia pagaría dos veces.
 * - "portal": `unpaid`. Stripe solo pone este estado DESPUÉS de haber tenido
 *   una suscripción cobrando y agotar los reintentos, así que la suscripción
 *   existe seguro — pero el backend no la deja editar (no está en
 *   CHANGEABLE_STATUSES). Comprar otra vez desde aquí duplicaría el cobro,
 *   así que no se ofrece: la salida es el portal, pagar la factura abierta.
 * - "buy": sin suscripción, prueba, pausa o cancelada. Aquí sí se puede
 *   contratar.
 */
export type PlanActionMode = "buy" | "switch" | "portal";

export function planActionMode(status: string | null | undefined): PlanActionMode {
  if (status === "unpaid") return "portal";
  if (status === "active" || status === "past_due") return "switch";
  return "buy";
}

/** ¿Podemos caer al checkout sin arriesgarnos a una suscripción duplicada?
 *
 * Solo cuando el estado descarta que Stripe tenga una suscripción cobrando:
 * cancelada (Stripe la borró), en pausa por nosotros (nunca llegó a Stripe),
 * en prueba (si tuviera suscripción, /change-plan habría funcionado) o sin
 * suscripción. Con active/past_due/unpaid un checkout sería un segundo cobro,
 * así que preferimos dejar al cliente sin comprar antes que cobrarle dos veces.
 */
export function checkoutIsSafe(status: string | null | undefined): boolean {
  return status !== "active" && status !== "past_due" && status !== "unpaid";
}

export type StatusNotice = {
  tone: "danger" | "warning";
  title: string;
  body: string;
};

/** Aviso honesto para los estados en los que el servicio está caído o a punto.
 * Devuelve null cuando todo va bien (prueba en curso o suscripción activa):
 * el panel no debe dar la brasa si no hay nada que arreglar. */
export function statusNotice(status: string | null | undefined): StatusNotice | null {
  switch (status) {
    case "paused":
      return {
        tone: "danger",
        title: "Tu cuenta está en pausa",
        body:
          "La IA ha dejado de atender: no responde WhatsApp, ni email, ni llamadas. " +
          "Elige un plan y vuelve a estar operativo en un minuto.",
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

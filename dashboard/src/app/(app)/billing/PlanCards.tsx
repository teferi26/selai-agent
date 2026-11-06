// Parrilla de planes con periodo mensual/anual y acciones de compra.
//
// Tres reglas que aquí valen dinero:
//
// 1. La acción depende del ESTADO de la suscripción, no de si el plan es el
//    mismo. Antes el botón se ocultaba cuando `plan.code === me.plan_code`, y
//    como TODAS las altas nacen en el plan más barato, el resultado era que el
//    plan de portada no se podía pagar desde el panel.
// 2. Con una suscripción viva en Stripe no se abre un checkout nuevo: se pide
//    el cambio de plan sobre la que ya existe. Un segundo checkout crearía una
//    segunda suscripción y la agencia pagaría dos veces. Y cuando /change-plan
//    dice que no puede, MIRAMOS POR QUÉ: solo "checkout_required" significa
//    "no hay nada vivo que tocar". Caer al checkout ante cualquier 409 volvía
//    a abrir la puerta al cobro doble (billing.py responde 409 también cuando
//    la suscripción existe pero tiene una forma que no se atreve a editar).
// 3. Ningún cobro se mueve sin que el cliente haya visto antes el importe:
//    o se lo enseñamos en el diálogo, o se lo enseña la pasarela de Stripe.
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ErrorState, SupportButton } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { PurchaseInterval } from "@/lib/purchase-intent";
import {
  canChangePlan,
  checkoutIsSafe,
  planActionMode,
  type PlanActionMode,
} from "./status";

export type BillingPlan = {
  id: string;
  code: string;
  name: string;
  description: string;
  monthly_price_eur: number;
  max_agents: number;
  max_properties: number;
  max_voice_minutes: number;
  /** El plan tiene precio mensual en Stripe: sin esto no se puede cobrar. */
  billable: boolean;
  /** …y precio anual. Si es false no ofrecemos el anual: prometerlo y que el
   * checkout falle sería peor que no ofrecerlo. */
  billable_yearly: boolean;
};

type Interval = PurchaseInterval;

/** Descuento anual anunciado en la web. */
const ANNUAL_DISCOUNT = 0.2;

/** Importe anual EXACTO que cobra Stripe, en céntimos.
 *
 * Misma fórmula que `annual_amount_cents` en
 * backend/app/scripts/sync_stripe_plans.py (12 meses con el -20%, redondeado a
 * céntimos). Se replica a propósito para que el panel enseñe el importe que se
 * va a cobrar de verdad y no una cifra "bonita" redondeada. */
function annualTotalCents(monthlyEur: number): number {
  return Math.round(monthlyEur * 12 * (1 - ANNUAL_DISCOUNT) * 100);
}

const EUR = new Intl.NumberFormat("es-ES", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function eur(cents: number): string {
  return EUR.format(cents / 100);
}

/** Precio en texto para un plan y un periodo, tal cual se factura. */
function priceLine(plan: BillingPlan, interval: Interval): string {
  if (interval === "month") return `${plan.monthly_price_eur} €/mes`;
  const total = annualTotalCents(plan.monthly_price_eur);
  return `${eur(total)} €/año (equivale a ${eur(total / 12)} €/mes)`;
}

/** El `detail` de un HTTPException de FastAPI, si lo hay.
 *
 * Distinguir un 409 de otro es lo que separa "este cliente no tiene nada en
 * Stripe, mándalo al checkout" de "esta suscripción existe y cobra, no la
 * dupliques". */
function errorDetail(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  try {
    const parsed: unknown = JSON.parse(err.body);
    const detail = (parsed as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const code = (detail as { code?: unknown }).code;
      return typeof code === "string" ? code : null;
    }
    return null;
  } catch {
    return null;
  }
}

function intervalWord(interval: Interval): string {
  return interval === "year" ? "anual" : "mensual";
}

type CardAction =
  | { kind: "current" }
  | { kind: "buy"; label: string }
  | { kind: "switch"; label: string }
  | { kind: "unavailable"; reason: string }
  | { kind: "none" };

function actionFor(
  plan: BillingPlan,
  interval: Interval,
  mode: PlanActionMode,
  isCurrent: boolean,
): CardAction {
  // Sin precio mensual en Stripe no hay nada que vender, ni siquiera en anual:
  // `_resolve_price` (billing.py:163) rechaza el plan con un 400 antes de
  // mirar el precio anual.
  const sellable =
    plan.billable && (interval === "month" || plan.billable_yearly);

  if (mode === "portal") {
    // Factura sin pagar: no se contrata nada desde aquí (ver status.ts).
    return isCurrent ? { kind: "current" } : { kind: "none" };
  }
  if (mode === "switch" && isCurrent) {
    // Ya está pagado: no se vuelve a contratar. Lo único que tiene sentido
    // ofrecer es el cambio de periodo, y solo si el usuario ha pedido ver el
    // anual (si no, el plan actual sale limpio y sin botones).
    if (interval === "year" && sellable) {
      return { kind: "switch", label: "Pasar a facturación anual" };
    }
    return { kind: "current" };
  }
  if (!sellable) {
    return {
      kind: "unavailable",
      reason:
        interval === "year" && plan.billable
          ? "La facturación anual de este plan aún no está disponible: elige pago mensual para contratarlo."
          : "Este plan todavía no se puede contratar desde aquí.",
    };
  }
  if (mode === "switch") return { kind: "switch", label: `Cambiar a ${plan.name}` };
  return { kind: "buy", label: isCurrent ? "Contratar este plan" : `Contratar ${plan.name}` };
}

type Pending = { plan: BillingPlan; interval: Interval };

export default function PlanCards({
  agencyId,
  plans,
  planCode,
  subscriptionStatus,
  trialEndsAt,
  initialPlanCode,
  initialInterval,
}: {
  agencyId: string;
  plans: BillingPlan[];
  planCode: string | null;
  subscriptionStatus: string | null;
  /** Fin de la prueba (ISO) para poder decir la fecha exacta del primer cobro. */
  trialEndsAt: string | null;
  /** Intención que llega desde la parrilla pública de precios. */
  initialPlanCode: string | null;
  initialInterval: Interval;
}) {
  const router = useRouter();
  const [interval, setBillingInterval] = useState<Interval>(initialInterval);
  const [selectedCode, setSelectedCode] = useState<string | null>(initialPlanCode);
  const [busyCode, setBusyCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Pending | null>(null);
  const [retry, setRetry] = useState<(() => void) | null>(null);

  const mode = planActionMode(subscriptionStatus);
  const inTrial = subscriptionStatus === "trialing";
  const yearlyOffered = plans.some((p) => p.billable && p.billable_yearly);
  const currentPlan = plans.find((p) => p.code === planCode) ?? null;

  /** Cambio sobre la suscripción existente.
   *
   * - "done": aplicado, hay que refrescar.
   * - "handled": no se ha podido, ya hemos explicado por qué y NO se ha
   *   cobrado nada (el backend rechaza antes de llamar a Stripe).
   * - "checkout": el backend confirma que no hay suscripción que tocar, así
   *   que la compra es legítima y pasa por la pasarela.
   */
  async function changePlan(
    plan: BillingPlan,
    when: Interval,
  ): Promise<"done" | "handled" | "checkout"> {
    try {
      const out = await api<{
        ok: boolean;
        status: string;
        billing_cycle_reset?: boolean;
      }>("/api/v1/billing/change-plan", {
        method: "POST",
        body: JSON.stringify({ agency_id: agencyId, plan_code: plan.code, interval: when }),
      });
      // No decimos "hemos aplicado el plan": billing.py NO escribe `plan_id`
      // aquí a propósito, lo hace el webhook cuando Stripe confirma. Y el
      // estado que devuelve es el de Stripe, que puede ser uno que no sabemos
      // decir en castellano, así que no se pinta.
      //
      // (n17) `billing_cycle_reset` = el cambio reinició el ciclo (pasar a
      // anual): Stripe factura el periodo nuevo AHORA. Decir "no se te cobra
      // nada" justo después del cargo más grande del producto era la clase de
      // sorpresa que acaba en una disputa de tarjeta.
      setNotice(
        `Hemos pedido el cambio al plan ${plan.name} con facturación ` +
          `${intervalWord(when)}. ` +
          (inTrial
            ? "Mientras dure la prueba no se te cobra nada; el primer cobro irá ya con este plan. "
            : out.billing_cycle_reset
              ? "Stripe factura ahora el nuevo periodo completo, descontando la parte no " +
                "consumida del anterior. Verás el cargo en tu próximo recibo de la tarjeta. "
              : "No se te cobra nada en este momento: el ajuste se prorratea y se " +
                "refleja en tu próxima factura. ") +
          "Stripe lo confirma en unos segundos y entonces se actualiza esta página.",
      );
      return "done";
    } catch (err) {
      const detail = errorDetail(err);
      if (err instanceof ApiError && err.status === 409) {
        if (detail === "checkout_required") return "checkout";
        if (detail === "annual_price_not_synced") {
          setError(
            "La facturación anual de este plan aún no está lista. " +
              "Puedes seguir con el pago mensual y cambiar el periodo cuando quieras.",
          );
          return "handled";
        }
        // 409 con cualquier otro motivo (hoy "subscription_items_unexpected"):
        // la suscripción EXISTE y está cobrando, pero el backend no se atreve
        // a editarla. Mandar aquí al checkout crearía una segunda suscripción
        // y el cliente pagaría dos veces. El backend rechaza antes de llamar a
        // Stripe, así que podemos afirmar que no se ha cobrado nada.
        setError(
          "No hemos podido cambiar el plan automáticamente: tu suscripción tiene una " +
            "configuración que preferimos no tocar por nuestra cuenta. No se ha cobrado " +
            "nada. Escríbenos y te la cambiamos nosotros hoy mismo.",
        );
        return "handled";
      }
      if (
        err instanceof ApiError &&
        err.status === 404 &&
        checkoutIsSafe(subscriptionStatus)
      ) {
        // El endpoint todavía no está desplegado. Solo caemos al checkout si
        // el estado descarta una suscripción viva: si no, preferimos fallar.
        return "checkout";
      }
      throw err;
    }
  }

  async function goToCheckout(plan: BillingPlan, when: Interval, startNow: boolean) {
    try {
      const baseReturn = new URLSearchParams({ plan: plan.code, interval: when });
      const successParams = new URLSearchParams(baseReturn);
      successParams.set("status", "success");
      successParams.set("start_now", startNow ? "1" : "0");
      const cancelParams = new URLSearchParams(baseReturn);
      cancelParams.set("status", "cancel");
      const { checkout_url } = await api<{ checkout_url: string }>("/api/v1/billing/checkout", {
        method: "POST",
        body: JSON.stringify({
          agency_id: agencyId,
          plan_code: plan.code,
          interval: when,
          start_now: startNow,
          success_url: `${window.location.origin}/billing?${successParams.toString()}`,
          cancel_url: `${window.location.origin}/billing?${cancelParams.toString()}`,
        }),
      });
      if (checkout_url) window.location.assign(checkout_url);
      // Sin STRIPE_SECRET_KEY el backend devuelve un stub sin URL: decirlo
      // claro es mejor que dejar el botón girando.
      else setError("Este plan todavía no se puede contratar desde aquí.");
    } catch (err) {
      if (err instanceof ApiError && errorDetail(err) === "annual_price_not_synced") {
        setError(
          "La facturación anual de este plan aún no está lista. " +
            "Puedes contratarlo al mes y cambiar el periodo cuando quieras.",
        );
        return;
      }
      if (err instanceof ApiError && errorDetail(err) === "already_subscribed") {
        setError(
          "Tu prueba ya está vinculada a una suscripción en Stripe y no hemos abierto " +
            "otra para evitar un cobro duplicado. Conserva la prueba o escríbenos para " +
            "activarla hoy de forma segura.",
        );
        return;
      }
      throw err;
    }
  }

  async function run(plan: BillingPlan, when: Interval, startNow = false) {
    setBusyCode(plan.code);
    setError(null);
    setNotice(null);
    setRetry(null);
    try {
      // Durante la prueba hay dos decisiones distintas. Conservarla sigue el
      // camino normal (cambio de plan o checkout con los días restantes).
      // Activar hoy debe ir a Stripe con el consentimiento explícito que el
      // backend exige para renunciar a esos días.
      if (inTrial && startNow) {
        await goToCheckout(plan, when, true);
        return;
      }
      // Solo se pide el cambio de plan en los estados en los que el backend
      // lo acepta (CHANGEABLE_STATUSES). En el resto contestaría 409 seguro,
      // así que se va derecho al checkout sin un viaje de más.
      if (canChangePlan(subscriptionStatus)) {
        const outcome = await changePlan(plan, when);
        if (outcome === "done") {
          router.refresh();
          return;
        }
        if (outcome === "handled") return;
        if (!checkoutIsSafe(subscriptionStatus)) {
          // Cinturón: aquí no deberíamos llegar (checkout_required implica que
          // no hay suscripción), pero si llegamos, no compramos otra vez.
          setError(
            "No hemos podido cambiar el plan. Para no arriesgarnos a cobrarte dos veces " +
              "no abrimos un pago nuevo: escríbenos y lo hacemos nosotros.",
          );
          return;
        }
      }
      await goToCheckout(plan, when, false);
    } catch {
      // Nada de prometer que no se ha cobrado: si el fallo llegó a mitad de
      // camino no lo sabemos. Ofrecemos reintentar y una vía de soporte.
      setError(
        mode === "switch"
          ? "No hemos podido completar el cambio de plan. Inténtalo otra vez o escríbenos y lo hacemos nosotros."
          : "No hemos podido iniciar el pago. Inténtalo otra vez.",
      );
      setRetry(() => () => void run(plan, when, startNow));
    } finally {
      setBusyCode(null);
    }
  }

  function onClick(plan: BillingPlan, action: CardAction) {
    if (action.kind !== "buy" && action.kind !== "switch") return;
    setSelectedCode(plan.code);
    // Todo clic que pueda acabar en /change-plan mueve el cobro sin pasar por
    // ninguna otra pantalla: hay que enseñar el importe antes. Cuando el
    // camino es el checkout de Stripe, el importe lo enseña Stripe y no
    // metemos un paso más entre el cliente y la compra.
    if (canChangePlan(subscriptionStatus)) {
      setConfirming({ plan, interval });
      return;
    }
    void run(plan, interval, false);
  }

  return (
    <section id="planes" className="scroll-mt-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-slate-500">Planes</h3>
        {yearlyOffered && (
          <div
            role="group"
            aria-label="Periodo de facturación"
            className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5 text-sm"
          >
            <button
              type="button"
              aria-pressed={interval === "month"}
              onClick={() => setBillingInterval("month")}
              className={`rounded-md px-3 py-1.5 ${
                interval === "month" ? "bg-selai-600 text-white" : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              Pago mensual
            </button>
            <button
              type="button"
              aria-pressed={interval === "year"}
              onClick={() => setBillingInterval("year")}
              className={`rounded-md px-3 py-1.5 ${
                interval === "year" ? "bg-selai-600 text-white" : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              Pago anual −20%
            </button>
          </div>
        )}
      </div>

      {mode === "portal" && (
        <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Tienes una factura sin pagar. Actualiza el método de pago en el portal de Stripe
          (el botón está más abajo) y tu plan se reactiva. No contratamos un plan nuevo
          desde aquí para no cobrarte dos veces.
        </p>
      )}

      {notice && (
        <div
          role="status"
          className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"
        >
          {notice}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {plans.map((p) => {
          const isCurrent = p.code === planCode;
          const isSelected = p.code === selectedCode;
          const action = actionFor(p, interval, mode, isCurrent);
          const total = annualTotalCents(p.monthly_price_eur);
          // Si el anual de ESTE plan no está sincronizado con Stripe, no
          // enseñamos un precio anual que no se puede contratar: se ve el
          // mensual y debajo el aviso.
          const shown: Interval =
            interval === "year" && p.billable && p.billable_yearly ? "year" : "month";
          return (
            <div
              key={p.id}
              aria-label={isSelected ? `${p.name}, plan elegido` : undefined}
              className={`flex flex-col rounded-xl border bg-white p-6 ${
                isSelected
                  ? "border-selai-500 ring-2 ring-selai-500 shadow-sm"
                  : isCurrent
                    ? "border-selai-200 ring-1 ring-selai-200"
                    : "border-slate-200"
              }`}
            >
              <div className="flex items-center justify-between">
                <h3 className="font-semibold">{p.name}</h3>
                {isSelected ? (
                  <span className="rounded-full bg-selai-600 px-2 py-0.5 text-xs text-white">
                    Plan elegido
                  </span>
                ) : isCurrent ? (
                  <span className="rounded-full bg-selai-50 px-2 py-0.5 text-xs text-selai-700">
                    Tu plan
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-2xl font-semibold">
                {shown === "month" ? p.monthly_price_eur : eur(total / 12)}€
                <span className="text-sm font-normal text-slate-500">/mes</span>
              </p>
              <p className="text-xs text-slate-500">
                {shown === "month" ? "facturado cada mes" : `facturados ${eur(total)} € al año`}
              </p>
              <p className="mt-2 text-sm text-slate-500">{p.description}</p>
              <ul className="mt-3 space-y-1 text-sm text-slate-600">
                <li>· {p.max_agents} agentes</li>
                <li>· {p.max_properties} propiedades</li>
                <li>· {p.max_voice_minutes} min de voz/mes</li>
              </ul>

              <div className="mt-4 flex-1" />

              {action.kind === "current" && (
                <p className="mt-4 rounded-md bg-slate-50 px-3 py-2 text-center text-sm font-medium text-slate-600">
                  Tu plan actual
                </p>
              )}
              {action.kind === "unavailable" && (
                <p className="mt-4 text-center text-xs text-slate-500">{action.reason}</p>
              )}
              {(action.kind === "buy" || action.kind === "switch") && (
                <button
                  type="button"
                  onClick={() => onClick(p, action)}
                  disabled={busyCode !== null}
                  className={`mt-4 w-full rounded-md px-3 py-2 text-sm font-medium disabled:opacity-60 ${
                    action.kind === "buy" || !isCurrent
                      ? "bg-selai-600 text-white hover:bg-selai-700"
                      : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  {busyCode === p.code ? "Un momento…" : action.label}
                </button>
              )}
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-xs text-slate-500">
        El precio que ves es el que se cobra. Puedes cambiar de plan o cancelar cuando quieras.
      </p>

      {error && (
        <ErrorState
          className="mt-4"
          title={error}
          message={null}
          onRetry={retry ?? undefined}
          retrying={busyCode !== null}
        >
          <SupportButton variant="link" label="Reportar un problema" />
        </ErrorState>
      )}

      {confirming && (
        <ConfirmChange
          pending={confirming}
          currentPlan={currentPlan}
          inTrial={inTrial}
          trialEndsAt={trialEndsAt}
          busy={busyCode !== null}
          onCancel={() => setConfirming(null)}
          onConfirm={(startNow) => {
            const { plan, interval: when } = confirming;
            setConfirming(null);
            void run(plan, when, startNow);
          }}
        />
      )}
    </section>
  );
}

/** Fecha larga en castellano, o null si no hay una fecha futura que enseñar. */
function futureDate(iso: string | null): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime()) || at.getTime() <= Date.now()) return null;
  return at.toLocaleDateString("es-ES", { day: "numeric", month: "long" });
}

/** Antes de tocar el dinero: qué plan, qué periodo, qué importe y cuándo se
 * cobra el ajuste. Sin esto, un clic cambiaba el cobro sin avisar. */
function ConfirmChange({
  pending,
  currentPlan,
  inTrial,
  trialEndsAt,
  busy,
  onCancel,
  onConfirm,
}: {
  pending: Pending;
  currentPlan: BillingPlan | null;
  inTrial: boolean;
  trialEndsAt: string | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (startNow: boolean) => void;
}) {
  const { plan, interval } = pending;
  const samePlan = currentPlan?.code === plan.code;
  const isDowngrade =
    !!currentPlan && !samePlan && plan.monthly_price_eur < currentPlan.monthly_price_eur;
  const trialEnds = inTrial ? futureDate(trialEndsAt) : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Confirmar cambio de plan"
    >
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <h3 className="text-lg font-semibold">
          {inTrial
            ? `Elige cuándo activar ${plan.name}`
            : samePlan && interval === "year"
            ? "Pasar a facturación anual"
            : samePlan
              ? `Continuar con el plan ${plan.name}`
              : `Cambiar al plan ${plan.name}`}
        </h3>

        <p className="mt-2 text-sm text-slate-700">
          {samePlan && interval === "year" ? (
            <>
              Sigues en el plan <strong>{plan.name}</strong>, pero pasas a pagar por año:{" "}
              <strong>{priceLine(plan, "year")}</strong>.
            </>
          ) : (
            <>
              {currentPlan && !samePlan ? `Pasas de ${currentPlan.name} a ` : "Contratas "}
              <strong>{plan.name}</strong>, con facturación {intervalWord(interval)}:{" "}
              <strong>{priceLine(plan, interval)}</strong>.
            </>
          )}
        </p>

        <ul className="mt-3 space-y-1.5 text-sm text-slate-600">
          {inTrial ? (
            <>
              <li>
                {trialEnds
                  ? `· Conservar la prueba: no se cobra hoy; el primer cobro será el ${trialEnds}.`
                  : "· Conservar la prueba: Stripe te enseñará el importe y la fecha antes de confirmar."}
              </li>
              <li>
                · Activar y pagar hoy: renuncias a los días gratuitos que queden y Stripe cobra{" "}
                {priceLine(plan, interval)} al confirmar el pago.
              </li>
            </>
          ) : interval === "year" ? (
            // (n17) Pasar a anual REINICIA el ciclo de facturación en Stripe:
            // el año entero se factura en el momento (menos la parte no
            // consumida del mes en curso). Prometer aquí "no se te cobra nada
            // hoy" era falso justo en el cargo más grande del producto.
            <li>
              · Al pasar a facturación anual, Stripe factura ahora el importe del año (
              {eur(annualTotalCents(plan.monthly_price_eur))}), descontando la parte no
              consumida de tu mes actual.
            </li>
          ) : (
            // proration_behavior="create_prorations" en billing.py:309 — un
            // cambio dentro de la facturación mensual no se cobra en el
            // momento: el ajuste se prorratea a la siguiente factura.
            <li>· No se te cobra nada hoy: el ajuste se prorratea y aparece en tu próxima factura.</li>
          )}
          {isDowngrade && (
            <li>
              · Al bajar de plan se aplican sus límites: {plan.max_agents} agentes,{" "}
              {plan.max_properties} propiedades y {plan.max_voice_minutes} min de voz al mes.
            </li>
          )}
          {samePlan && interval === "year" && (
            <li>· Si ya facturabas por año, no cambia nada ni se te cobra de nuevo.</li>
          )}
          <li>· No pierdes ninguna conversación ni ningún dato.</li>
        </ul>

        <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-60"
          >
            Volver
          </button>
          {inTrial ? (
            <>
              <button
                type="button"
                onClick={() => onConfirm(false)}
                disabled={busy}
                className="rounded-md border border-selai-300 bg-white px-4 py-2 text-sm font-medium text-selai-700 hover:bg-selai-50 disabled:opacity-60"
              >
                Elegir plan y conservar prueba
              </button>
              <button
                type="button"
                onClick={() => onConfirm(true)}
                disabled={busy}
                className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
              >
                Activar y pagar hoy
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => onConfirm(false)}
              disabled={busy}
              className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
            >
              Confirmar
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

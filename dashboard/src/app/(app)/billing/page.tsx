// Billing: current plan + usage + plan switching + Stripe customer portal.
import { getMe, serverApi } from "@/lib/server-api";
import { parsePurchaseIntent } from "@/lib/purchase-intent";
import BillingActions from "./BillingActions";
import CancelSubscription from "./CancelSubscription";
import PlanCards, { type BillingPlan } from "./PlanCards";
import { statusLabel } from "./status";

export const metadata = { title: "Facturación" };

const RESOURCE_LABEL: Record<string, string> = {
  agents: "Agentes",
  properties: "Propiedades",
  voice_minutes: "Minutos de voz",
};

export default async function BillingPage({
  searchParams,
}: {
  searchParams: Promise<{
    status?: string | string[];
    start_now?: string | string[];
    plan?: string | string[];
    interval?: string | string[];
  }>;
}) {
  const me = await getMe();
  const plans = await serverApi<BillingPlan[]>("/api/v1/billing/plans");
  // De vuelta de la pasarela de Stripe (success_url / cancel_url).
  const params = await searchParams;
  const flow = Array.isArray(params.status) ? params.status[0] : params.status;
  const startedNow =
    (Array.isArray(params.start_now) ? params.start_now[0] : params.start_now) === "1";
  const purchaseIntent = parsePurchaseIntent(params);
  // El aviso de estado NO se pinta aquí: lo pinta el layout del panel en todas
  // las pantallas, incluida esta. Repetirlo dejaba el mismo recuadro rojo dos
  // veces seguidas en /billing.

  return (
    <div className="max-w-4xl">
      <header className="mb-6">
        <h2 className="text-2xl font-semibold">Facturación</h2>
        <p className="text-sm text-slate-500">
          {me.agency_name} · {statusLabel(me.subscription_status)}
          {me.trial_ends_at && me.subscription_status === "trialing" && (
            <>
              {/* En pasado si ya venció: la barrida que pausa la cuenta corre
                  una vez al día, así que hay unas horas en las que el estado
                  sigue siendo "en prueba" con la fecha ya cumplida. */}
              {" "}—{" "}
              {me.trial_is_expired ? "la prueba terminó el" : "la prueba termina el"}{" "}
              {new Date(me.trial_ends_at).toLocaleDateString("es-ES", {
                day: "numeric",
                month: "long",
              })}
            </>
          )}
        </p>
      </header>

      {flow === "success" && (
        <div
          role="status"
          className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"
        >
          {startedNow
            ? "Listo, has activado el plan para empezar a pagar hoy. Stripe lo confirma en unos segundos y entonces se actualiza esta página."
            : "Listo, tu suscripción está en marcha. Si aún te quedaban días de prueba, no se te cobra hasta que termine. Puede tardar unos segundos en verse aquí: lo actualizamos en cuanto Stripe nos lo confirma."}
        </div>
      )}
      {flow === "cancel" && (
        <div
          role="status"
          className="mb-6 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600"
        >
          Has salido de la pasarela sin terminar. No se ha cobrado nada.
        </div>
      )}

      {me.usage.length > 0 && (
        <section className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
          {me.usage.map((u) => {
            const pct = u.limit > 0 ? Math.min(100, Math.round((u.used / u.limit) * 100)) : 0;
            const isVoice = u.resource === "voice_minutes";
            // Minutos de voz: barra propia con color acento al pasar el 80%.
            const barColor = isVoice
              ? pct >= 80
                ? "bg-accent"
                : "bg-selai-500"
              : pct >= 90
                ? "bg-amber-500"
                : "bg-selai-500";
            return (
              <div key={u.resource} className="rounded-lg border border-slate-200 bg-white p-4">
                <div className="flex items-baseline justify-between">
                  <span className="text-sm font-medium">
                    {RESOURCE_LABEL[u.resource] ?? u.resource}
                  </span>
                  <span className="text-xs text-slate-500">
                    {u.used} / {u.limit}
                  </span>
                </div>
                <div className="mt-2 h-2 rounded-full bg-slate-100">
                  <div
                    className={`h-2 rounded-full ${barColor}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                {isVoice && (
                  <p className={`mt-2 text-xs ${pct >= 80 ? "text-accent" : "text-slate-500"}`}>
                    {u.used} de {u.limit} min de voz usados este mes
                    {pct >= 80 && " — te queda poco margen"}
                  </p>
                )}
              </div>
            );
          })}
        </section>
      )}

      <PlanCards
        agencyId={me.agency_id}
        plans={plans}
        planCode={me.plan_code}
        subscriptionStatus={me.subscription_status}
        trialEndsAt={me.trial_ends_at}
        initialPlanCode={purchaseIntent?.plan ?? null}
        initialInterval={purchaseIntent?.interval ?? "month"}
      />

      <section className="mt-8">
        <BillingActions
          kind="portal"
          agencyId={me.agency_id}
          label="Gestionar método de pago y facturas"
        />
        <p className="mt-2 text-xs text-slate-500">
          Se abre el portal seguro de Stripe: cambiar tarjeta, descargar facturas o cancelar.
        </p>
        <div className="mt-4">
          <CancelSubscription agencyId={me.agency_id} />
        </div>
      </section>
    </div>
  );
}

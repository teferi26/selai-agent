export const PURCHASE_PLAN_CODES = ["starter", "pro", "agencia"] as const;

export type PurchasePlanCode = (typeof PURCHASE_PLAN_CODES)[number];
export type PurchaseInterval = "month" | "year";

export type PurchaseIntent = {
  plan: PurchasePlanCode;
  interval: PurchaseInterval;
};

const PURCHASE_PLAN_LABELS: Record<PurchasePlanCode, string> = {
  starter: "Starter",
  pro: "Pro",
  agencia: "Agencia",
};

type SearchValue = string | string[] | undefined;

function first(value: SearchValue): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function parsePurchaseIntent(params: {
  plan?: SearchValue;
  interval?: SearchValue;
}): PurchaseIntent | null {
  const plan = first(params.plan);
  const interval = first(params.interval);

  if (!PURCHASE_PLAN_CODES.some((code) => code === plan)) return null;
  if (interval !== "month" && interval !== "year") return null;

  return { plan: plan as PurchasePlanCode, interval };
}

export function withPurchaseIntent(path: string, intent: PurchaseIntent | null): string {
  if (!intent) return path;
  const query = new URLSearchParams({ plan: intent.plan, interval: intent.interval });
  return `${path}?${query.toString()}`;
}

export function purchasePlanLabel(plan: PurchasePlanCode): string {
  return PURCHASE_PLAN_LABELS[plan];
}

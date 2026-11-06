/**
 * Server-side data layer.
 *
 * Server components call the FastAPI backend directly (BACKEND_URL) with the
 * Clerk JWT injected here — the token never reaches client JavaScript. When
 * Clerk isn't configured (local dev), we fall back to the backend's
 * X-Dev-Email trusted-header mode.
 */
import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { PENDING_INVITE_COOKIE } from "@/app/invitacion/pending";

const BASE = process.env.BACKEND_URL || "http://localhost:8000";
const DEFAULT_TIMEOUT_MS = 30_000;

export const clerkEnabled = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

async function authHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (clerkEnabled) {
    const { auth } = await import("@clerk/nextjs/server");
    const { getToken } = await auth();
    const token = await getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  } else if (process.env.DEV_USER_EMAIL) {
    headers["X-Dev-Email"] = process.env.DEV_USER_EMAIL;
  }
  return headers;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
  ) {
    super(`API ${status}: ${body}`);
  }
}

export async function serverApi<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = { ...(await authHeaders()), ...(init?.headers || {}) };
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers,
      cache: "no-store",
      signal: init?.signal ?? AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });
  } catch (error) {
    if (error instanceof Error && ["AbortError", "TimeoutError"].includes(error.name)) {
      throw new ApiError(504, "backend_timeout");
    }
    throw error;
  }
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json();
}

export type Me = {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  agency_id: string;
  agency_name: string;
  agency_slug: string;
  subscription_status: string | null;
  plan_code: string | null;
  plan_name: string | null;
  monthly_price_eur: number | null;
  trial_ends_at: string | null;
  trial_is_expired: boolean;
  usage: { resource: string; used: number; limit: number }[];
};

/** Per-request cached /me. Redirects to onboarding when the user has no agency —
 * unless the user is in the middle of accepting a team invitation. */
export const getMe = cache(async (): Promise<Me> => {
  try {
    return await serverApi<Me>("/api/v1/me");
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      // "Sin agencia" tenía una única lectura: alta nueva. Con las
      // invitaciones de equipo hay otra, y la anterior le hacía daño: un
      // comercial invitado que aterrizaba aquí acababa en /onboarding, que
      // CREA una agencia — se montaba la suya, vacía, en lugar de entrar en
      // la de su jefe. Si trae una invitación a medias, su sitio es esa
      // invitación (la cookie la escribe /invitacion/{token}).
      const pending = (await cookies()).get(PENDING_INVITE_COOKIE)?.value;
      if (pending) redirect(`/invitacion/${encodeURIComponent(pending)}`);
      redirect("/onboarding");
    }
    throw err;
  }
});

export async function getAgencyId(): Promise<string> {
  // Dev escape hatch: explicit agency id skips the /me roundtrip.
  const fixed = process.env.NEXT_PUBLIC_DEFAULT_AGENCY_ID;
  if (!clerkEnabled && fixed) return fixed;
  return (await getMe()).agency_id;
}

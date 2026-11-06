"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";

export type Member = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  has_signed_in: boolean;
};

export type Seats = {
  members: number;
  pending: number;
  used: number;
  /** null = sin plan medido (entornos de desarrollo, datos sembrados). */
  limit: number | null;
};

export type MembersResponse = { members: Member[]; seats: Seats };

export type Invitation = {
  id: string;
  email: string;
  role: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  created_at: string | null;
  expires_at: string;
  invited_by: string | null;
  // Solo llegan al crear y al reenviar (n26): el backend dice si el correo
  // salió de verdad, y da el enlace por si hay que pasarlo a mano.
  email_sent?: boolean | null;
  accept_url?: string | null;
};

/** El aviso honesto: "enviada" solo si salió. */
function noticeFor(inv: Invitation, email: string, verbo: string): string {
  if (inv.email_sent === false) {
    return (
      `Invitación creada para ${email}, pero el email no ha salido. ` +
      `Pásale tú el enlace de abajo y podrá entrar igual.`
    );
  }
  return `Invitación ${verbo} a ${email}.`;
}

const ROLE_LABELS: Record<string, string> = {
  owner: "Responsable",
  agent: "Comercial",
  admin: "Administrador",
};

function errorCode(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  try {
    const detail = JSON.parse(err.body)?.detail;
    if (typeof detail === "string") return detail;
    return typeof detail?.code === "string" ? detail.code : null;
  } catch {
    return null;
  }
}

function formatDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("es-ES", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function EquipoClient({
  agencyId,
  currentUserId,
  canInvite,
  team,
  invitations,
}: {
  agencyId: string;
  currentUserId: string;
  canInvite: boolean;
  team: MembersResponse;
  invitations: Invitation[];
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("agent");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Enlace de respaldo cuando el email no sale (n26).
  const [fallbackUrl, setFallbackUrl] = useState<string | null>(null);

  const { seats } = team;
  const pending = invitations.filter((i) => i.status === "pending");
  const full = seats.limit !== null && seats.used >= seats.limit;

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    setFallbackUrl(null);
    try {
      const created = await api<Invitation>(
        `/api/v1/agencies/${agencyId}/invitations`,
        { method: "POST", body: JSON.stringify({ email, role }) }
      );
      // (n26) El backend dice si el correo salió de verdad. Antes se decía
      // "enviada" pase lo que pase, y el compañero esperaba un email que no
      // existía. Si falló, se ofrece el enlace para pasarlo por WhatsApp.
      setNotice(noticeFor(created, email.trim().toLowerCase(), "enviada"));
      setFallbackUrl(created.email_sent === false ? created.accept_url ?? null : null);
      setEmail("");
      router.refresh();
    } catch (err) {
      const messages: Record<string, string> = {
        plan_limit_reached:
          "Tu plan no incluye más personas. Amplía el plan para seguir invitando.",
        subscription_inactive:
          "Tu suscripción no está activa, así que no se pueden añadir personas.",
        already_member: "Esa persona ya está en tu equipo.",
        email_already_registered:
          "Ese email ya tiene cuenta en selai con otra agencia. Una persona sólo puede estar en un equipo.",
        invitation_already_pending:
          "Ya hay una invitación abierta para ese email. Puedes reenviarla desde la lista.",
        role_required: "Sólo el responsable de la agencia puede invitar.",
      };
      setError(
        messages[errorCode(err) ?? ""] ??
          "No se ha podido enviar la invitación. Inténtalo de nuevo en un momento."
      );
    } finally {
      setBusy(false);
    }
  }

  async function resend(inv: Invitation) {
    setError(null);
    setNotice(null);
    setFallbackUrl(null);
    try {
      const out = await api<Invitation>(
        `/api/v1/agencies/${agencyId}/invitations/${inv.id}/resend`,
        { method: "POST" }
      );
      setNotice(noticeFor(out, inv.email, "reenviada"));
      setFallbackUrl(out.email_sent === false ? out.accept_url ?? null : null);
      router.refresh();
    } catch {
      setError("No se ha podido reenviar la invitación.");
    }
  }

  async function revoke(inv: Invitation) {
    setError(null);
    setNotice(null);
    setFallbackUrl(null);
    try {
      // fetch pelado y no `api()`: la respuesta es un 204 sin cuerpo y el
      // wrapper hace res.json(), que reventaría sobre una respuesta vacía.
      const res = await fetch(
        `/api/backend/api/v1/agencies/${agencyId}/invitations/${inv.id}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(String(res.status));
      setNotice(`Invitación de ${inv.email} anulada.`);
      router.refresh();
    } catch {
      setError("No se ha podido anular la invitación.");
    }
  }

  return (
    <div className="max-w-3xl">
      <header className="mb-6">
        <h2 className="text-2xl font-semibold">Equipo</h2>
        <p className="mt-1 text-sm text-slate-500">
          Las personas de tu inmobiliaria que usan selai. Cada una entra con su
          propio email y ve las conversaciones, los leads y las visitas de la
          agencia.
        </p>
      </header>

      {/* Contador de plazas: miembros + invitaciones abiertas, igual que lo
          cuenta el backend al invitar. */}
      <section className="mb-6 rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-medium text-slate-900">
            {seats.limit === null ? (
              <>
                {seats.used} {seats.used === 1 ? "persona" : "personas"} en el equipo
              </>
            ) : (
              <>
                {seats.used} de {seats.limit} plazas ocupadas
              </>
            )}
          </p>
          <p className="text-xs text-slate-500">
            {seats.members} {seats.members === 1 ? "persona dentro" : "personas dentro"}
            {seats.pending > 0 &&
              ` · ${seats.pending} ${
                seats.pending === 1 ? "invitación pendiente" : "invitaciones pendientes"
              }`}
          </p>
        </div>
        {seats.limit !== null && (
          <span className="mt-3 block h-1.5 rounded-full bg-slate-100">
            <span
              className={`block h-1.5 rounded-full ${full ? "bg-amber-500" : "bg-accent"}`}
              style={{
                width: `${Math.min(100, Math.round((seats.used / seats.limit) * 100))}%`,
              }}
            />
          </span>
        )}
        {full && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-sm text-amber-900">
              Has ocupado todas las plazas de tu plan. Amplíalo para sumar a más
              gente de tu equipo.
            </p>
            <a
              href="/billing#planes"
              className="rounded-md bg-selai-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-selai-700"
            >
              Ver planes
            </a>
          </div>
        )}
      </section>

      {notice && (
        <div
          role="status"
          className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800"
        >
          {notice}
          {fallbackUrl && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <code className="rounded bg-white/70 px-2 py-1 font-mono text-xs break-all">
                {fallbackUrl}
              </code>
              <button
                type="button"
                onClick={() => void navigator.clipboard?.writeText(fallbackUrl)}
                className="rounded-md border border-emerald-300 px-2 py-1 text-xs font-medium hover:bg-white"
              >
                Copiar enlace
              </button>
            </div>
          )}
        </div>
      )}
      {error && (
        <div
          role="alert"
          className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
        >
          {error}
        </div>
      )}

      {canInvite && (
        <section className="mb-8 rounded-xl border border-slate-200 bg-white p-5">
          <h3 className="text-base font-semibold">Invitar a alguien</h3>
          <p className="mt-1 text-sm text-slate-500">
            Le enviaremos un email con un enlace para crear su cuenta. Caduca a los
            7 días.
          </p>
          <form onSubmit={invite} className="mt-4 flex flex-wrap items-end gap-3">
            <label className="flex-1 min-w-[220px] text-sm">
              <span className="mb-1 block font-medium">Email</span>
              <input
                required
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="comercial@tuinmobiliaria.es"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block font-medium">Rol</span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="agent">Comercial</option>
                <option value="owner">Responsable</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={busy || full}
              className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
              title={full ? "No quedan plazas en tu plan" : undefined}
            >
              {busy ? "Enviando…" : "Enviar invitación"}
            </button>
          </form>
          <p className="mt-3 text-xs text-slate-500">
            El <strong>responsable</strong> puede invitar y gestionar el plan; el{" "}
            <strong>comercial</strong> trabaja con los leads y las visitas.
          </p>
        </section>
      )}

      <section className="mb-8">
        <h3 className="mb-3 text-base font-semibold">En el equipo</h3>
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
          {team.members.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3">
              <div>
                <p className="text-sm font-medium text-slate-900">
                  {m.full_name}
                  {m.id === currentUserId && (
                    <span className="ml-2 text-xs font-normal text-slate-400">(tú)</span>
                  )}
                </p>
                <p className="text-xs text-slate-500">{m.email}</p>
              </div>
              <div className="flex items-center gap-3 text-xs">
                {!m.has_signed_in && (
                  <span className="text-slate-400">Sin entrar todavía</span>
                )}
                {!m.is_active && <span className="text-amber-700">Desactivada</span>}
                <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600">
                  {ROLE_LABELS[m.role] ?? m.role}
                </span>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3 className="mb-3 text-base font-semibold">Invitaciones pendientes</h3>
        {pending.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-8 text-center text-sm text-slate-500">
            No hay invitaciones abiertas.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
            {pending.map((inv) => (
              <li
                key={inv.id}
                className="flex flex-wrap items-center justify-between gap-2 px-5 py-3"
              >
                <div>
                  <p className="text-sm font-medium text-slate-900">{inv.email}</p>
                  <p className="text-xs text-slate-500">
                    {ROLE_LABELS[inv.role] ?? inv.role}
                    {inv.invited_by && ` · invitada por ${inv.invited_by}`}
                    {` · caduca el ${formatDate(inv.expires_at)}`}
                  </p>
                </div>
                {canInvite && (
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => resend(inv)}
                      className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium hover:bg-slate-50"
                    >
                      Reenviar
                    </button>
                    <button
                      type="button"
                      onClick={() => revoke(inv)}
                      className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50"
                    >
                      Anular
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

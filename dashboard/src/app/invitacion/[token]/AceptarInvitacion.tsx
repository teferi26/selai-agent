"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useUser } from "@clerk/nextjs";
import { api, ApiError } from "@/lib/api";
import { forgetPendingInvite, rememberPendingInvite } from "../pending";

const clerkEnabled = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

export type InvitationPreview = {
  agency_name: string;
  inviter_name: string | null;
  email: string;
  role: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
};

type Accepted = {
  agency_id: string;
  agency_name: string;
  user_id: string;
  role: string;
};

function useOptionalUser() {
  // useUser revienta fuera de ClerkProvider; en desarrollo sin Clerk no se usa.
  if (!clerkEnabled) return { user: null, isLoaded: true };
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return useUser();
}

const ROLE_LABELS: Record<string, string> = {
  owner: "responsable",
  agent: "comercial",
  admin: "administrador",
};

/** Mensajes de enlace muerto. El invitado no puede hacer nada con el enlace,
 * así que lo que necesita es saber a quién pedirle otro. */
const DEAD_LINK: Record<string, { title: string; body: string }> = {
  expired: {
    title: "Esta invitación ha caducado",
    body: "Los enlaces de invitación caducan a los 7 días. Pide a la persona que te invitó que te la reenvíe desde Ajustes → Equipo.",
  },
  revoked: {
    title: "Esta invitación ya no está activa",
    body: "La agencia ha anulado la invitación. Si crees que es un error, habla con la persona que te invitó.",
  },
  accepted: {
    title: "Esta invitación ya se ha usado",
    body: "Si la cuenta es tuya, inicia sesión con normalidad.",
  },
};

/** Código de error del backend a partir del cuerpo de un ApiError. */
function errorCode(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  try {
    const parsed = JSON.parse(err.body);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    return typeof detail?.code === "string" ? detail.code : null;
  } catch {
    return null;
  }
}

export default function AceptarInvitacion({
  token,
  preview,
}: {
  token: string;
  preview: InvitationPreview;
}) {
  const router = useRouter();
  const { user, isLoaded } = useOptionalUser();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mientras la invitación esté pendiente, el panel tiene que saber que esta
  // persona viene a UNIRSE y no a crear una agencia: sin esta marca, el primer
  // /me sin agencia la manda a /onboarding.
  //
  // Y en cuanto deja de estar pendiente hay que borrarla, porque si no la
  // marca encierra a la persona: `getMe` manda a /invitacion/{token} a
  // cualquiera sin agencia que la lleve, y esta pantalla ya sólo sabe decir
  // "este enlace está muerto". Quien abrió una invitación que luego caducó o
  // le anularon se quedaba dando vueltas entre las dos durante una semana,
  // sin panel y sin poder darse de alta por su cuenta.
  useEffect(() => {
    if (preview.status === "pending") rememberPendingInvite(token);
    else forgetPendingInvite();
  }, [token, preview.status]);

  const dead = DEAD_LINK[preview.status];
  if (dead) {
    return (
      <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-xl font-semibold text-slate-900">{dead.title}</h1>
        <p className="mt-2 text-sm text-slate-600">{dead.body}</p>
        <a
          href="/inbox"
          className="mt-6 inline-block rounded-md border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-50"
        >
          Ir al panel
        </a>
      </div>
    );
  }

  const sessionEmail =
    user?.primaryEmailAddress?.emailAddress ??
    user?.emailAddresses?.[0]?.emailAddress ??
    null;
  const signedIn = clerkEnabled ? !!user : true;
  const emailMatches =
    !clerkEnabled || !sessionEmail
      ? true
      : sessionEmail.trim().toLowerCase() === preview.email.toLowerCase();

  const redirectBack = `/invitacion/${encodeURIComponent(token)}`;

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<Accepted>(
        `/api/v1/invitations/${encodeURIComponent(token)}/accept`,
        {
          method: "POST",
          body: JSON.stringify({ full_name: user?.fullName ?? null }),
        }
      );
      forgetPendingInvite();
      // refresh() antes de navegar: el layout del panel lee /me en el servidor
      // y sin esto pintaría el estado "sin agencia" de hace un segundo.
      router.refresh();
      router.push(res.agency_id ? "/inbox" : "/");
    } catch (err) {
      const code = errorCode(err);
      const messages: Record<string, string> = {
        invitation_email_mismatch: `Esta invitación es para ${preview.email}. Has iniciado sesión con otra cuenta: cierra sesión y entra (o regístrate) con ese email.`,
        invitation_expired: "La invitación ha caducado. Pide que te la reenvíen.",
        invitation_revoked: "La agencia ha anulado esta invitación.",
        invitation_already_accepted: "Esta invitación ya se había usado.",
        already_in_another_agency:
          "Tu cuenta ya pertenece a otra agencia. Una persona sólo puede estar en un equipo.",
        plan_limit_reached: `El equipo de ${preview.agency_name} ha llegado al número de personas que incluye su plan. Avisa a la persona que te invitó para que amplíe el plan.`,
        subscription_inactive: `La suscripción de ${preview.agency_name} no está activa ahora mismo. Avisa a la persona que te invitó.`,
      };
      setError(
        (code && messages[code]) ||
          "No se ha podido completar la invitación. Vuelve a intentarlo en un momento."
      );
    } finally {
      setBusy(false);
    }
  }

  const roleLabel = ROLE_LABELS[preview.role] ?? ROLE_LABELS.agent;

  return (
    <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
      <h1 className="text-2xl font-semibold">
        Únete al equipo de {preview.agency_name}
      </h1>
      <p className="mt-2 text-sm text-slate-600">
        {preview.inviter_name ? `${preview.inviter_name} te ha invitado` : "Te han invitado"}{" "}
        a entrar como <strong>{roleLabel}</strong> en {preview.agency_name}. Verás sus
        conversaciones, sus leads y sus visitas.
      </p>

      <p className="mt-4 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
        Invitación para <strong>{preview.email}</strong>
      </p>

      {error && (
        <div
          role="alert"
          className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
        >
          {error}
        </div>
      )}

      {!isLoaded ? (
        <p className="mt-6 text-sm text-slate-500">Cargando…</p>
      ) : !signedIn ? (
        <>
          <a
            href={`/sign-up?redirect_url=${encodeURIComponent(redirectBack)}`}
            className="mt-6 block w-full rounded-md bg-selai-600 px-4 py-2.5 text-center text-sm font-medium text-white hover:bg-selai-700"
          >
            Crear mi cuenta y unirme
          </a>
          <a
            href={`/sign-in?redirect_url=${encodeURIComponent(redirectBack)}`}
            className="mt-3 block text-center text-sm text-slate-600 underline"
          >
            Ya tengo cuenta en selai
          </a>
          <p className="mt-4 text-xs text-slate-500">
            Regístrate con <strong>{preview.email}</strong>; si usas otro email no
            podremos darte acceso a {preview.agency_name}.
          </p>
        </>
      ) : !emailMatches ? (
        <>
          <div className="mt-6 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            Has iniciado sesión como <strong>{sessionEmail}</strong>, pero esta
            invitación es para <strong>{preview.email}</strong>. Cierra sesión y entra
            con ese email para poder unirte.
          </div>
          <a
            href={`/sign-in?redirect_url=${encodeURIComponent(redirectBack)}`}
            className="mt-4 block w-full rounded-md border border-slate-300 px-4 py-2.5 text-center text-sm font-medium hover:bg-slate-50"
          >
            Entrar con otra cuenta
          </a>
        </>
      ) : (
        <>
          <button
            type="button"
            onClick={accept}
            disabled={busy}
            className="mt-6 w-full rounded-md bg-selai-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
          >
            {busy ? "Uniéndote…" : `Unirme a ${preview.agency_name}`}
          </button>
          {/* Mientras la invitación siga viva, esta cuenta no puede crear una
              agencia propia (se lo impide el backend) ni entrar al panel. Si
              no es lo que quiere, lo único que le desatasca es que se la
              anulen: mejor decirlo que dejarle probar puertas. */}
          <p className="mt-4 text-xs text-slate-500">
            ¿No esperabas esta invitación? Pide a {preview.inviter_name ?? "quien te la envió"} que
            la anule desde Ajustes → Equipo y podrás usar selai por tu cuenta.
          </p>
        </>
      )}
    </div>
  );
}

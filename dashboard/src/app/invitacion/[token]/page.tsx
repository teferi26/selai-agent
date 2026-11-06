// /invitacion/{token} — lo que ve alguien a quien han invitado a un equipo,
// normalmente antes de tener cuenta.
//
// Es pública a propósito (proxy.ts): la persona todavía no se ha
// registrado, y mandarla al login sin explicarle de qué va el enlace es la
// forma más rápida de que lo tome por spam y lo borre. La vista previa sólo
// dice quién invita, a qué agencia y con qué email hay que entrar; aceptar
// exige sesión de Clerk y que ese email coincida.
import Link from "next/link";

import { ApiError, serverApi } from "@/lib/server-api";
import AceptarInvitacion, { type InvitationPreview } from "./AceptarInvitacion";

export const metadata = { title: "Invitación a un equipo" };

function Aviso({ title, body }: { title: string; body: string }) {
  return (
    <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm">
      <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
      <p className="mt-2 text-sm text-slate-600">{body}</p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-md border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-50"
      >
        Ir a selai
      </Link>
    </div>
  );
}

export default async function InvitacionPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  let preview: InvitationPreview | null = null;
  try {
    preview = await serverApi<InvitationPreview>(
      `/api/v1/invitations/${encodeURIComponent(token)}`
    );
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 404) throw err;
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-4 py-12">
      {preview === null ? (
        <Aviso
          title="Este enlace no es válido"
          body="La invitación no existe o el enlace está incompleto. Pide a la persona que te invitó que te la reenvíe."
        />
      ) : (
        <AceptarInvitacion token={token} preview={preview} />
      )}
    </main>
  );
}

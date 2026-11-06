// "Devolver a la IA" — visible cuando la conversación está en handed_off.
// Pide confirmación, hace POST /resume y refresca la página.
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ErrorState } from "@/components/ui";
import { api } from "@/lib/api";

export default function ResumeButton({
  agencyId,
  conversationId,
}: {
  agencyId: string;
  conversationId: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function askAndResume() {
    if (
      !window.confirm(
        "¿Devolver la conversación a la IA? El asistente volverá a responder a este cliente."
      )
    ) {
      return;
    }
    void resume();
  }

  // El reintento no vuelve a preguntar: el usuario ya confirmó una vez.
  async function resume() {
    setBusy(true);
    setError(null);
    try {
      await api<{ ok: boolean }>(
        `/api/v1/agencies/${agencyId}/conversations/${conversationId}/resume`,
        { method: "POST" }
      );
      setDone(true);
      router.refresh();
    } catch {
      setError("No se pudo devolver a la IA. Inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return <span className="text-sm text-emerald-700">La IA ha retomado la conversación ✓</span>;
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        onClick={askAndResume}
        disabled={busy}
        className="self-start rounded-md bg-selai-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
      >
        {busy ? "Devolviendo…" : "Devolver a la IA"}
      </button>
      {error && (
        <ErrorState
          title={error}
          message="La conversación sigue en tus manos mientras tanto."
          onRetry={() => void resume()}
          retrying={busy}
        />
      )}
    </div>
  );
}

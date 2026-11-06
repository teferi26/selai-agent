// "Exportar CSV" de /leads.
//
// Antes el fichero se construía en el navegador con los leads que la página ya
// tenía cargados, que eran 100 como máximo: el botón decía "todos los leads" y
// entregaba los primeros 100 sin avisar. Ahora el CSV lo genera el backend
// leyendo la tabla entera, y aquí solo lo descargamos — por eso el botón puede
// decir cuántos leads van dentro.
"use client";

import { ErrorState, Spinner } from "@/components/ui";
import { useState } from "react";

export default function ExportLeadsButton({
  agencyId,
  total,
}: {
  agencyId: string;
  /** Leads que hay en total, no los que se ven en pantalla. */
  total: number;
}) {
  const [downloading, setDownloading] = useState(false);
  const [failed, setFailed] = useState(false);

  async function exportCsv() {
    if (downloading) return;
    setDownloading(true);
    setFailed(false);
    try {
      // A través del proxy del panel, que es quien añade el token de Clerk.
      const res = await fetch(`/api/backend/api/v1/agencies/${agencyId}/leads.csv`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`export ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `leads-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setFailed(true);
    } finally {
      setDownloading(false);
    }
  }

  const count = total.toLocaleString("es-ES");

  return (
    <div className="flex flex-col items-end gap-2">
      <button
        onClick={exportCsv}
        disabled={total === 0 || downloading}
        title={
          total === 0
            ? "Todavía no hay leads que exportar"
            : `Se descargarán los ${count} leads, no solo los de esta pantalla`
        }
        className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 disabled:opacity-50"
      >
        {downloading && <Spinner size={16} className="text-selai-600" label={null} />}
        {downloading
          ? `Exportando ${count} leads…`
          : total === 0
            ? "Exportar CSV"
            : `Exportar CSV (${count})`}
      </button>
      {failed && (
        <ErrorState
          title="No hemos podido preparar el fichero"
          message="Inténtalo de nuevo en un momento."
          onRetry={exportCsv}
          retrying={downloading}
        />
      )}
    </div>
  );
}

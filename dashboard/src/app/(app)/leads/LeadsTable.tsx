// Tabla operativa de leads con paginación, cambio de etapa y próxima acción.
"use client";

import { ErrorState, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { Fragment, useState } from "react";

export type LeadStatus =
  | "new"
  | "engaged"
  | "qualified"
  | "visit_scheduled"
  | "visited"
  | "negotiating"
  | "won"
  | "lost"
  | "dormant";

export type Lead = {
  id: string;
  full_name: string | null;
  phone_e164: string | null;
  email: string | null;
  status: LeadStatus;
  temperature: "hot" | "warm" | "cold";
  score: number;
  language: string;
  budget_min: number | null;
  budget_max: number | null;
  operation: string | null;
  created_at: string;
  next_action_at: string | null;
  next_action_note: string | null;
};

export type LeadsPage = {
  items: Lead[];
  next_cursor: string | null;
  total: number;
};

const TEMP: Record<string, string> = {
  hot: "bg-red-100 text-red-700",
  warm: "bg-amber-100 text-amber-700",
  cold: "bg-slate-100 text-slate-600",
};

const STATUS_LABEL: Record<LeadStatus, string> = {
  new: "Nuevo",
  engaged: "Contactado",
  qualified: "Cualificado",
  visit_scheduled: "Visita agendada",
  visited: "Visitado",
  negotiating: "Negociando",
  won: "Ganado",
  lost: "Perdido",
  dormant: "Inactivo",
};

/** wa.me quiere solo dígitos, sin "+" ni espacios; si el número parece
 * español y viene sin prefijo, le añadimos el 34. */
function waHref(phone: string): string {
  let digits = phone.replace(/\D/g, "");
  if (!phone.trim().startsWith("+") && digits.length === 9) {
    digits = `34${digits}`;
  }
  return `https://wa.me/${digits}`;
}

function telHref(phone: string): string {
  return `tel:${phone.replace(/[^\d+]/g, "")}`;
}

function toLocalDateTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function LeadRow({
  agencyId,
  lead,
  onUpdated,
}: {
  agencyId: string;
  lead: Lead;
  onUpdated: (lead: Lead) => void;
}) {
  const [statusValue, setStatusValue] = useState(lead.status);
  const [statusState, setStatusState] = useState<"idle" | "saving" | "saved" | "error">(
    "idle"
  );
  const [editingAction, setEditingAction] = useState(false);
  const [actionAt, setActionAt] = useState(() => toLocalDateTime(lead.next_action_at));
  const [actionNote, setActionNote] = useState(lead.next_action_note || "");
  const [actionState, setActionState] = useState<"idle" | "saving" | "error">("idle");
  const [actionError, setActionError] = useState("");
  const rowSaving = statusState === "saving" || actionState === "saving";

  async function patchLead(body: Record<string, unknown>): Promise<Lead> {
    const updated = await api<Lead>(`/api/v1/agencies/${agencyId}/leads/${lead.id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    onUpdated(updated);
    return updated;
  }

  async function changeStatus(nextStatus: LeadStatus) {
    if (rowSaving || nextStatus === statusValue) return;
    const previous = statusValue;
    setStatusValue(nextStatus);
    setStatusState("saving");
    try {
      const updated = await patchLead({ status: nextStatus });
      setStatusValue(updated.status);
      setStatusState("saved");
    } catch {
      setStatusValue(previous);
      setStatusState("error");
    }
  }

  function openActionEditor() {
    setActionAt(toLocalDateTime(lead.next_action_at));
    setActionNote(lead.next_action_note || "");
    setActionError("");
    setActionState("idle");
    setEditingAction(true);
  }

  async function saveAction() {
    if (rowSaving) return;
    const parsed = new Date(actionAt);
    if (!actionAt || Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
      setActionError("Elige una fecha y hora futuras.");
      return;
    }
    const note = actionNote.trim();
    if (!note) {
      setActionError("Indica qué hay que hacer.");
      return;
    }

    setActionState("saving");
    setActionError("");
    try {
      const updated = await patchLead({
        next_action_at: parsed.toISOString(),
        next_action_note: note,
      });
      setActionAt(toLocalDateTime(updated.next_action_at));
      setActionNote(updated.next_action_note || "");
      setEditingAction(false);
      setActionState("idle");
    } catch {
      setActionState("error");
      setActionError("No se ha podido guardar. Inténtalo de nuevo.");
    }
  }

  async function clearAction() {
    if (rowSaving) return;
    setActionState("saving");
    setActionError("");
    try {
      await patchLead({ next_action_at: null, next_action_note: null });
      setActionAt("");
      setActionNote("");
      setEditingAction(false);
      setActionState("idle");
    } catch {
      setActionState("error");
      setActionError("No se ha podido quitar la acción.");
    }
  }

  return (
    <Fragment>
      <tr>
        <td className="px-4 py-2 font-medium">{lead.full_name || "-"}</td>
        <td className="px-4 py-2 text-slate-600">
          {lead.phone_e164 || lead.email || "-"}
        </td>
        <td className="px-4 py-2">
          <div className="min-w-40">
            <select
              aria-label={`Estado de ${lead.full_name || "lead"}`}
              value={statusValue}
              disabled={rowSaving}
              onChange={(event) => void changeStatus(event.target.value as LeadStatus)}
              className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm disabled:opacity-60"
            >
              {(Object.keys(STATUS_LABEL) as LeadStatus[]).map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABEL[status]}
                </option>
              ))}
            </select>
            <p
              aria-live="polite"
              className={`mt-1 min-h-4 text-xs ${
                statusState === "error" ? "text-red-700" : "text-slate-500"
              }`}
            >
              {statusState === "saving"
                ? "Guardando…"
                : statusState === "saved"
                  ? "Guardado"
                  : statusState === "error"
                    ? "No guardado"
                    : ""}
            </p>
          </div>
        </td>
        <td className="px-4 py-2">
          <span
            className={`rounded px-1.5 py-0.5 text-xs ${TEMP[lead.temperature]}`}
            title={
              lead.temperature === "hot"
                ? "Caliente: presupuesto definido + búsqueda concreta + urgencia"
                : undefined
            }
          >
            {lead.temperature}
          </span>
        </td>
        <td className="px-4 py-2 text-right">
          {lead.budget_max
            ? `€${(lead.budget_min || 0).toLocaleString()}–${lead.budget_max.toLocaleString()}`
            : "-"}
        </td>
        <td className="px-4 py-2">{lead.operation || "-"}</td>
        <td className="px-4 py-2">{lead.language}</td>
        <td className="px-4 py-2 text-slate-500">
          {new Date(lead.created_at).toLocaleDateString("es-ES")}
        </td>
        <td className="px-4 py-2">
          <div className="min-w-48">
            {lead.next_action_at ? (
              <>
                <p className="font-medium text-slate-800">
                  {new Date(lead.next_action_at).toLocaleString("es-ES", {
                    dateStyle: "short",
                    timeStyle: "short",
                  })}
                </p>
                <p
                  className="max-w-64 truncate text-xs text-slate-500"
                  title={lead.next_action_note || ""}
                >
                  {lead.next_action_note}
                </p>
              </>
            ) : (
              <span className="text-slate-400">Sin programar</span>
            )}
            <button
              type="button"
              onClick={openActionEditor}
              disabled={rowSaving}
              className="mt-1 text-xs font-medium text-selai-700 hover:text-selai-900 disabled:opacity-60"
            >
              {lead.next_action_at ? "Editar" : "Programar"}
            </button>
          </div>
        </td>
        <td className="px-4 py-2">
          {lead.phone_e164 ? (
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <a
                href={telHref(lead.phone_e164)}
                className="rounded border border-slate-200 px-2 py-1 text-xs hover:bg-slate-50"
                title={`Llamar a ${lead.phone_e164}`}
              >
                Llamar
              </a>
              <a
                href={waHref(lead.phone_e164)}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs text-emerald-800 hover:bg-emerald-100"
                title={`Abrir WhatsApp con ${lead.phone_e164}`}
              >
                WhatsApp
              </a>
            </div>
          ) : (
            <span className="text-xs text-slate-400">-</span>
          )}
        </td>
      </tr>
      {editingAction && (
        <tr className="bg-slate-50/80">
          <td colSpan={10} className="px-4 py-3">
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                void saveAction();
              }}
            >
              <label className="min-w-56 flex-1 text-xs font-medium text-slate-700">
                Fecha y hora
                <input
                  type="datetime-local"
                  required
                  value={actionAt}
                  onChange={(event) => setActionAt(event.target.value)}
                  className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                />
              </label>
              <label className="min-w-72 flex-[2] text-xs font-medium text-slate-700">
                Próxima acción
                <input
                  type="text"
                  required
                  maxLength={500}
                  value={actionNote}
                  onChange={(event) => setActionNote(event.target.value)}
                  placeholder="Ej. Llamar para confirmar financiación"
                  className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                />
              </label>
              <button
                type="submit"
                disabled={rowSaving}
                className="inline-flex h-10 items-center gap-2 rounded-md bg-selai-600 px-4 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
              >
                {actionState === "saving" && <Spinner size={16} label={null} />}
                Guardar
              </button>
              {lead.next_action_at && (
                <button
                  type="button"
                  onClick={() => void clearAction()}
                  disabled={rowSaving}
                  className="h-10 rounded-md border border-red-200 bg-white px-3 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
                >
                  Quitar acción
                </button>
              )}
              <button
                type="button"
                onClick={() => setEditingAction(false)}
                disabled={rowSaving}
                className="h-10 px-2 text-sm font-medium text-slate-600 hover:text-slate-900 disabled:opacity-60"
              >
                Cancelar
              </button>
            </form>
            <p aria-live="polite" className="mt-2 min-h-4 text-xs text-red-700">
              {actionError}
            </p>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export default function LeadsTable({
  agencyId,
  initialItems,
  initialCursor,
  total,
}: {
  agencyId: string;
  initialItems: Lead[];
  initialCursor: string | null;
  total: number;
}) {
  const [leads, setLeads] = useState(initialItems);
  const [cursor, setCursor] = useState(initialCursor);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  function updateLead(updated: Lead) {
    setLeads((current) => current.map((lead) => (lead.id === updated.id ? updated : lead)));
  }

  async function loadMore() {
    if (!cursor || loading) return;
    setLoading(true);
    setFailed(false);
    try {
      const page = await api<LeadsPage>(
        `/api/v1/agencies/${agencyId}/leads?paginated=true&cursor=${encodeURIComponent(cursor)}`
      );
      setLeads((current) => {
        const seen = new Set(current.map((lead) => lead.id));
        return [...current, ...page.items.filter((lead) => !seen.has(lead.id))];
      });
      setCursor(page.next_cursor);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <p className="mb-2 text-sm text-slate-500">
        {cursor
          ? `Mostrando ${leads.length.toLocaleString("es-ES")} de ${total.toLocaleString("es-ES")} leads`
          : `${leads.length.toLocaleString("es-ES")} lead${leads.length === 1 ? "" : "s"}`}
      </p>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2 text-left">Nombre</th>
              <th className="px-4 py-2 text-left">Contacto</th>
              <th className="px-4 py-2 text-left">Estado</th>
              <th className="px-4 py-2 text-left">Temp.</th>
              <th className="px-4 py-2 text-right">Presupuesto</th>
              <th className="px-4 py-2 text-left">Operación</th>
              <th className="px-4 py-2 text-left">Idioma</th>
              <th className="px-4 py-2 text-left">Creado</th>
              <th className="px-4 py-2 text-left">Próxima acción</th>
              <th className="px-4 py-2 text-left">Contacto rápido</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {leads.map((lead) => (
              <LeadRow key={lead.id} agencyId={agencyId} lead={lead} onUpdated={updateLead} />
            ))}
            {leads.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-8 text-center text-slate-500">
                  Sin leads todavía.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {failed && (
        <ErrorState
          className="mt-3"
          title="No hemos podido cargar más leads"
          message="Los que ya se ven siguen siendo válidos."
          onRetry={loadMore}
          retrying={loading}
        />
      )}

      {cursor && (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={() => void loadMore()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-60"
          >
            {loading && <Spinner size={16} className="text-selai-600" label={null} />}
            {loading ? "Cargando…" : "Cargar más"}
          </button>
        </div>
      )}
    </div>
  );
}

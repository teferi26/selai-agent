"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";

const MAX_DESCRIPTION_LENGTH = 12_000;

export type DescriptionDraft = {
  id: string;
  description: string;
  generated_description: string;
  highlights: string[];
  weaknesses: string[];
  base_description: string;
  created_at: string;
  updated_at: string;
};

type ApplyOut = {
  description: string;
  applied_at: string;
  changed: boolean;
  undo_id: string | null;
};

type HistoryItem = {
  id: string;
  description: string;
  replaced_at: string;
  restored_at: string | null;
  can_restore: boolean;
};

type Operation =
  | "loading"
  | "generating"
  | "saving"
  | "applying"
  | "cancelling"
  | "history"
  | "restoring"
  | null;

type ErrorAction = "load" | "generate" | "save" | "apply" | "cancel" | "history" | "restore";
type EditorError = { action: ErrorAction; message: string } | null;

function detailFrom(error: unknown): unknown {
  if (!(error instanceof ApiError)) return null;
  try {
    return JSON.parse(error.body)?.detail ?? null;
  } catch {
    return null;
  }
}

function errorCode(error: unknown): string | null {
  const detail = detailFrom(error);
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "code" in detail) {
    const code = (detail as { code?: unknown }).code;
    return typeof code === "string" ? code : null;
  }
  return null;
}

function conflictDescription(error: unknown): string | null {
  const detail = detailFrom(error);
  if (!detail || typeof detail !== "object" || !("current_description" in detail)) {
    return null;
  }
  const value = (detail as { current_description?: unknown }).current_description;
  return typeof value === "string" ? value : null;
}

function sessionDraftKey(agencyId: string, propertyId: string, draftId: string) {
  return `selai:description-draft:${agencyId}:${propertyId}:${draftId}`;
}

function readSessionDraft(agencyId: string, propertyId: string, draftId: string) {
  try {
    const recovered = window.sessionStorage.getItem(
      sessionDraftKey(agencyId, propertyId, draftId)
    );
    return recovered === null
      ? null
      : recovered.slice(0, MAX_DESCRIPTION_LENGTH);
  } catch {
    return null;
  }
}

function clearSessionDraft(agencyId: string, propertyId: string, draftId: string) {
  try {
    window.sessionStorage.removeItem(
      sessionDraftKey(agencyId, propertyId, draftId)
    );
  } catch {
    // Browsers may disable storage. The server-side draft remains canonical.
  }
}

export default function DescriptionDraftEditor({
  agencyId,
  propertyId,
  propertyTitle,
  currentDescription,
  hasInitialDraft,
  hasInitialHistory,
  canApply,
  onClose,
  onDescriptionChange,
  onDraftStateChange,
  onHistoryStateChange,
}: {
  agencyId: string;
  propertyId: string;
  propertyTitle: string;
  currentDescription: string;
  hasInitialDraft: boolean;
  hasInitialHistory: boolean;
  canApply: boolean;
  onClose: () => void;
  onDescriptionChange: (description: string) => void;
  onDraftStateChange: (hasDraft: boolean) => void;
  onHistoryStateChange: (hasHistory: boolean) => void;
}) {
  const [draft, setDraft] = useState<DescriptionDraft | null>(null);
  const [text, setText] = useState("");
  const [operation, setOperation] = useState<Operation>(null);
  const [error, setError] = useState<EditorError>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [undoId, setUndoId] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmRegenerate, setConfirmRegenerate] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null);

  const busy = operation !== null;
  const dirty = draft !== null && text !== draft.description;

  const loadDraft = useCallback(async () => {
    setOperation("loading");
    setError(null);
    try {
      const out = await api<DescriptionDraft>(
        `/api/v1/agencies/${agencyId}/properties/${propertyId}/description-draft`
      );
      if (draft && draft.id !== out.id) {
        clearSessionDraft(agencyId, propertyId, draft.id);
      }
      const recovered = readSessionDraft(agencyId, propertyId, out.id);
      setDraft(out);
      setText(recovered ?? out.description);
      if (recovered !== null && recovered !== out.description) {
        setSuccess("Hemos recuperado los cambios locales que aún no habías guardado.");
      }
      onDraftStateChange(true);
    } catch (loadError) {
      if (loadError instanceof ApiError && loadError.status === 404) {
        onDraftStateChange(false);
        setDraft(null);
      } else {
        setError({
          action: "load",
          message: "No hemos podido recuperar el borrador. Inténtalo de nuevo; la descripción actual no ha cambiado.",
        });
      }
    } finally {
      setOperation(null);
    }
  }, [agencyId, draft, onDraftStateChange, propertyId]);

  useEffect(() => {
    if (!hasInitialDraft || draft) return;
    // Defer the initial request until after the effect has committed. This
    // avoids a synchronous state cascade while keeping the server draft as
    // the source of truth.
    const timer = window.setTimeout(() => void loadDraft(), 0);
    return () => window.clearTimeout(timer);
  }, [draft, hasInitialDraft, loadDraft]);

  useEffect(() => {
    if (!dirty) return;
    const protectUnsavedText = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protectUnsavedText);
    return () => window.removeEventListener("beforeunload", protectUnsavedText);
  }, [dirty]);

  useEffect(() => {
    if (!draft) return;
    try {
      const key = sessionDraftKey(agencyId, propertyId, draft.id);
      if (dirty) window.sessionStorage.setItem(key, text);
      else window.sessionStorage.removeItem(key);
    } catch {
      // Saving on the backend still works when browser storage is disabled.
    }
  }, [agencyId, dirty, draft, propertyId, text]);

  async function generate(replaceExisting: boolean) {
    if (busy) return;
    setOperation("generating");
    setError(null);
    setSuccess(null);
    setConflict(null);
    try {
      const out = await api<DescriptionDraft>(
        `/api/v1/agencies/${agencyId}/properties/${propertyId}/describe-from-photos`,
        {
          method: "POST",
          body: JSON.stringify({
            target_language: "es",
            replace_existing_draft: replaceExisting,
          }),
        }
      );
      if (draft && draft.id !== out.id) {
        clearSessionDraft(agencyId, propertyId, draft.id);
      }
      setDraft(out);
      setText(out.description);
      setConfirmRegenerate(false);
      onDraftStateChange(true);
    } catch (generateError) {
      if (errorCode(generateError) === "active_description_draft_exists") {
        await loadDraft();
        return;
      }
      setError({
        action: "generate",
        message: "No hemos podido crear el borrador desde las fotos. Puedes reintentarlo; la descripción actual sigue intacta.",
      });
    } finally {
      setOperation(null);
    }
  }

  async function saveDraft() {
    if (!draft || !text.trim() || busy) return;
    setOperation("saving");
    setError(null);
    setSuccess(null);
    try {
      const out = await api<DescriptionDraft>(
        `/api/v1/agencies/${agencyId}/properties/${propertyId}/description-draft/${draft.id}`,
        { method: "PUT", body: JSON.stringify({ description: text }) }
      );
      clearSessionDraft(agencyId, propertyId, out.id);
      setDraft(out);
      setText(out.description);
      setSuccess("Borrador guardado. Puedes continuar más tarde.");
    } catch {
      setError({
        action: "save",
        message: "No hemos podido guardar los cambios. El texto sigue aquí para que puedas reintentarlo.",
      });
    } finally {
      setOperation(null);
    }
  }

  async function applyDraft(force = false) {
    if (!draft || !text.trim() || busy || !canApply) return;
    setOperation("applying");
    setError(null);
    setSuccess(null);
    try {
      const out = await api<ApplyOut>(
        `/api/v1/agencies/${agencyId}/properties/${propertyId}/description`,
        {
          method: "PUT",
          body: JSON.stringify({
            draft_id: draft.id,
            description: text,
            force,
            confirmed_current_description: force ? conflict : undefined,
          }),
        }
      );
      clearSessionDraft(agencyId, propertyId, draft.id);
      setDraft(null);
      setConflict(null);
      setUndoId(out.undo_id);
      setSuccess(
        out.changed
          ? "Descripción aplicada exactamente como la revisaste."
          : "La propiedad ya tenía exactamente este texto."
      );
      onDescriptionChange(out.description);
      onDraftStateChange(false);
      if (out.undo_id) {
        onHistoryStateChange(true);
        setHistory(null);
      }
    } catch (applyError) {
      if (errorCode(applyError) === "description_changed_since_draft") {
        const latest = conflictDescription(applyError);
        if (latest !== null) {
          setConflict(latest);
          onDescriptionChange(latest);
        }
        setError({
          action: "apply",
          message: "Otra persona cambió la descripción desde que se creó este borrador. Revisa ambos textos antes de decidir si quieres sustituirla.",
        });
      } else if (applyError instanceof ApiError && applyError.status === 403) {
        setError({
          action: "apply",
          message: "Solo una persona propietaria o administradora puede aplicar el texto final. El borrador sigue guardado.",
        });
      } else {
        setError({
          action: "apply",
          message: "No hemos podido aplicar la descripción. El borrador y tus cambios siguen en pantalla para reintentarlo.",
        });
      }
    } finally {
      setOperation(null);
    }
  }

  async function cancelDraft() {
    if (!draft || busy) return;
    setOperation("cancelling");
    setError(null);
    try {
      await api(
        `/api/v1/agencies/${agencyId}/properties/${propertyId}/description-draft/${draft.id}`,
        { method: "DELETE" }
      );
      clearSessionDraft(agencyId, propertyId, draft.id);
      setDraft(null);
      setText("");
      setConfirmCancel(false);
      onDraftStateChange(false);
      onClose();
    } catch {
      setError({
        action: "cancel",
        message: "No hemos podido cancelar el borrador. Sigue guardado y no hemos cambiado la propiedad.",
      });
    } finally {
      setOperation(null);
    }
  }

  const loadHistory = useCallback(async () => {
    setOperation("history");
    setError(null);
    try {
      const items = await api<HistoryItem[]>(
        `/api/v1/agencies/${agencyId}/properties/${propertyId}/description-history`
      );
      setHistory(items);
      setHistoryOpen(true);
      onHistoryStateChange(items.length > 0);
    } catch {
      setError({
        action: "history",
        message: "No hemos podido cargar el historial. La descripción actual no ha cambiado.",
      });
    } finally {
      setOperation(null);
    }
  }, [agencyId, onHistoryStateChange, propertyId]);

  async function restore(historyId: string) {
    if (busy || !canApply) return;
    setRestoreTarget(historyId);
    setOperation("restoring");
    setError(null);
    setSuccess(null);
    try {
      const out = await api<{ description: string; restored_at: string }>(
        `/api/v1/agencies/${agencyId}/properties/${propertyId}/description-history/${historyId}/restore`,
        { method: "POST", body: JSON.stringify({}) }
      );
      onDescriptionChange(out.description);
      setUndoId(null);
      setRestoreTarget(null);
      setSuccess("Descripción anterior restaurada.");
      await loadHistory();
    } catch (restoreError) {
      setError({
        action: "restore",
        message:
          errorCode(restoreError) === "description_changed_since_history"
            ? "La descripción cambió después. No la hemos sobrescrito; recarga el historial antes de intentarlo de nuevo."
            : "No hemos podido restaurar esa versión. La descripción actual sigue intacta.",
      });
    } finally {
      setOperation(null);
    }
  }

  function requestClose() {
    if (dirty) setConfirmClose(true);
    else onClose();
  }

  function discardLocalChangesAndClose() {
    if (draft) clearSessionDraft(agencyId, propertyId, draft.id);
    onClose();
  }

  return (
    <section
      className="mt-4 rounded-xl border border-selai-200 bg-selai-50/40 p-4"
      aria-labelledby={`description-editor-${propertyId}`}
      aria-busy={busy}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h4 id={`description-editor-${propertyId}`} className="font-semibold text-slate-900">
            Borrador de descripción · {propertyTitle}
          </h4>
          <p className="mt-1 text-sm text-slate-600">
            Generar o editar aquí no cambia la propiedad. Solo se sustituye al pulsar
            «Aplicar exactamente este texto».
          </p>
        </div>
        <button
          type="button"
          onClick={requestClose}
          disabled={busy}
          className="rounded-md px-2 py-1 text-sm text-slate-600 hover:bg-white disabled:opacity-50"
          aria-label="Cerrar editor de descripción"
        >
          Cerrar
        </button>
      </div>

      {confirmClose ? (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm" role="alert">
          <p className="font-medium text-amber-900">Hay cambios locales sin guardar.</p>
          <p className="mt-1 text-amber-800">
            El borrador guardado se conserva, pero perderás lo editado desde la última vez que
            pulsaste «Guardar borrador».
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => setConfirmClose(false)} className="rounded-md border border-amber-300 px-3 py-1.5 font-medium text-amber-900">
              Seguir editando
            </button>
            <button type="button" onClick={discardLocalChangesAndClose} className="rounded-md bg-amber-700 px-3 py-1.5 font-medium text-white">
              Cerrar sin guardar cambios locales
            </button>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">
          <p>{error.message}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {error.action === "load" ? <button type="button" onClick={() => void loadDraft()} className="font-semibold underline">Reintentar carga</button> : null}
            {error.action === "generate" ? <button type="button" onClick={() => void generate(false)} className="font-semibold underline">Reintentar generación</button> : null}
            {error.action === "save" ? <button type="button" onClick={() => void saveDraft()} className="font-semibold underline">Reintentar guardado</button> : null}
            {error.action === "apply" && !conflict ? <button type="button" onClick={() => void applyDraft(false)} className="font-semibold underline">Reintentar aplicación</button> : null}
            {error.action === "cancel" ? <button type="button" onClick={() => void cancelDraft()} className="font-semibold underline">Reintentar cancelación</button> : null}
            {error.action === "history" ? <button type="button" onClick={() => void loadHistory()} className="font-semibold underline">Reintentar historial</button> : null}
            {error.action === "restore" && restoreTarget ? <button type="button" onClick={() => void restore(restoreTarget)} className="font-semibold underline">Reintentar restauración</button> : null}
            {error.action === "restore" ? <button type="button" onClick={() => void loadHistory()} className="font-semibold underline">Recargar historial</button> : null}
          </div>
        </div>
      ) : null}

      {success ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800" role="status">
          <span>{success}</span>
          {undoId && canApply ? (
            <button type="button" onClick={() => void restore(undoId)} disabled={busy} className="font-semibold underline disabled:opacity-50">
              Deshacer ahora
            </button>
          ) : null}
        </div>
      ) : null}

      {!draft ? (
        <div className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-sm font-medium text-slate-800">Descripción actual</p>
          <p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">
            {currentDescription || "Esta propiedad todavía no tiene descripción."}
          </p>
          <button
            type="button"
            onClick={() => void generate(false)}
            disabled={busy}
            className="mt-4 rounded-md bg-selai-600 px-4 py-2 text-sm font-semibold text-white hover:bg-selai-700 disabled:opacity-50"
          >
            {operation === "generating" ? "Creando borrador…" : "Crear borrador desde las fotos"}
          </button>
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="rounded-lg border border-slate-200 bg-white p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Descripción actual · no cambia hasta aprobar
              </p>
              <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">
                {currentDescription || "Sin descripción"}
              </p>
            </div>
            <div className="rounded-lg border border-selai-200 bg-white p-3">
              <div className="flex items-center justify-between gap-2">
                <label htmlFor={`description-draft-${propertyId}`} className="text-xs font-semibold uppercase tracking-wide text-selai-700">
                  Borrador revisable
                </label>
                <span className="text-xs text-slate-500">{text.length}/{MAX_DESCRIPTION_LENGTH}</span>
              </div>
              <textarea
                id={`description-draft-${propertyId}`}
                value={text}
                onChange={(event) => {
                  setText(event.target.value.slice(0, MAX_DESCRIPTION_LENGTH));
                  setSuccess(null);
                }}
                rows={10}
                maxLength={MAX_DESCRIPTION_LENGTH}
                className="mt-2 w-full resize-y rounded-md border border-slate-300 px-3 py-2 text-sm leading-6 focus:border-selai-500 focus:outline-none focus:ring-2 focus:ring-selai-200"
              />
              {text !== draft.generated_description ? (
                <p className="mt-1 text-xs text-selai-700">Editado por una persona después de generarlo.</p>
              ) : null}
              <p
                className={`mt-1 text-xs ${dirty ? "font-medium text-amber-700" : "text-emerald-700"}`}
                role="status"
              >
                {dirty
                  ? "Hay cambios locales sin guardar. Aplicar usará exactamente este texto."
                  : "Borrador guardado y recuperable."}
              </p>
            </div>
          </div>

          {draft.highlights.length > 0 || draft.weaknesses.length > 0 ? (
            <div className="mt-3 grid gap-3 text-sm md:grid-cols-2">
              <div className="rounded-lg bg-emerald-50 p-3">
                <p className="font-semibold text-emerald-800">Puntos fuertes detectados</p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-emerald-900">
                  {draft.highlights.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
                </ul>
              </div>
              <div className="rounded-lg bg-amber-50 p-3">
                <p className="font-semibold text-amber-800">Aspectos que conviene revisar</p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-amber-900">
                  {draft.weaknesses.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
                </ul>
              </div>
            </div>
          ) : null}

          {conflict ? (
            <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
              <p className="font-semibold text-amber-900">La versión actual cambió mientras revisabas.</p>
              <p className="mt-1 text-amber-800">
                Arriba ves la versión nueva. Solo continúa si has comprobado que este borrador debe sustituirla.
              </p>
              {canApply ? (
                <button type="button" onClick={() => void applyDraft(true)} disabled={busy} className="mt-3 rounded-md bg-amber-700 px-3 py-2 font-semibold text-white disabled:opacity-50">
                  Sustituirla con este texto revisado
                </button>
              ) : null}
            </div>
          ) : null}

          {confirmRegenerate ? (
            <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
              <p className="font-medium text-amber-900">El borrador actual se sustituirá por uno nuevo.</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" onClick={() => setConfirmRegenerate(false)} className="rounded-md border border-amber-300 px-3 py-1.5 font-medium text-amber-900">Conservar este</button>
                <button type="button" onClick={() => void generate(true)} className="rounded-md bg-amber-700 px-3 py-1.5 font-medium text-white">Generar uno nuevo</button>
              </div>
            </div>
          ) : null}

          {confirmCancel ? (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
              <p className="font-medium text-red-900">Se borrará este borrador, no la descripción actual.</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" onClick={() => setConfirmCancel(false)} className="rounded-md border border-red-300 px-3 py-1.5 font-medium text-red-900">Conservar borrador</button>
                <button type="button" onClick={() => void cancelDraft()} className="rounded-md bg-red-700 px-3 py-1.5 font-medium text-white">Borrar borrador</button>
              </div>
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void saveDraft()}
              disabled={busy || !dirty || !text.trim()}
              className="rounded-md border border-selai-300 bg-white px-3 py-2 text-sm font-semibold text-selai-700 hover:bg-selai-50 disabled:opacity-50"
            >
              {operation === "saving" ? "Guardando…" : "Guardar borrador"}
            </button>
            {canApply ? (
              <button
                type="button"
                onClick={() => void applyDraft(false)}
                disabled={busy || !text.trim() || conflict !== null}
                className="rounded-md bg-selai-600 px-3 py-2 text-sm font-semibold text-white hover:bg-selai-700 disabled:opacity-50"
              >
                {operation === "applying" ? "Aplicando…" : "Aplicar exactamente este texto"}
              </button>
            ) : (
              <p className="basis-full text-sm text-slate-600">
                Puedes editar y guardar el borrador. Una persona propietaria o administradora debe aplicar el texto final.
              </p>
            )}
            <button type="button" onClick={() => setConfirmRegenerate(true)} disabled={busy} className="rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-white disabled:opacity-50">
              Generar otro
            </button>
            <button type="button" onClick={() => setConfirmCancel(true)} disabled={busy} className="rounded-md px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50">
              Cancelar borrador
            </button>
          </div>
        </>
      )}

      {hasInitialHistory || history?.length ? (
        <div className="mt-4 border-t border-slate-200 pt-3">
          <button
            type="button"
            onClick={() => historyOpen ? setHistoryOpen(false) : void loadHistory()}
            disabled={busy}
            aria-expanded={historyOpen}
            className="text-sm font-semibold text-slate-700 underline disabled:opacity-50"
          >
            {historyOpen ? "Ocultar historial" : "Ver historial de descripciones"}
          </button>
          {historyOpen && history ? (
            <ul className="mt-3 space-y-2">
              {history.map((item) => (
                <li key={item.id} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="text-xs text-slate-500">Sustituida el {new Date(item.replaced_at).toLocaleString("es-ES")}</p>
                      <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-slate-700">{item.description || "Sin descripción"}</p>
                    </div>
                    {item.restored_at ? <span className="text-xs text-emerald-700">Restaurada</span> : item.can_restore && canApply ? (
                      <button type="button" onClick={() => void restore(item.id)} disabled={busy} className="rounded-md border border-slate-300 px-2 py-1 font-medium text-slate-700 disabled:opacity-50">Restaurar</button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

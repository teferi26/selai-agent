// Client half of the catalog: receives the initial data from the server
// component and handles search, quick status changes (optimistic PATCH)
// and the "Describir con IA" interaction.
"use client";

import { useMemo, useState } from "react";
import Image from "next/image";
import { api } from "@/lib/api";
import DescriptionDraftEditor from "./DescriptionDraftEditor";
import ImportCatalog from "./ImportCatalog";

export type Property = {
  id: string;
  reference: string;
  title: string;
  city: string;
  neighborhood: string | null;
  price: number;
  bedrooms: number | null;
  area_sqm: number | null;
  status: string;
  description: string;
  image_urls: string[];
  has_description_draft: boolean;
  has_description_history: boolean;
};

// Literales reales del enum del backend (PropertyStatus).
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "available", label: "Disponible" },
  { value: "reserved", label: "Reservada" },
  { value: "sold", label: "Vendida" },
  { value: "rented", label: "Alquilada" },
];

const EXTRA_STATUS_LABEL: Record<string, string> = {
  draft: "Borrador",
  archived: "Archivada",
};

/** (62) Campos que la IA necesita para responder bien — solo los que existen
 * en PropertyOut: habitaciones, m² y descripción. */
function missingFields(p: Property, description = p.description): string[] {
  const missing: string[] = [];
  if (p.bedrooms === null) missing.push("habitaciones");
  if (p.area_sqm === null) missing.push("m²");
  if (!description.trim()) missing.push("descripción");
  return missing;
}

export default function PropertiesClient({
  agencyId,
  initialItems,
  canApplyDescriptions,
}: {
  agencyId: string;
  initialItems: Property[];
  canApplyDescriptions: boolean;
}) {
  // No local copy of the list: after a CSV import we router.refresh() and the
  // server component re-renders with the fresh data. Status changes are the
  // exception — they live in a small optimistic overlay map.
  const items = initialItems;
  const [query, setQuery] = useState("");
  const [statusOverride, setStatusOverride] = useState<Record<string, string>>({});
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusSaving, setStatusSaving] = useState<string | null>(null);
  const [activeDescriptionId, setActiveDescriptionId] = useState<string | null>(null);
  const [descriptionOverride, setDescriptionOverride] = useState<Record<string, string>>({});
  const [draftOverride, setDraftOverride] = useState<Record<string, boolean>>({});
  const [historyOverride, setHistoryOverride] = useState<Record<string, boolean>>({});

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (p) =>
        p.id === activeDescriptionId ||
        [p.title, p.reference, p.city, p.neighborhood ?? ""]
          .join(" ")
          .toLowerCase()
          .includes(q)
    );
  }, [activeDescriptionId, items, query]);

  async function changeStatus(p: Property, next: string) {
    const prev = statusOverride[p.id] ?? p.status;
    if (next === prev) return;
    setStatusError(null);
    setStatusSaving(p.id);
    // Optimista: pintamos el cambio ya y revertimos si el PATCH falla.
    setStatusOverride((m) => ({ ...m, [p.id]: next }));
    try {
      await api<{ ok: boolean; status: string }>(
        `/api/v1/agencies/${agencyId}/properties/${p.id}`,
        { method: "PATCH", body: JSON.stringify({ status: next }) }
      );
    } catch {
      setStatusOverride((m) => ({ ...m, [p.id]: prev }));
      setStatusError("No se pudo cambiar el estado. Inténtalo de nuevo.");
    } finally {
      setStatusSaving(null);
    }
  }

  return (
    <div>
      <header className="mb-6 flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Catálogo</h2>
        <span className="text-sm text-slate-500">
          {filtered.length === items.length
            ? `${items.length} propiedades`
            : `${filtered.length} de ${items.length} propiedades`}
        </span>
      </header>

      {/* (169) Las referencias que ya tenemos permiten avisar en la vista
          previa de qué filas actualizarán en vez de crear. */}
      <ImportCatalog
        agencyId={agencyId}
        existingReferences={items.map((p) => p.reference)}
      />

      {items.length > 0 && (
        <div className="mb-4">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar por título, referencia, zona o ciudad…"
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
            aria-label="Buscar propiedades"
          />
        </div>
      )}

      {statusError && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {statusError}
        </div>
      )}

      <div className="grid gap-4">
        {filtered.map((p) => {
          const status = statusOverride[p.id] ?? p.status;
          const description = descriptionOverride[p.id] ?? p.description;
          const hasDescriptionDraft =
            draftOverride[p.id] ?? p.has_description_draft;
          const hasDescriptionHistory =
            historyOverride[p.id] ?? p.has_description_history;
          const missing = missingFields(p, description);
          const anotherDescriptionEditorIsOpen =
            activeDescriptionId !== null && activeDescriptionId !== p.id;
          const dimmed = status === "sold" || status === "rented";
          const hasKnownStatus = STATUS_OPTIONS.some((o) => o.value === status);
          return (
            <div
              key={p.id}
              className={`rounded-lg border border-slate-200 bg-white p-4 transition-opacity ${
                dimmed ? "opacity-55" : ""
              }`}
            >
              <div className="flex items-start gap-4">
                {p.image_urls[0] ? (
                  <Image
                    src={p.image_urls[0]}
                    alt={`Foto principal de ${p.title}`}
                    width={128}
                    height={96}
                    unoptimized
                    className="w-32 h-24 object-cover rounded"
                  />
                ) : (
                  <div
                    className="flex h-24 w-32 items-center justify-center rounded bg-selai-50 text-3xl font-semibold text-selai-300"
                    aria-label="Sin foto"
                  >
                    {(p.title || "?").charAt(0).toUpperCase()}
                  </div>
                )}
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium">{p.title}</h3>
                    <span className="text-xs text-slate-500">{p.reference}</span>
                    {missing.length > 0 && (
                      <span
                        className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800"
                        title={`La IA no podrá responder preguntas sobre esto. Falta: ${missing.join(", ")}`}
                      >
                        Faltan datos: {missing.length}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-slate-600">
                    {p.bedrooms ?? "?"} hab · {p.area_sqm ?? "?"} m² ·{" "}
                    {p.neighborhood || p.city} · €{p.price.toLocaleString()}
                  </p>
                  <p className="text-sm text-slate-700 mt-1 line-clamp-2">
                    {description || (
                      <span className="italic text-slate-400">
                        Sin descripción. Usa la IA para generarla desde las fotos.
                      </span>
                    )}
                  </p>
                </div>
                <div className="flex flex-col items-stretch gap-1.5">
                  <select
                    value={status}
                    onChange={(e) => changeStatus(p, e.target.value)}
                    disabled={statusSaving === p.id}
                    className="rounded border border-slate-200 bg-white px-2 py-1 text-xs disabled:opacity-50"
                    aria-label="Estado de la propiedad"
                  >
                    {!hasKnownStatus && (
                      <option value={status}>
                        {EXTRA_STATUS_LABEL[status] ?? status}
                      </option>
                    )}
                    {STATUS_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => setActiveDescriptionId(p.id)}
                    disabled={
                      anotherDescriptionEditorIsOpen ||
                      (p.image_urls.length === 0 && !hasDescriptionDraft)
                    }
                    className="rounded border border-selai-200 px-2 py-1 text-xs font-medium text-selai-700 hover:bg-selai-50 disabled:opacity-50"
                    title={
                      anotherDescriptionEditorIsOpen
                        ? "Cierra primero el borrador que tienes abierto"
                        : p.image_urls.length === 0 && !hasDescriptionDraft
                        ? "Añade al menos una foto para crear el borrador"
                        : undefined
                    }
                  >
                    {hasDescriptionDraft ? "Continuar borrador" : "Describir con IA"}
                  </button>
                </div>
              </div>

              {activeDescriptionId === p.id ? (
                <DescriptionDraftEditor
                  agencyId={agencyId}
                  propertyId={p.id}
                  propertyTitle={p.title}
                  currentDescription={description}
                  hasInitialDraft={hasDescriptionDraft}
                  hasInitialHistory={hasDescriptionHistory}
                  canApply={canApplyDescriptions}
                  onClose={() => setActiveDescriptionId(null)}
                  onDescriptionChange={(next) =>
                    setDescriptionOverride((current) => ({ ...current, [p.id]: next }))
                  }
                  onDraftStateChange={(hasDraft) =>
                    setDraftOverride((current) => ({ ...current, [p.id]: hasDraft }))
                  }
                  onHistoryStateChange={(hasHistory) =>
                    setHistoryOverride((current) => ({ ...current, [p.id]: hasHistory }))
                  }
                />
              ) : null}
            </div>
          );
        })}
        {filtered.length === 0 && items.length > 0 && (
          <p className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
            Ninguna propiedad coincide con «{query}».
          </p>
        )}
      </div>
    </div>
  );
}

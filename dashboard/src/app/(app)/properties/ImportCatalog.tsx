// Collapsible "Importar catálogo" section for /properties.
// (63) Antes de subir, el CSV se parsea en el navegador (separador , o ;) y
// se muestra una vista previa de las 5 primeras filas; el POST solo se lanza
// al pulsar "Confirmar e importar". (40) Pestañas de ayuda por origen del
// archivo (Excel propio / Inmovilla / Witei / Otro CRM).
// (169) El backend hace upsert por referencia: la vista previa marca las
// filas que van a actualizar una propiedad existente y el resultado
// distingue nuevas de actualizadas.
"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

/** Respuesta de POST /properties/import. `created`/`updated` son opcionales
 * por si el backend desplegado todavía es anterior al upsert. */
type ImportOut = {
  imported: number;
  created?: number;
  updated?: number;
  skipped: number;
  errors: string[];
};

type Preview = {
  columns: string[];
  rows: string[][];
  /** Paralelo a `rows`: la fila actualiza una propiedad que ya existe. */
  rowUpdates: boolean[];
  totalRows: number;
  totalUpdates: number;
  hasReferenceColumn: boolean;
  separator: "," | ";";
};

/** Mismas reglas que `normalize_header` del backend: sin acentos, minúsculas
 * y solo alfanuméricos. Así la columna de referencia se detecta igual aquí. */
function normalizeHeader(name: string): string {
  return (name || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

const REFERENCE_HEADERS = new Set([
  "referencia",
  "ref",
  "externalref",
  "reference",
  "codigo",
]);

function findReferenceColumn(headers: string[]): number {
  return headers.findIndex((h) => REFERENCE_HEADERS.has(normalizeHeader(h)));
}

/** Separador más frecuente en la primera línea, ignorando el interior de comillas. */
function detectSeparator(line: string): "," | ";" {
  let inQuotes = false;
  let commas = 0;
  let semis = 0;
  for (const ch of line) {
    if (ch === '"') inQuotes = !inQuotes;
    else if (!inQuotes && ch === ",") commas++;
    else if (!inQuotes && ch === ";") semis++;
  }
  return semis > commas ? ";" : ",";
}

/** Parser CSV mínimo con soporte de comillas ("" = comilla escapada). */
function parseCsv(text: string, sep: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === sep) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  if (cell !== "" || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  // Fuera filas totalmente vacías (línea final, saltos sueltos).
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

const HELP_TABS: { key: string; label: string; body: React.ReactNode }[] = [
  {
    key: "excel",
    label: "Excel propio",
    body: (
      <>
        Si llevas tus propiedades en un Excel, abre el archivo y usa{" "}
        <strong>Archivo → Guardar como → CSV</strong>. Comprueba que la primera fila
        tiene los nombres de las columnas (referencia, título, precio…) y sube el
        archivo aquí. Puedes apoyarte en nuestra plantilla para ver qué columnas
        entiende selai.
      </>
    ),
  },
  {
    key: "inmovilla",
    label: "Inmovilla",
    body: (
      <>
        Entra en tu cuenta de Inmovilla y busca la opción de{" "}
        <strong>exportar cartera / propiedades</strong> (los menús cambian según la
        versión). Exporta a Excel, guárdalo como CSV y súbelo aquí. Si no encuentras
        la opción, su soporte te lo genera en un momento.
      </>
    ),
  },
  {
    key: "witei",
    label: "Witei",
    body: (
      <>
        En Witei, busca la opción de <strong>exportar propiedades</strong> desde el
        listado de inmuebles (los menús cambian según la versión). Descarga el Excel,
        guárdalo como CSV y súbelo aquí.
      </>
    ),
  },
  {
    key: "otro",
    label: "Otro CRM",
    body: (
      <>
        Casi todos los CRM inmobiliarios (Idealista Tools, Inmogesco, Mobilia…) tienen
        una opción de <strong>Exportar cartera/propiedades</strong> a Excel o CSV.
        Búscala en el listado de inmuebles o en ajustes. Si solo te da Excel,
        guárdalo como CSV y súbelo aquí. ¿No la encuentras? Escríbenos a
        hola@selai.es y te echamos una mano.
      </>
    ),
  },
];

export default function ImportCatalog({
  agencyId,
  defaultOpen = false,
  existingReferences,
}: {
  agencyId: string;
  /** El wizard de puesta en marcha lo muestra ya desplegado. */
  defaultOpen?: boolean;
  /** Referencias del catálogo actual: sirven para avisar en la vista previa
   * de qué filas van a actualizar en vez de crear. Si no llega, no se avisa. */
  existingReferences?: string[];
}) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(defaultOpen);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<ImportOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [helpTab, setHelpTab] = useState<string | null>(null);

  // (63) Al elegir archivo: parsear en cliente y mostrar vista previa.
  async function onFileChange() {
    setError(null);
    setResult(null);
    setPreview(null);
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const firstLine = text.split(/\r?\n/, 1)[0] ?? "";
      const separator = detectSeparator(firstLine);
      const rows = parseCsv(text, separator);
      if (rows.length === 0) {
        setError("El archivo está vacío. Comprueba que has exportado bien el CSV.");
        return;
      }
      const [header, ...data] = rows;
      const columns = header.map((c) => c.trim());

      // (169) ¿Qué filas actualizarán algo que ya está en el catálogo?
      // Comparamos la referencia tal cual (recortada), igual que el backend.
      const refColumn = findReferenceColumn(columns);
      const known = new Set(existingReferences ?? []);
      const updates = (row: string[]) => {
        if (refColumn < 0 || known.size === 0) return false;
        const ref = (row[refColumn] ?? "").trim();
        return ref !== "" && known.has(ref);
      };

      setPreview({
        columns,
        rows: data.slice(0, 5),
        rowUpdates: data.slice(0, 5).map(updates),
        totalRows: data.length,
        totalUpdates: data.filter(updates).length,
        hasReferenceColumn: refColumn >= 0,
        separator,
      });
    } catch {
      setError("No se pudo leer el archivo. Comprueba que es un CSV e inténtalo de nuevo.");
    }
  }

  async function confirmImport() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setError("Elige primero un archivo CSV.");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const out = await api<ImportOut>(
        `/api/v1/agencies/${agencyId}/properties/import`,
        { method: "POST", body: fd }
      );
      setResult(out);
      setPreview(null);
      if (fileRef.current) fileRef.current.value = "";
      if (out.imported > 0) router.refresh();
    } catch {
      setError("No se pudo importar el archivo. Comprueba que es un CSV e inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-6 rounded-xl border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <span className="font-medium">Importar catálogo</span>
          <p className="mt-0.5 text-sm text-slate-500">
            Sube tus propiedades de golpe con un archivo CSV.
          </p>
        </div>
        <span className="text-slate-400" aria-hidden>
          {open ? "▴" : "▾"}
        </span>
      </button>

      {open && (
        <div className="border-t border-slate-200 px-5 py-4">
          <p className="mb-3 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
            Elige el archivo y te enseñamos una vista previa antes de importar nada. Se
            importan las filas correctas; las que tengan errores te las listamos para
            arreglarlas y volver a subir solo esas.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".csv,text/csv"
              onChange={onFileChange}
              className="block text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-selai-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-selai-700 hover:file:bg-selai-100"
            />
            <a
              href="/plantilla-catalogo.csv"
              download
              className="text-sm text-selai-600 underline hover:text-selai-700"
            >
              Descargar plantilla CSV
            </a>
          </div>

          {/* (63) Vista previa de las 5 primeras filas */}
          {preview && (
            <div className="mt-4 rounded-md border border-slate-200">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
                <p className="text-sm text-slate-700">
                  <span className="font-medium">Vista previa</span> — {preview.totalRows}{" "}
                  fila{preview.totalRows === 1 ? "" : "s"} detectada
                  {preview.totalRows === 1 ? "" : "s"}, {preview.columns.length} columnas
                  (separador «{preview.separator}»). Aquí ves las {Math.min(5, preview.totalRows)}{" "}
                  primeras.
                  {/* (169) Aviso de upsert: nada se duplica. */}
                  {preview.hasReferenceColumn && preview.totalUpdates > 0 && (
                    <>
                      {" "}
                      <span className="text-blue-700">
                        {preview.totalUpdates} fila
                        {preview.totalUpdates === 1 ? "" : "s"} coincide
                        {preview.totalUpdates === 1 ? "" : "n"} con propiedades que ya
                        tienes: se actualizarán en vez de duplicarse.
                      </span>
                    </>
                  )}
                  {!preview.hasReferenceColumn && (
                    <>
                      {" "}
                      <span className="text-amber-700">
                        El archivo no trae columna de referencia, así que todas las
                        filas se darán de alta como propiedades nuevas.
                      </span>
                    </>
                  )}
                </p>
                <button
                  type="button"
                  onClick={confirmImport}
                  disabled={busy}
                  className="rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
                >
                  {busy ? "Importando…" : "Confirmar e importar"}
                </button>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                    <tr>
                      {preview.columns.map((c, i) => (
                        <th key={i} className="whitespace-nowrap px-3 py-2 text-left">
                          {c || <span className="normal-case italic">(sin nombre)</span>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {preview.rows.map((r, i) => (
                      <tr key={i} className={preview.rowUpdates[i] ? "bg-blue-50/60" : ""}>
                        {preview.columns.map((_, j) => (
                          <td
                            key={j}
                            className="max-w-[16rem] truncate whitespace-nowrap px-3 py-1.5 text-slate-700"
                            title={r[j]}
                          >
                            {j === 0 && preview.rowUpdates[i] && (
                              <span className="mr-1.5 rounded bg-blue-100 px-1.5 py-0.5 text-[11px] font-medium text-blue-800">
                                actualiza
                              </span>
                            )}
                            {r[j]?.trim() || <span className="text-slate-300">—</span>}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {error && (
            <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          {result && (
            <div className="mt-3 space-y-2">
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                {result.created !== undefined && result.updated !== undefined ? (
                  <>
                    {result.created} nueva{result.created === 1 ? "" : "s"},{" "}
                    {result.updated} actualizada{result.updated === 1 ? "" : "s"},{" "}
                    {result.skipped} saltada{result.skipped === 1 ? "" : "s"}.
                  </>
                ) : (
                  <>
                    {result.imported} importada{result.imported === 1 ? "" : "s"},{" "}
                    {result.skipped} saltada{result.skipped === 1 ? "" : "s"}.
                  </>
                )}
              </div>
              {result.errors.length > 0 && (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-900">
                  <p className="flex items-center gap-1.5 font-medium">
                    <span aria-hidden>⚠️</span>
                    Filas con errores ({result.errors.length}) — arréglalas y vuelve a
                    subir solo esas:
                  </p>
                  <ul className="mt-1.5 space-y-0.5 pl-6">
                    {result.errors.map((err, i) => (
                      <li key={i} className="list-disc">
                        {err}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* (40) Ayuda por origen del archivo */}
          <div className="mt-4 rounded-md border border-selai-100 bg-selai-50 px-4 py-3 text-sm text-slate-700">
            <p className="font-medium text-slate-800">¿De dónde saco el archivo?</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {HELP_TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => setHelpTab((cur) => (cur === t.key ? null : t.key))}
                  className={`rounded-full px-3 py-1 text-sm transition-colors ${
                    helpTab === t.key
                      ? "bg-selai-600 text-white"
                      : "border border-selai-200 bg-white text-slate-600 hover:border-selai-400"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            {helpTab && (
              <p className="mt-3 leading-relaxed">
                {HELP_TABS.find((t) => t.key === helpTab)?.body}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

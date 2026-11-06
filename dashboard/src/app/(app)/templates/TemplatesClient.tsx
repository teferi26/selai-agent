// Client half de /templates: listado + formulario de creación
// (POST /templates) + (57) ejemplos estilo Meta listos para usar cuyo botón
// "Usar" rellena el formulario.
"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export type Template = {
  id: string;
  name: string;
  body: string;
  channel: string;
  language: string;
  description: string;
};

type Draft = {
  name: string;
  channel: string;
  language: string;
  description: string;
  body: string;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  channel: "whatsapp",
  language: "es",
  description: "",
  body: "",
};

// (57) Plantillas pre-redactadas al estilo Meta ({{1}}, {{2}}… numerados).
const EXAMPLES: { title: string; hint: string; draft: Draft }[] = [
  {
    title: "Recordatorio de visita",
    hint: "{{1}} nombre · {{2}} fecha · {{3}} hora · {{4}} dirección",
    draft: {
      name: "recordatorio_visita",
      channel: "whatsapp",
      language: "es",
      description: "Recuerda al cliente la visita agendada.",
      body: "Hola {{1}}, te recordamos tu visita del {{2}} a las {{3}} en {{4}}. Si no puedes venir, responde a este mensaje y buscamos otro momento.",
    },
  },
  {
    title: "Seguimiento post-visita",
    hint: "{{1}} nombre · {{2}} propiedad",
    draft: {
      name: "seguimiento_post_visita",
      channel: "whatsapp",
      language: "es",
      description: "Pregunta qué le pareció la visita y reabre la conversación.",
      body: "Hola {{1}}, ¡gracias por visitar {{2}}! ¿Qué te pareció? Si quieres verla otra vez o ver opciones parecidas, respóndeme y lo organizamos.",
    },
  },
  {
    title: "Aviso de nueva propiedad",
    hint: "{{1}} nombre · {{2}} propiedad · {{3}} precio",
    draft: {
      name: "aviso_nueva_propiedad",
      channel: "whatsapp",
      language: "es",
      description: "Avisa de una propiedad nueva que encaja con su búsqueda.",
      body: "Hola {{1}}, acaba de entrar una propiedad que encaja con lo que buscas: {{2}} por {{3}}. ¿Te mando fotos o agendamos una visita?",
    },
  },
];

export default function TemplatesClient({
  agencyId,
  rows,
}: {
  agencyId: string;
  rows: Template[];
}) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function applyExample(d: Draft) {
    setDraft(d);
    setCreated(null);
    setError(null);
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setCreated(null);
    try {
      await api<Template>(`/api/v1/agencies/${agencyId}/templates`, {
        method: "POST",
        body: JSON.stringify(draft),
      });
      setCreated(draft.name);
      setDraft(EMPTY_DRAFT);
      router.refresh();
    } catch {
      setError("No se pudo crear la plantilla. Inténtalo de nuevo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <header className="mb-6 flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Plantillas de mensaje</h2>
        <span className="text-xs text-slate-500">
          Usa <code>{"{{1}}"}</code>, <code>{"{{2}}"}</code>… como huecos numerados
          (estilo Meta).
        </span>
      </header>

      {/* (57) Ejemplos listos para usar */}
      <section className="mb-8">
        <h3 className="text-lg font-medium">Ejemplos listos para usar</h3>
        <p className="mb-3 mt-1 text-sm text-slate-500">
          Tres plantillas ya redactadas. Pulsa «Usar», ajusta lo que quieras y créala.
        </p>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {EXAMPLES.map((ex) => (
            <div
              key={ex.draft.name}
              className="flex flex-col rounded-xl border border-slate-200 bg-white p-4"
            >
              <h4 className="font-medium">{ex.title}</h4>
              <p className="mt-0.5 text-xs text-slate-400">{ex.hint}</p>
              <p className="mt-2 flex-1 whitespace-pre-wrap rounded bg-slate-50 p-2.5 text-sm text-slate-700">
                {ex.draft.body}
              </p>
              <button
                type="button"
                onClick={() => applyExample(ex.draft)}
                className="mt-3 self-start rounded-md bg-selai-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-selai-700"
              >
                Usar
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* Formulario de creación */}
      <section className="mb-8">
        <h3 className="text-lg font-medium">Crear plantilla</h3>
        <form
          ref={formRef}
          onSubmit={create}
          className="mt-3 scroll-mt-8 rounded-xl border border-slate-200 bg-white p-5"
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label htmlFor="template-name" className="mb-1 block text-sm font-medium">
                Nombre
              </label>
              <input
                id="template-name"
                name="name"
                required
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="Ej. recordatorio_visita"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label htmlFor="template-channel" className="mb-1 block text-sm font-medium">
                Canal
              </label>
              <select
                id="template-channel"
                name="channel"
                value={draft.channel}
                onChange={(e) => setDraft({ ...draft, channel: e.target.value })}
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
              >
                <option value="whatsapp">WhatsApp</option>
                <option value="email">Email</option>
                <option value="sms">SMS</option>
              </select>
            </div>
            <div>
              <label htmlFor="template-language" className="mb-1 block text-sm font-medium">
                Idioma
              </label>
              <select
                id="template-language"
                name="language"
                value={draft.language}
                onChange={(e) => setDraft({ ...draft, language: e.target.value })}
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
              >
                <option value="es">Español</option>
                <option value="en">Inglés</option>
              </select>
            </div>
          </div>

          <div className="mt-4">
            <label htmlFor="template-description" className="mb-1 block text-sm font-medium">
              Descripción <span className="font-normal text-slate-400">(opcional)</span>
            </label>
            <input
              id="template-description"
              name="description"
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              placeholder="Para qué sirve esta plantilla"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>

          <div className="mt-4">
            <label htmlFor="template-message" className="mb-1 block text-sm font-medium">
              Mensaje
            </label>
            <textarea
              id="template-message"
              name="body"
              required
              rows={4}
              value={draft.body}
              onChange={(e) => setDraft({ ...draft, body: e.target.value })}
              placeholder="Hola {{1}}, …"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>

          {error && (
            <div
              role="alert"
              className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
            >
              {error}
            </div>
          )}
          {created && (
            <p role="status" className="mt-3 text-sm text-emerald-700">
              Plantilla «{created}» creada ✓
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="mt-4 rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:opacity-60"
          >
            {busy ? "Creando…" : "Crear plantilla"}
          </button>
        </form>
      </section>

      {/* Listado */}
      <div className="grid gap-4">
        {rows.map((t) => (
          <div key={t.id} className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-medium">{t.name}</h3>
              <div className="flex gap-2 text-xs">
                <span className="bg-slate-100 px-1.5 py-0.5 rounded">{t.channel}</span>
                <span className="bg-slate-100 px-1.5 py-0.5 rounded">{t.language}</span>
              </div>
            </div>
            {t.description && (
              <p className="text-sm text-slate-500 mb-2">{t.description}</p>
            )}
            <pre className="whitespace-pre-wrap text-sm text-slate-700 bg-slate-50 p-3 rounded">
              {t.body}
            </pre>
          </div>
        ))}
        {rows.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-slate-500">
            Aún no tienes plantillas. Empieza por uno de los ejemplos de arriba.
          </div>
        )}
      </div>
    </div>
  );
}

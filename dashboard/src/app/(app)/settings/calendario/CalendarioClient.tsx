// Mitad cliente de /settings/calendario. La conexión en sí vive en
// CalendarConnect (compartido con la Puesta en marcha); aquí ponemos el
// marco de la página, el estado en la cabecera y lo que hay que contarle al
// cliente sobre qué guardamos y cómo quitarlo.
"use client";

import { useState } from "react";
import CalendarConnect, {
  type CalendarStatus,
  calendarStateLabel,
} from "../../setup/CalendarConnect";

function Card({
  title,
  status,
  children,
}: {
  title: string;
  status?: { label: string; tone: "ok" | "warn" | "off" };
  children: React.ReactNode;
}) {
  const toneClass =
    status?.tone === "ok"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : status?.tone === "warn"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : "border-slate-200 bg-slate-50 text-slate-500";
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">{title}</h3>
        {status && (
          <span
            className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs ${toneClass}`}
          >
            {status.label}
          </span>
        )}
      </div>
      {children}
    </section>
  );
}

export default function CalendarioClient({ agencyId }: { agencyId: string }) {
  const [status, setStatus] = useState<CalendarStatus | null>(null);

  return (
    <div className="max-w-3xl">
      <header className="mb-8">
        <h2 className="text-2xl font-semibold">Calendario</h2>
        <p className="text-sm text-slate-500">
          Conecta Google Calendar y cada visita agendada aparecerá también ahí.
        </p>
      </header>

      <Card title="Google Calendar" status={calendarStateLabel(status)}>
        <CalendarConnect agencyId={agencyId} onChange={setStatus} />
      </Card>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5">
        <h3 className="mb-2 font-medium">Qué guardamos y qué no</h3>
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-600">
          <li>
            Pedimos el permiso mínimo para crear eventos. No leemos tu agenda ni
            los eventos que ya tienes.
          </li>
          <li>
            El evento lleva el nombre del cliente, la propiedad, la hora en tu
            zona horaria y el teléfono al que llamar.
          </li>
          <li>
            El permiso que nos da Google se guarda en tu cuenta de selai, no se
            muestra en ninguna pantalla y nunca sale de nuestros servidores.
          </li>
          <li>
            Si Google falla, la visita se agenda igual: lo único que se pierde es
            la copia en el calendario, y aquí te avisamos.
          </li>
          <li>
            Puedes desconectarlo desde esta página, y retirar el permiso por
            completo desde{" "}
            <a
              href="https://myaccount.google.com/permissions"
              target="_blank"
              rel="noreferrer noopener"
              className="text-selai-700 underline"
            >
              los permisos de tu cuenta de Google
            </a>
            .
          </li>
        </ul>
      </section>
    </div>
  );
}

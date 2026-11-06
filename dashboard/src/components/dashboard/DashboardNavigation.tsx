"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export type SetupProgress = { done: number; total: number };

const NAV_GROUPS: ReadonlyArray<{
  label: string;
  items: ReadonlyArray<readonly [string, string]>;
}> = [
  {
    label: "Operación",
    items: [
      ["Bandeja", "/inbox"],
      ["Llamadas", "/calls"],
      ["Visitas", "/visitas"],
      ["Leads", "/leads"],
      ["Catálogo", "/properties"],
      ["Ofertas", "/offers"],
    ],
  },
  {
    label: "Inteligencia artificial",
    items: [
      ["Probar mi IA", "/probar"],
      ["Voz del agente", "/settings/voz"],
      ["Comportamiento IA", "/settings/ia"],
      ["Plantillas", "/templates"],
    ],
  },
  {
    label: "Análisis",
    items: [
      ["Métricas", "/kpis"],
      ["Embudo", "/funnel"],
    ],
  },
  {
    label: "Configuración",
    items: [
      ["Canales", "/settings/canales"],
      ["Equipo", "/settings/equipo"],
      ["Facturación", "/billing"],
    ],
  },
];

function isCurrentPath(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function SetupLink({ progress, current }: { progress: SetupProgress | null; current: boolean }) {
  return (
    <Link
      href="/setup"
      aria-current={current ? "page" : undefined}
      className={`mb-3 flex flex-col rounded px-2 py-1.5 font-medium hover:bg-slate-100 ${
        current ? "bg-selai-50 text-selai-700" : ""
      } ${
        progress && progress.done < progress.total
          ? "border border-accent/40 bg-accent/5"
          : ""
      }`}
    >
      <span className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />
        {!progress || progress.done < progress.total ? "Ponlo en marcha" : "Puesta en marcha"}
      </span>
      {progress && (
        <span className="mt-1.5 block">
          <span className="block text-[11px] font-normal text-slate-500">
            {progress.done === progress.total
              ? "¡Completada!"
              : `${progress.done} de ${progress.total}`}
          </span>
          <span className="mt-0.5 block h-1.5 rounded-full bg-slate-100">
            <span
              className={`block h-1.5 rounded-full ${
                progress.done === progress.total ? "bg-emerald-500" : "bg-accent"
              }`}
              style={{ width: `${Math.round((progress.done / progress.total) * 100)}%` }}
            />
          </span>
        </span>
      )}
    </Link>
  );
}

export function DashboardNavigation({ progress }: { progress: SetupProgress | null }) {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col text-sm" aria-label="Navegación principal">
      <SetupLink progress={progress} current={isCurrentPath(pathname, "/setup")} />
      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="mb-4 last:mb-0">
          <h2 className="mb-1 px-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            {group.label}
          </h2>
          <ul className="space-y-0.5">
            {group.items.map(([label, href]) => {
              const current = isCurrentPath(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={current ? "page" : undefined}
                    className={`block rounded px-2 py-1.5 hover:bg-slate-100 ${
                      current ? "bg-selai-50 font-medium text-selai-700" : ""
                    }`}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

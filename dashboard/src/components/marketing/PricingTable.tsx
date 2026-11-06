"use client";

// Collapsible plan comparison table (backlog #17). Lives right under the
// pricing grid; collapsed by default so it doesn't bury the CTAs.
import { useEffect, useRef, useState } from "react";

import { trackFunnel } from "./analytics";
import { Reveal } from "./animated";
import { ICheck, IPlus } from "./icons";
import { COMPARISON_GROUPS, PLAN_PRICES } from "./pricing-data";
import type { ComparisonCell } from "./pricing-data";

function Cell({ value }: { value: ComparisonCell }) {
  if (value === true) {
    return (
      <span
        className="inline-flex w-5 h-5 rounded-full bg-selai-100 text-selai-600 items-center justify-center"
        role="img"
        aria-label="Incluido"
      >
        <ICheck size={12} />
      </span>
    );
  }
  if (value === false) {
    return (
      <span className="text-ink-300" role="img" aria-label="No incluido">
        —
      </span>
    );
  }
  return <span className="text-[13px] font-medium text-ink-700">{value}</span>;
}

export function PricingTable() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Embudo (backlog #156): "apertura de precios". Disparamos `pricing_view`
  // una sola vez, cuando la parrilla entra de verdad en pantalla — no al
  // montar, porque el bloque vive al final de una página muy larga.
  useEffect(() => {
    const el = rootRef.current;
    const fire = () => {
      trackFunnel("pricing_view", {
        pagina: typeof window === "undefined" ? "" : window.location.pathname,
      });
    };
    if (!el || typeof IntersectionObserver === "undefined") {
      fire();
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          fire();
          io.disconnect();
        }
      },
      { rootMargin: "0px 0px -20% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) trackFunnel("pricing_compare_open");
  };

  return (
    <div className="mt-10" ref={rootRef}>
      <div className="text-center">
        <button
          onClick={toggle}
          aria-expanded={open}
          aria-controls="tabla-comparativa"
          className="inline-flex items-center gap-2 px-5 py-3 rounded-xl text-[14px] font-medium text-ink-900 ring-1 ring-ink-900/10 hover:bg-ink-900/5 transition"
        >
          Comparar planes en detalle
          <IPlus
            size={15}
            className={`transition-transform duration-300 ${open ? "rotate-45" : "rotate-0"}`}
          />
        </button>
      </div>

      <div
        id="tabla-comparativa"
        className="grid transition-all duration-500"
        style={{ gridTemplateRows: open ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          <Reveal className="mt-8">
            <div className="overflow-x-auto rounded-3xl ring-1 ring-ink-900/5 shadow-card bg-white">
              <table className="w-full min-w-[640px] text-left border-collapse">
                <caption className="sr-only">
                  Comparativa detallada de los planes Starter, Pro y Agencia
                </caption>
                <thead>
                  <tr className="border-b border-ink-900/5">
                    <th scope="col" className="px-6 py-5 text-[13px] font-medium text-ink-500 w-[40%]">
                      Qué incluye
                    </th>
                    {(
                      [
                        ["Starter", PLAN_PRICES.starter, false],
                        ["Pro", PLAN_PRICES.pro, true],
                        ["Agencia", PLAN_PRICES.agencia, false],
                      ] as const
                    ).map(([name, price, rec]) => (
                      <th scope="col" key={name} className="px-4 py-5 text-center">
                        <div
                          className={`font-display font-bold text-[18px] ${
                            rec ? "text-selai-600" : "text-ink-900"
                          }`}
                        >
                          {name}
                        </div>
                        <div className="text-[12px] text-ink-500 font-mono mt-0.5">
                          {price} €/mes
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                {COMPARISON_GROUPS.map((group) => (
                  <tbody key={group.title}>
                    <tr className="bg-paper/70">
                      <th
                        scope="rowgroup"
                        colSpan={4}
                        className="px-6 py-2.5 text-[11px] uppercase tracking-[0.14em] text-ink-500 font-mono font-medium text-left"
                      >
                        {group.title}
                      </th>
                    </tr>
                    {group.rows.map((row) => (
                      <tr key={row.label} className="border-b border-ink-900/5 last:border-0">
                        <th
                          scope="row"
                          className="px-6 py-3.5 text-[14px] font-normal text-ink-700 text-left"
                        >
                          {row.label}
                        </th>
                        <td className="px-4 py-3.5 text-center">
                          <Cell value={row.starter} />
                        </td>
                        <td className="px-4 py-3.5 text-center bg-selai-50/50">
                          <Cell value={row.pro} />
                        </td>
                        <td className="px-4 py-3.5 text-center">
                          <Cell value={row.agencia} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                ))}
              </table>
            </div>
            <p className="mt-3 text-[12px] text-ink-500 text-center">
              Todos los planes: 14 días gratis sin tarjeta, sin permanencia y con tus datos
              alojados en la UE.
            </p>
          </Reveal>
        </div>
      </div>
    </div>
  );
}

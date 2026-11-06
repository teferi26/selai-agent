// Disparador del formulario de soporte. Resuelve por sí solo la pantalla en la
// que está el usuario, para que quien lo coloque no tenga que acordarse.
"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";

import { SupportDialog } from "./SupportDialog";

type Variant = "primary" | "secondary" | "sidebar" | "link";

const STYLES: Record<Variant, string> = {
  primary:
    "rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700",
  secondary:
    "rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50",
  sidebar:
    "w-full rounded px-2 py-1.5 text-left text-sm text-slate-600 hover:bg-slate-100",
  link: "text-sm font-medium text-selai-700 underline hover:text-selai-800",
};

export function SupportButton({
  label = "Reportar un problema",
  variant = "secondary",
  className = "",
  errorRef = null,
}: {
  label?: string;
  variant?: Variant;
  className?: string;
  /** Digest del fallo cuando se abre desde una pantalla de error. */
  errorRef?: string | null;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={`${STYLES[variant]} ${className}`}>
        {label}
      </button>
      <SupportDialog
        open={open}
        onClose={() => setOpen(false)}
        screen={pathname}
        errorRef={errorRef}
      />
    </>
  );
}

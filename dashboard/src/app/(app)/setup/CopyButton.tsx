// Small "Copiar" button with a 2s "Copiado ✓" confirmation. Shared by the
// setup wizard and /settings/canales for leads addresses and code snippets.
"use client";

import { useEffect, useRef, useState } from "react";

export default function CopyButton({
  value,
  label = "Copiar",
}: {
  value: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard blocked (http, permisos): selection fallback would be
      // overkill here; the value is always visible and selectable next to
      // the button.
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      className={`inline-flex flex-shrink-0 items-center rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
        copied
          ? "border-emerald-300 bg-emerald-50 text-emerald-700"
          : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
      }`}
    >
      {copied ? "Copiado ✓" : label}
    </button>
  );
}

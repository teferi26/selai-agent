// Search + filter bar for /inbox. Updates the URL (?q=&channel=&conv_status=)
// so the server component re-fetches with the filters applied server-side.
// The text search is debounced 300ms; chips apply instantly.
"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

const CHANNELS: { value: string; label: string }[] = [
  { value: "whatsapp", label: "💬 WhatsApp" },
  { value: "email", label: "📧 Email" },
  { value: "voice", label: "📞 Voz" },
  { value: "sms", label: "✉️ SMS" },
  { value: "web", label: "🌐 Web" },
];

const STATUSES: { value: string; label: string }[] = [
  { value: "active", label: "Activas" },
  { value: "snoozed", label: "Pospuestas" },
  { value: "handed_off", label: "Con humano" },
  { value: "closed", label: "Cerradas" },
];

function buildUrl(q: string, channel: string, convStatus: string): string {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  if (channel) params.set("channel", channel);
  if (convStatus) params.set("conv_status", convStatus);
  const qs = params.toString();
  return qs ? `/inbox?${qs}` : "/inbox";
}

export default function InboxFilters({
  initialQ,
  initialChannel,
  initialStatus,
}: {
  initialQ: string;
  initialChannel: string;
  initialStatus: string;
}) {
  const router = useRouter();
  const [q, setQ] = useState(initialQ);
  const [channel, setChannel] = useState(initialChannel);
  const [convStatus, setConvStatus] = useState(initialStatus);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const firstRender = useRef(true);

  // Debounced navigation when the text changes.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      router.replace(buildUrl(q, channel, convStatus));
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  function applyChannel(value: string) {
    const next = channel === value ? "" : value;
    setChannel(next);
    router.replace(buildUrl(q, next, convStatus));
  }

  function applyStatus(value: string) {
    const next = convStatus === value ? "" : value;
    setConvStatus(next);
    router.replace(buildUrl(q, channel, next));
  }

  const chipBase = "rounded-full border px-3 py-1 text-xs transition-colors";
  const chipOff = "border-slate-200 bg-white text-slate-600 hover:border-selai-300";
  const chipOn = "border-selai-500 bg-selai-50 text-selai-700 font-medium";

  return (
    <div className="mb-4 rounded-lg border border-slate-200 bg-white p-4">
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Buscar por nombre, teléfono o mensaje…"
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
        aria-label="Buscar conversaciones"
      />
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-slate-400">Canal:</span>
        {CHANNELS.map((c) => (
          <button
            key={c.value}
            onClick={() => applyChannel(c.value)}
            className={`${chipBase} ${channel === c.value ? chipOn : chipOff}`}
          >
            {c.label}
          </button>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-slate-400">Estado:</span>
        {STATUSES.map((st) => (
          <button
            key={st.value}
            onClick={() => applyStatus(st.value)}
            className={`${chipBase} ${convStatus === st.value ? chipOn : chipOff}`}
          >
            {st.label}
          </button>
        ))}
        {(q || channel || convStatus) && (
          <button
            onClick={() => {
              setQ("");
              setChannel("");
              setConvStatus("");
              router.replace("/inbox");
            }}
            className="ml-2 text-xs text-slate-400 underline hover:text-slate-600"
          >
            Quitar filtros
          </button>
        )}
      </div>
    </div>
  );
}

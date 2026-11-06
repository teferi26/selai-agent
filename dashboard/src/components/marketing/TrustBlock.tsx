// RGPD trust line (backlog #24/#136). Truthful: hosting on Railway (EU
// region) + no-training clause in the DPA. Rendered near sign-up CTAs and,
// in compact form, in the footer.
export function TrustBlock({ className = "" }: { className?: string }) {
  return (
    <p
      className={`inline-flex flex-wrap items-center justify-center gap-x-2 gap-y-1 rounded-xl bg-selai-50 ring-1 ring-selai-200 px-4 py-2.5 text-[13px] text-selai-700 ${className}`}
    >
      <span role="img" aria-label="Unión Europea">
        🇪🇺
      </span>
      <span className="font-medium">Datos alojados en la UE</span>
      <span className="text-selai-700/40" aria-hidden>
        ·
      </span>
      <span className="font-medium">RGPD</span>
      <span className="text-selai-700/40" aria-hidden>
        ·
      </span>
      <span className="font-medium">No entrenamos modelos de IA con tus datos</span>
    </p>
  );
}

// FAQ de facturación (backlog #90) — native <details> accordion so it stays
// server-renderable and crawleable. Shown under the pricing grid and on
// /precios.
import { BILLING_FAQ } from "./pricing-data";

export function BillingFAQ() {
  return (
    <div className="mx-auto max-w-[760px]">
      <h3 className="font-display font-bold text-[24px] md:text-[28px] text-ink-900 text-center mb-6">
        Preguntas sobre la facturación
      </h3>
      <div className="space-y-2">
        {BILLING_FAQ.map(([q, a]) => (
          <details
            key={q}
            className="group rounded-2xl ring-1 ring-ink-900/5 bg-white open:ring-selai-200"
          >
            <summary className="flex items-center justify-between gap-4 px-5 py-4 cursor-pointer list-none [&::-webkit-details-marker]:hidden text-[15px] font-medium text-ink-700 group-open:text-ink-900">
              {q}
              <span
                className="flex-shrink-0 w-7 h-7 rounded-full bg-ink-900/5 text-ink-700 group-open:bg-selai-500 group-open:text-white flex items-center justify-center transition-colors duration-300"
                aria-hidden
              >
                <svg
                  width={14}
                  height={14}
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.6}
                  strokeLinecap="round"
                  className="transition-transform duration-300 group-open:rotate-45"
                >
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </span>
            </summary>
            <p className="px-5 pb-5 text-[14px] text-ink-500 leading-relaxed">{a}</p>
          </details>
        ))}
      </div>
    </div>
  );
}

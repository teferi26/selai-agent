"use client";

// Below-the-fold lazy islands (backlog #119): the live demo chat and the ROI
// calculator are pure-interactive sections with no SEO value, so they load in
// their own chunks on the client instead of inflating the initial bundle.
import dynamic from "next/dynamic";

function SectionSkeleton() {
  return (
    <div className="py-24 md:py-32" aria-hidden>
      <div className="mx-auto max-w-[1200px] px-6 md:px-10">
        <div className="mx-auto max-w-[760px] h-[420px] rounded-3xl bg-ink-900/[0.03] ring-1 ring-ink-900/5 animate-pulse" />
      </div>
    </div>
  );
}

export const LazyLiveDemoChat = dynamic(
  () => import("./LiveDemoChat").then((m) => m.LiveDemoChat),
  { ssr: false, loading: SectionSkeleton },
);

export const LazyROICalculator = dynamic(
  () => import("./ROICalculator").then((m) => m.ROICalculator),
  { ssr: false, loading: SectionSkeleton },
);

// Shared UI primitives for the marketing surface — Pill, Btn, Section,
// Container, EyebrowH2. All server-renderable.
import type { ReactNode } from "react";

export type Tone = "selai" | "dark" | "accent" | "paper";

const PILL_TONES: Record<Tone, string> = {
  selai: "bg-selai-100 text-selai-700 ring-1 ring-selai-200",
  dark: "bg-white/10 text-selai-100 ring-1 ring-white/15",
  accent: "bg-accent/10 text-accent ring-1 ring-accent/20",
  paper: "bg-paper text-ink-700 ring-1 ring-black/5",
};

export function Pill({
  children,
  tone = "selai",
  className = "",
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-[12px] font-medium ${PILL_TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export type BtnVariant = "primary" | "dark" | "selai" | "ghost" | "ghostDark" | "link";
export type BtnSize = "sm" | "md" | "lg";

const BTN_SIZES: Record<BtnSize, string> = {
  sm: "px-3 py-2 text-[13px]",
  md: "px-5 py-3 text-[14px]",
  lg: "px-6 py-3.5 text-[15px]",
};

const BTN_VARIANTS: Record<BtnVariant, string> = {
  primary: "bg-accent text-white hover:bg-[#FF6A45] shadow-accent",
  dark: "bg-ink-900 text-white hover:bg-ink-800",
  selai: "bg-selai-500 text-white hover:bg-selai-600 shadow-soft",
  ghost: "bg-transparent text-ink-900 hover:bg-ink-900/5 ring-1 ring-ink-900/10",
  ghostDark: "bg-transparent text-selai-100 hover:bg-white/10 ring-1 ring-white/15",
  link: "text-selai-600 hover:text-selai-700",
};

type BtnProps = {
  children: ReactNode;
  variant?: BtnVariant;
  size?: BtnSize;
  href?: string;
  className?: string;
  type?: "button" | "submit";
  onClick?: () => void;
};

/**
 * Class string for a button/link. Exported so client components can render
 * their own <a> (e.g. with an onClick tracker) without duplicating styles.
 */
export function btnClass(
  variant: BtnVariant = "primary",
  size: BtnSize = "md",
  className = "",
): string {
  return `inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-all duration-200 ${BTN_SIZES[size]} ${BTN_VARIANTS[variant]} ${className}`;
}

/** Button or link, depending on whether `href` is provided. */
export function Btn({
  children,
  variant = "primary",
  size = "md",
  href,
  className = "",
  type,
  onClick,
}: BtnProps) {
  const cls = btnClass(variant, size, className);
  if (href) {
    return (
      <a href={href} className={cls}>
        {children}
      </a>
    );
  }
  return (
    <button type={type ?? "button"} onClick={onClick} className={cls}>
      {children}
    </button>
  );
}

export function Section({
  id,
  children,
  className = "",
  dark = false,
}: {
  id?: string;
  children: ReactNode;
  className?: string;
  dark?: boolean;
}) {
  return (
    <section
      id={id}
      className={`relative ${dark ? "bg-ink-900 text-selai-50 grain" : ""} ${className}`}
    >
      {children}
    </section>
  );
}

export function Container({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mx-auto max-w-[1200px] px-6 md:px-10 ${className}`}>{children}</div>
  );
}

export function EyebrowH2({
  eyebrow,
  title,
  sub,
  dark = false,
  center = false,
}: {
  eyebrow?: string;
  title: ReactNode;
  sub?: ReactNode;
  dark?: boolean;
  center?: boolean;
}) {
  return (
    <div className={`max-w-3xl ${center ? "mx-auto text-center" : ""}`}>
      {eyebrow && (
        <Pill tone={dark ? "dark" : "selai"} className="mb-5">
          {eyebrow}
        </Pill>
      )}
      <h2
        className={`font-display font-bold text-[40px] md:text-[56px] leading-[1.02] ${
          dark ? "text-selai-50" : "text-ink-900"
        }`}
        style={{ textWrap: "balance" } as React.CSSProperties}
      >
        {title}
      </h2>
      {sub && (
        <p
          className={`mt-5 text-[17px] leading-[1.6] ${
            dark ? "text-selai-100/75" : "text-ink-500"
          }`}
          style={{ textWrap: "pretty" } as React.CSSProperties}
        >
          {sub}
        </p>
      )}
    </div>
  );
}

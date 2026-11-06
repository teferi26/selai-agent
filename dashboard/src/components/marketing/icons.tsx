// Inline SVG icons used across the marketing surface.
// Kept stroke-based and minimal so they tree-shake to almost nothing.
import type { SVGProps } from "react";

type IconProps = {
  size?: number;
  stroke?: number;
  className?: string;
} & Omit<SVGProps<SVGSVGElement>, "stroke">;

const Icon = ({ size = 20, stroke = 1.6, className, children, ...rest }: IconProps & { children: React.ReactNode }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    strokeWidth={stroke}
    stroke="currentColor"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    {...rest}
  >
    {children}
  </svg>
);

export const IBolt = (p: IconProps) => (
  <Icon {...p}>
    <path d="M13 3 4 14h7l-1 7 9-11h-7z" />
  </Icon>
);
export const ICheck = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 12l5 5L20 6" />
  </Icon>
);
export const IArrow = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Icon>
);
export const IClock = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </Icon>
);
export const IShield = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3 4 6v6c0 5 4 8 8 9 4-1 8-4 8-9V6z" />
    <path d="M9 12l2 2 4-4" />
  </Icon>
);
export const ITrend = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 17l6-6 4 4 8-8" />
    <path d="M14 7h7v7" />
  </Icon>
);
export const IInfinity = (p: IconProps) => (
  <Icon {...p}>
    <path d="M6.5 12c0-2 1.5-4 4-4 2 0 3 1.5 5 4s3 4 5 4 4-2 4-4-2-4-4-4-3 1.5-5 4-3 4-5 4-4-2-4-4z" />
  </Icon>
);
export const IMail = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M3 7l9 7 9-7" />
  </Icon>
);
export const IPhone = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 4h4l2 5-3 2a12 12 0 0 0 5 5l2-3 5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z" />
  </Icon>
);
export const IChat = (p: IconProps) => (
  <Icon {...p}>
    <path d="M21 12a8 8 0 1 1-3-6.2L21 5l-1 3.5A8 8 0 0 1 21 12z" />
  </Icon>
);
export const IWhats = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 21l1.7-5A8 8 0 1 1 8 19.3z" />
    <path d="M9 9c0 3 3 6 6 6l1.5-1-2-1-1 .8c-1-.5-2-1.5-2.5-2.5l.8-1-1-2z" fill="currentColor" />
  </Icon>
);
export const IMenu = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Icon>
);
export const IX = (p: IconProps) => (
  <Icon {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Icon>
);
export const ITranslate = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 6h10M8 4v2c0 4-2 8-5 10M5 9c0 3 4 6 8 6" />
    <path d="M14 21l4-10 4 10M16 17h6" />
  </Icon>
);
export const IPlus = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);
export const IMinus = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12h14" />
  </Icon>
);
export const IHouse = (p: IconProps) => (
  <Icon {...p}>
    <path d="m3 11 9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" />
  </Icon>
);
export const ICalendar = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 10h18M8 3v4M16 3v4" />
  </Icon>
);
export const IPin = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 22s7-7 7-12a7 7 0 0 0-14 0c0 5 7 12 7 12z" />
    <circle cx="12" cy="10" r="2.5" />
  </Icon>
);
export const ILinkedIn = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <path d="M8 10v7M8 7v.01M12 17v-4a2 2 0 0 1 4 0v4M12 13v4" fill="currentColor" />
  </Icon>
);
export const ISend = (p: IconProps) => (
  <Icon {...p}>
    <path d="M22 3 11 14M22 3l-7 18-4-7-7-4z" />
  </Icon>
);

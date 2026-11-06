import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Existing dashboard tokens (lead temperature)
        hot: "#ef4444",
        warm: "#f59e0b",
        cold: "#64748b",

        // selai brand palette (marketing surface)
        selai: {
          50: "#F5F7FF",
          100: "#EAEEFF",
          200: "#D4DBFF",
          300: "#B0B8FF",
          400: "#7B83FF",
          500: "#5B61F0",
          600: "#4A4FD9",
          700: "#3A3EB0",
          900: "#1A1B33",
        },
        ink: {
          900: "#0B0D17",
          800: "#13162A",
          700: "#1F2333",
          500: "#4A4F66",
          300: "#9098B0",
        },
        paper: "#FAFAFC",
        accent: "#FF7A59",
        "accent-2": "#FF9F7A",
        wa: "#25D366",
        "wa-bg": "#0B141A",
        "wa-bubble": "#005C4B",
      },
      fontFamily: {
        // Bound to next/font CSS variables loaded in src/app/fonts.ts
        display: ["var(--font-display)", "Fraunces", "serif"],
        sans: ["var(--font-sans)", "Inter", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "JetBrains Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        soft: "0 24px 60px -30px rgba(91,97,240,0.40)",
        card: "0 1px 0 rgba(11,13,23,0.04), 0 12px 40px -16px rgba(11,13,23,0.10)",
        glow: "0 0 0 1px rgba(91,97,240,0.20), 0 24px 60px -20px rgba(91,97,240,0.45)",
        accent: "0 16px 40px -16px rgba(255,122,89,0.55)",
      },
      keyframes: {
        rise: {
          "0%": { opacity: "0", transform: "translateY(16px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        breathe: {
          "0%,100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-6px)" },
        },
        "pulse-line": {
          "0%,100%": { opacity: "0.35" },
          "50%": { opacity: "1" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        typing: {
          "0%,80%,100%": { opacity: "0.2" },
          "40%": { opacity: "1" },
        },
        ticker: {
          from: { transform: "translateX(0)" },
          to: { transform: "translateX(-50%)" },
        },
        draw: {
          to: { strokeDashoffset: "0" },
        },
      },
      animation: {
        rise: "rise 0.7s cubic-bezier(0.16,1,0.3,1) both",
        breathe: "breathe 6s ease-in-out infinite",
        "pulse-line": "pulse-line 2.4s ease-in-out infinite",
        shimmer: "shimmer 3s linear infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;

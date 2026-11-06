// Default OpenGraph image generated with the supported Node.js runtime. Renders the selai
// wordmark on a brand-coloured gradient — used by social card previews.
import { ImageResponse } from "next/og";

export const runtime = "nodejs";
export const alt = "selai — IA para inmobiliarias en España";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function OG() {
  return new ImageResponse(
    (
      <div
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background:
            "radial-gradient(ellipse at top left, #5B61F0 0%, #1A1B33 60%, #0B0D17 100%)",
          color: "#F5F7FF",
          padding: 80,
          fontFamily: "system-ui, -apple-system, sans-serif",
          letterSpacing: "-0.04em",
        }}
      >
        <div
          style={{
            fontSize: 200,
            fontWeight: 800,
            lineHeight: 1,
            display: "flex",
          }}
        >
          <span>sel</span>
          <span style={{ color: "#FF7A59" }}>ai.</span>
        </div>
        <div
          style={{
            marginTop: 48,
            fontSize: 44,
            color: "#EAEEFF",
            fontWeight: 500,
            textAlign: "center",
            maxWidth: 900,
            lineHeight: 1.15,
          }}
        >
          Atiende cada lead en 15 segundos.
        </div>
        <div
          style={{
            marginTop: 28,
            fontSize: 26,
            color: "#7B83FF",
            fontWeight: 400,
          }}
        >
          IA para inmobiliarias en España · WhatsApp · Email · Voz · Web
        </div>
      </div>
    ),
    { ...size },
  );
}

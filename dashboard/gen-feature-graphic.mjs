// One-off: renders the Google Play feature graphic (1024x500) from an SVG
// composed with the selai brand system. Run from dashboard/ (sharp lives here).
import sharp from "sharp";

const svg = `
<svg width="1024" height="500" viewBox="0 0 1024 500" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#5B61F0"/>
      <stop offset="1" stop-color="#3A3EB0"/>
    </linearGradient>
  </defs>
  <rect width="1024" height="500" fill="url(#bg)"/>
  <circle cx="880" cy="80" r="160" fill="#ffffff" opacity="0.06"/>
  <circle cx="120" cy="440" r="120" fill="#ffffff" opacity="0.06"/>
  <rect x="96" y="150" width="120" height="120" rx="32" fill="#ffffff"/>
  <text x="156" y="238" font-family="Georgia, serif" font-size="84" font-weight="700"
        fill="#5B61F0" text-anchor="middle">s</text>
  <circle cx="204" cy="256" r="10" fill="#FF7A59"/>
  <text x="260" y="215" font-family="Georgia, serif" font-size="88" font-weight="700"
        fill="#ffffff">selai</text>
  <text x="264" y="272" font-family="Arial, Helvetica, sans-serif" font-size="30"
        fill="#E4E5FB">La IA que atiende tu inmobiliaria 24/7</text>
  <text x="264" y="330" font-family="Arial, Helvetica, sans-serif" font-size="24"
        fill="#C3C5F5">Llamadas · WhatsApp · Visitas agendadas solas</text>
</svg>`;

await sharp(Buffer.from(svg))
  .png()
  .toFile("../mobile/store-assets/feature-graphic-1024x500.png");
console.log("feature graphic OK");

import "./globals.css";
import type { Metadata, Viewport } from "next";
import Script from "next/script";
import { ClerkProvider } from "@clerk/nextjs";
import { esES } from "@clerk/localizations";
import { Analytics } from "@vercel/analytics/next";

import { fontDisplay, fontMono, fontSans } from "./fonts";

const clerkEnabled = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const clerkLocalization = {
  ...esES,
  // Clerk's Spanish pack currently falls back to English for this placeholder
  // in the sign-up flow. Keep the first user-facing form fully localized.
  formFieldInputPlaceholder__signUpPassword: "Crea una contraseña",
};

export const metadata: Metadata = {
  // Canonical host is www — selai.es 308-redirects there.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://www.selai.es"),
  keywords: [
    "IA inmobiliaria",
    "asistente IA inmobiliaria",
    "responder leads Idealista",
    "WhatsApp inmobiliaria automático",
    "agente IA leads",
    "software inmobiliario España",
  ],
  title: {
    default: "selai — Atiende cada lead en 15 segundos | IA para inmobiliarias en España",
    template: "%s · selai",
  },
  description:
    "selai es el asistente de IA conversacional que atiende WhatsApp, email, web y teléfono de tu inmobiliaria 24/7. Respuesta en 15 segundos, entrenado con tus mejores ventas.",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "selai",
    statusBarStyle: "default",
  },
  alternates: { canonical: "/" },
};

export const viewport: Viewport = {
  themeColor: "#5B61F0",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const shell = (
    <html
      lang="es-ES"
      className={`${fontDisplay.variable} ${fontSans.variable} ${fontMono.variable}`}
    >
      <body className="bg-paper text-ink-900 antialiased font-sans">
        {children}
        {/* Analítica cookieless (backlog #156): mide visitas y eventos de
            embudo sin escribir cookies ni identificadores persistentes, para
            que la promesa "sin rastreo" del aviso siga siendo cierta.
            Requiere activar Web Analytics en el proyecto de Vercel. */}
        <Analytics />
        <Script id="register-sw" strategy="afterInteractive">
          {`if ("serviceWorker" in navigator) { navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" }).catch(() => {}); }`}
        </Script>
      </body>
    </html>
  );
  if (!clerkEnabled) return shell;
  return <ClerkProvider localization={clerkLocalization}>{shell}</ClerkProvider>;
}

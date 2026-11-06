import { CookieNotice } from "@/components/marketing/CookieNotice";
import { Footer } from "@/components/marketing/Footer";
import { Header } from "@/components/marketing/Header";
import { StickyCTA } from "@/components/marketing/StickyCTA";

export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-paper text-ink-900">
      <Header />
      <main>{children}</main>
      <Footer />
      {/* CTA fija en móvil tras pasar el hero (backlog #87) */}
      <StickyCTA />
      {/* Aviso de cookies técnico, sin gestor de consentimiento (backlog #138) */}
      <CookieNotice />
    </div>
  );
}

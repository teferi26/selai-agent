// Hero — server component with client islands for the staggered headline and
// the tracked primary CTA.
import { SignupCTA } from "./analytics";
import { ChannelCard } from "./ChannelCard";
import { IArrow, IPin, IShield } from "./icons";
import { StaggerWords } from "./animated";
import { Btn, Container, Pill } from "./ui";

export function Hero() {
  return (
    <section className="relative overflow-hidden pb-8 pt-16 md:pb-10">
      <div className="absolute inset-0 grid-bg pointer-events-none" />
      <Container className="relative">
        <div className="grid lg:grid-cols-[1.05fr_0.95fr] gap-10 lg:gap-12 items-center">
          <div>
            <div className="animate-rise">
              <Pill tone="selai">
                <span className="w-1.5 h-1.5 rounded-full bg-selai-500" />
                Para inmobiliarias en España
              </Pill>
            </div>
            <h1
              className="mt-4 font-display text-[48px] font-bold leading-[1] text-ink-900 sm:text-[58px] md:text-[64px] lg:text-[68px]"
              style={{ textWrap: "balance" } as React.CSSProperties}
            >
              {/* Delays ajustados para que el titular completo sea legible
                  en <1,5 s (backlog #88). */}
              <StaggerWords text="Cada lead, respondido en" />
              <span className="block">
                <StaggerWords text="15 segundos." delay={0.2} />
              </span>
              <span className="block font-display text-[44px] font-medium italic leading-[1.05] text-ink-500/80 sm:text-[50px] md:text-[54px] lg:text-[56px]">
                <StaggerWords text="A las 3 a.m. también." delay={0.4} />
              </span>
            </h1>
            <p
              className="mt-4 max-w-[560px] animate-rise text-[16px] leading-[1.5] text-ink-500 md:text-[17px]"
              style={{ animationDelay: "0.5s", textWrap: "pretty" } as React.CSSProperties}
            >
              selai atiende WhatsApp, email, web y teléfono con tu catálogo y tu tono, para
              responder antes de que el cliente cambie de inmobiliaria.{" "}
              <span className="font-medium text-ink-900">
                Desde 99 €/mes · pruébalo gratis 14 días sin tarjeta.
              </span>
            </p>
            <p className="mt-2 max-w-[560px] text-[12px] leading-relaxed text-ink-500">
              Puesta en marcha guiada desde el primer día. WhatsApp queda operativo cuando Meta
              verifica tu empresa, normalmente en 1-2 días.
            </p>
            <div
              className="mt-5 flex flex-wrap items-center gap-3 animate-rise"
              style={{ animationDelay: "0.65s" }}
            >
              {/* CTA principal del embudo (backlog #156): evento signup_click
                  con origen "hero". */}
              <SignupCTA origen="hero" variant="primary" size="lg">
                Empieza gratis
                <IArrow size={16} />
              </SignupCTA>
              <Btn variant="ghost" size="lg" href="/instalacion">
                Ver cómo se instala
              </Btn>
              {/* Sello discreto (backlog #29) */}
              <span className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl ring-1 ring-ink-900/10 text-[12px] text-ink-500 bg-white/60">
                <span role="img" aria-label="España">
                  🇪🇸
                </span>
                Hecho en España · Soporte en español
              </span>
            </div>
            <div
              className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12px] text-ink-500 animate-rise font-mono"
              style={{ animationDelay: "0.8s" }}
            >
              <span className="flex items-center gap-1.5">
                <IPin size={13} />
                Hecho en Alicante
              </span>
              <span className="text-ink-300">·</span>
              <span className="flex items-center gap-1.5">
                <IShield size={13} />
                RGPD-ready
              </span>
              <span className="text-ink-300">·</span>
              <span>Sin permanencia</span>
              <span className="text-ink-300">·</span>
              <span>14 días gratis</span>
            </div>
          </div>

          {/* Channel mockups stack */}
          <div className="relative h-[520px] hidden lg:block">
            <div className="absolute top-0 right-12 float-1 z-30">
              <ChannelCard kind="whatsapp" />
            </div>
            <div className="absolute top-[140px] left-0 float-2 z-20">
              <ChannelCard kind="email" />
            </div>
            <div className="absolute top-[280px] right-0 float-3 z-30">
              <ChannelCard kind="voice" />
            </div>
            <div className="absolute bottom-0 left-12 float-4 z-20">
              <ChannelCard kind="web" />
            </div>
          </div>
        </div>

        {/* Mobile channel preview */}
        <div className="lg:hidden mt-10 flex gap-4 overflow-x-auto no-scrollbar -mx-6 px-6 pb-4">
          <ChannelCard kind="whatsapp" />
          <ChannelCard kind="email" />
          <ChannelCard kind="voice" />
          <ChannelCard kind="web" />
        </div>
      </Container>
    </section>
  );
}

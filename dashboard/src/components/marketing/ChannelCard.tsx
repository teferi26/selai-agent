// Static marketing mockup of a chat in each channel. Pure server component.
import { ICalendar, IChat, IHouse, IMail, IPhone, IWhats } from "./icons";

type Kind = "whatsapp" | "email" | "voice" | "web";

const VOICE_BARS = [8, 14, 22, 18, 28, 12, 26, 32, 20, 14, 24, 18, 28, 16, 22, 12, 18];

export function ChannelCard({ kind, className = "" }: { kind: Kind; className?: string }) {
  return <div className={className}>{render(kind)}</div>;
}

function render(kind: Kind) {
  switch (kind) {
    case "whatsapp":
      return (
        <div className="bg-[#0B141A] text-white p-3 w-[260px] rounded-2xl shadow-soft">
          <div className="flex items-center gap-2 pb-2 border-b border-white/10">
            <div className="w-8 h-8 rounded-full bg-wa flex items-center justify-center">
              <IWhats size={16} />
            </div>
            <div>
              <div className="text-[12px] font-semibold">selai · Inmob. Costa</div>
              <div className="text-[10px] text-emerald-300/80">en línea</div>
            </div>
          </div>
          <div className="space-y-1.5 pt-2">
            <div className="bg-white/10 rounded-lg rounded-tl-sm px-3 py-1.5 text-[12px] max-w-[80%]">
              Sigue disponible el de Plaza Nueva?
            </div>
            <div className="bg-wa-bubble rounded-lg rounded-tr-sm px-3 py-1.5 text-[12px] max-w-[88%] ml-auto">
              ¡Hola Marta! Sí, sigue disponible. ¿Te paso ficha y agendamos visita mañana 10:30?
            </div>
            <div className="text-[9px] text-white/40 text-right pt-0.5 font-mono">12s</div>
          </div>
        </div>
      );
    case "email":
      return (
        <div className="bg-white p-4 w-[270px] rounded-2xl shadow-card border border-ink-900/5">
          <div className="flex items-center gap-2 pb-2 border-b border-ink-900/5">
            <div className="w-8 h-8 rounded-lg bg-blue-100 text-blue-600 flex items-center justify-center">
              <IMail size={16} />
            </div>
            <div>
              <div className="text-[12px] font-semibold text-ink-900">Re: Piso C/ Velázquez</div>
              <div className="text-[10px] text-ink-500 font-mono">selai · 14s</div>
            </div>
          </div>
          <div className="pt-2 text-[12px] text-ink-700 leading-relaxed">
            <span className="text-ink-500">Hola Carlos,</span>
            <br />
            gracias por tu interés. He preparado{" "}
            <span className="text-selai-600 font-medium">3 alternativas</span> que encajan con tu
            presupuesto y zona…
          </div>
        </div>
      );
    case "voice":
      return (
        <div className="bg-ink-900 text-white p-4 w-[250px] rounded-2xl shadow-soft">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-8 h-8 rounded-full bg-selai-500/20 text-selai-300 flex items-center justify-center">
              <IPhone size={14} />
            </div>
            <div>
              <div className="text-[12px] font-semibold">Llamada · selai</div>
              <div className="text-[10px] text-selai-300 font-mono">00:42</div>
            </div>
          </div>
          <div className="flex items-end gap-[3px] h-10">
            {VOICE_BARS.map((h, i) => (
              <div
                key={i}
                className="w-[3px] rounded-full bg-gradient-to-t from-selai-500 to-selai-300"
                style={{ height: `${h}px` }}
              />
            ))}
          </div>
          <div className="mt-3 text-[11px] text-selai-100/80 leading-snug">
            &ldquo;...tres habitaciones, terraza orientación sur, plaza de garaje incluida...&rdquo;
          </div>
        </div>
      );
    case "web":
      return (
        <div className="bg-white p-3 w-[260px] rounded-2xl shadow-card border border-ink-900/5">
          <div className="flex items-center gap-2 pb-2 border-b border-ink-900/5">
            <div className="w-8 h-8 rounded-xl bg-accent/10 text-accent flex items-center justify-center">
              <IChat size={16} />
            </div>
            <div className="text-[12px] font-semibold text-ink-900">Web chat · inmoroca.es</div>
          </div>
          <div className="pt-2 space-y-1.5">
            <div className="bg-ink-900/5 rounded-lg rounded-tl-sm px-3 py-1.5 text-[12px] text-ink-900 max-w-[85%]">
              Busco ático en Cabo Huertas, hasta 600k
            </div>
            <div className="bg-selai-500 text-white rounded-lg rounded-tr-sm px-3 py-1.5 text-[12px] max-w-[88%] ml-auto">
              Tengo 4 que encajan. ¿Prefieres ver primero los más nuevos o los de mayor terraza?
            </div>
            <div className="flex gap-1 pt-1">
              <div className="w-1.5 h-1.5 rounded-full bg-ink-300 dot" />
              <div className="w-1.5 h-1.5 rounded-full bg-ink-300 dot" />
              <div className="w-1.5 h-1.5 rounded-full bg-ink-300 dot" />
            </div>
          </div>
        </div>
      );
  }
}

// Re-export so consumers don't import it from a different file
export { ICalendar, IHouse };

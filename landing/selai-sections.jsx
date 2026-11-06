// All page sections

// ===================== HEADER =====================
const Header = () => {
  const [scrolled, setScrolled] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  const links = [
    ['Producto', '#por-que-selai'],
    ['Cómo funciona', '#como-funciona'],
    ['Precios', '#precios'],
    ['FAQ', '#faq'],
    ['Sobre nosotros', '#sobre-nosotros'],
  ];
  return (
    <header className={`fixed top-0 inset-x-0 z-50 transition-all duration-300 ${scrolled ? 'glass bg-paper/70 border-b border-ink-900/5' : 'bg-transparent'}`}>
      <Container className="flex items-center justify-between h-16 md:h-[72px]">
        <a href="#" className="flex items-center gap-2"><SelaiLogo className="text-[26px]" /></a>
        <nav className="hidden md:flex items-center gap-1">
          {links.map(([label, href]) => (
            <a key={href} href={href} className="px-3 py-2 text-[14px] text-ink-700 hover:text-ink-900 rounded-lg hover:bg-ink-900/5 transition">{label}</a>
          ))}
        </nav>
        <div className="hidden md:flex items-center gap-2">
          <Btn variant="ghost" size="sm">Iniciar sesión</Btn>
          <Btn variant="primary" size="sm">Reservar demo<IArrow size={14} /></Btn>
        </div>
        <button onClick={() => setOpen(!open)} className="md:hidden p-2 -mr-2 text-ink-900">{open ? <IX size={22}/> : <IMenu size={22}/>}</button>
      </Container>
      {open && (
        <div className="md:hidden glass bg-paper/95 border-t border-ink-900/5">
          <Container className="py-4 flex flex-col gap-1">
            {links.map(([label, href]) => (
              <a key={href} href={href} onClick={() => setOpen(false)} className="px-3 py-3 text-[15px] text-ink-700 rounded-lg hover:bg-ink-900/5">{label}</a>
            ))}
            <div className="flex gap-2 pt-3 border-t border-ink-900/5 mt-2">
              <Btn variant="ghost" size="sm" className="flex-1">Iniciar sesión</Btn>
              <Btn variant="primary" size="sm" className="flex-1">Reservar demo</Btn>
            </div>
          </Container>
        </div>
      )}
    </header>
  );
};

// ===================== HERO =====================
const ChannelCard = ({ kind, className = '' }) => {
  const cards = {
    whatsapp: (
      <div className="bg-[#0B141A] text-white p-3 w-[260px] rounded-2xl shadow-soft">
        <div className="flex items-center gap-2 pb-2 border-b border-white/10">
          <div className="w-8 h-8 rounded-full bg-wa flex items-center justify-center"><IWhats size={16}/></div>
          <div>
            <div className="text-[12px] font-semibold">selai · Inmob. Costa</div>
            <div className="text-[10px] text-emerald-300/80">en línea</div>
          </div>
        </div>
        <div className="space-y-1.5 pt-2">
          <div className="bg-white/10 rounded-lg rounded-tl-sm px-3 py-1.5 text-[12px] max-w-[80%]">Sigue disponible el de Plaza Nueva?</div>
          <div className="bg-wa-bubble rounded-lg rounded-tr-sm px-3 py-1.5 text-[12px] max-w-[88%] ml-auto">¡Hola Marta! Sí, sigue disponible. ¿Te paso ficha y agendamos visita mañana 10:30?</div>
          <div className="text-[9px] text-white/40 text-right pt-0.5 font-mono">12s</div>
        </div>
      </div>
    ),
    email: (
      <div className="bg-white p-4 w-[270px] rounded-2xl shadow-card border border-ink-900/5">
        <div className="flex items-center gap-2 pb-2 border-b border-ink-900/5">
          <div className="w-8 h-8 rounded-lg bg-blue-100 text-blue-600 flex items-center justify-center"><IMail size={16}/></div>
          <div>
            <div className="text-[12px] font-semibold text-ink-900">Re: Piso C/ Velázquez</div>
            <div className="text-[10px] text-ink-500 font-mono">selai · 14s</div>
          </div>
        </div>
        <div className="pt-2 text-[12px] text-ink-700 leading-relaxed">
          <span className="text-ink-500">Hola Carlos,</span><br/>
          gracias por tu interés. He preparado <span className="text-selai-600 font-medium">3 alternativas</span> que encajan con tu presupuesto y zona…
        </div>
      </div>
    ),
    voice: (
      <div className="bg-ink-900 text-white p-4 w-[250px] rounded-2xl shadow-soft">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-8 h-8 rounded-full bg-selai-500/20 text-selai-300 flex items-center justify-center"><IPhone size={14}/></div>
          <div>
            <div className="text-[12px] font-semibold">Llamada · selai</div>
            <div className="text-[10px] text-selai-300 font-mono">00:42</div>
          </div>
        </div>
        <div className="flex items-end gap-[3px] h-10">
          {[8,14,22,18,28,12,26,32,20,14,24,18,28,16,22,12,18].map((h,i)=>(
            <div key={i} className="w-[3px] rounded-full bg-gradient-to-t from-selai-500 to-selai-300" style={{height:`${h}px`}}/>
          ))}
        </div>
        <div className="mt-3 text-[11px] text-selai-100/80 leading-snug">
          "...tres habitaciones, terraza orientación sur, plaza de garaje incluida..."
        </div>
      </div>
    ),
    web: (
      <div className="bg-white p-3 w-[260px] rounded-2xl shadow-card border border-ink-900/5">
        <div className="flex items-center gap-2 pb-2 border-b border-ink-900/5">
          <div className="w-8 h-8 rounded-xl bg-accent/10 text-accent flex items-center justify-center"><IChat size={16}/></div>
          <div className="text-[12px] font-semibold text-ink-900">Web chat · inmoroca.es</div>
        </div>
        <div className="pt-2 space-y-1.5">
          <div className="bg-ink-900/5 rounded-lg rounded-tl-sm px-3 py-1.5 text-[12px] text-ink-900 max-w-[85%]">Busco ático en Cabo Huertas, hasta 600k</div>
          <div className="bg-selai-500 text-white rounded-lg rounded-tr-sm px-3 py-1.5 text-[12px] max-w-[88%] ml-auto">Tengo 4 que encajan. ¿Prefieres ver primero los más nuevos o los de mayor terraza?</div>
          <div className="flex gap-1 pt-1">
            <div className="w-1.5 h-1.5 rounded-full bg-ink-300 dot"/>
            <div className="w-1.5 h-1.5 rounded-full bg-ink-300 dot"/>
            <div className="w-1.5 h-1.5 rounded-full bg-ink-300 dot"/>
          </div>
        </div>
      </div>
    ),
  };
  return <div className={className}>{cards[kind]}</div>;
};

const Hero = () => (
  <section className="relative pt-28 md:pt-36 pb-20 md:pb-28 overflow-hidden">
    <div className="absolute inset-0 grid-bg pointer-events-none" />
    {/* faint orbital glows */}
    <div className="absolute top-40 left-1/2 -translate-x-1/2 w-[800px] h-[800px] rounded-full bg-selai-100/40 blur-3xl pointer-events-none opacity-60"/>
    <Container className="relative">
      <div className="grid lg:grid-cols-[1.05fr_0.95fr] gap-10 lg:gap-12 items-center">
        <div>
          <div className="animate-rise"><Pill tone="selai"><span className="w-1.5 h-1.5 rounded-full bg-selai-500"/>Para inmobiliarias en España</Pill></div>
          <h1 className="mt-6 font-display font-bold text-[clamp(40px,6vw,84px)] leading-[0.98] text-ink-900" style={{textWrap:'balance'}}>
            <StaggerWords text="Cada lead, respondido en" />
            <span className="block"><StaggerWords text="15 segundos." delay={0.27} /></span>
            <span className="block text-ink-500/80 italic font-display font-medium"><StaggerWords text="A las 3 a.m. también." delay={0.54} /></span>
          </h1>
          <p className="mt-6 text-[17px] md:text-[18px] leading-[1.55] text-ink-500 max-w-[560px] animate-rise" style={{animationDelay:'0.85s', textWrap:'pretty'}}>
            selai atiende WhatsApp, email, web y teléfono — entrenado con tus mejores ventas para que cada cliente potencial reciba la respuesta perfecta antes de que cambie de inmobiliaria.
          </p>
          <div className="mt-8 flex flex-wrap gap-3 animate-rise" style={{animationDelay:'1s'}}>
            <Btn variant="primary" size="lg">Reservar demo (20 min)<IArrow size={16}/></Btn>
            <Btn variant="ghost" size="lg" as="a" href="#como-funciona">Cómo funciona</Btn>
          </div>
          <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12px] text-ink-500 animate-rise font-mono" style={{animationDelay:'1.15s'}}>
            <span className="flex items-center gap-1.5"><IPin size={13}/>Hecho en Alicante</span>
            <span className="text-ink-300">·</span>
            <span className="flex items-center gap-1.5"><IShield size={13}/>RGPD-ready</span>
            <span className="text-ink-300">·</span>
            <span>Sin permanencia</span>
            <span className="text-ink-300">·</span>
            <span>14 días gratis</span>
          </div>
        </div>

        {/* Channel mockups stack */}
        <div className="relative h-[520px] hidden lg:block">
          <div className="absolute top-0 right-12 float-1 z-30"><ChannelCard kind="whatsapp"/></div>
          <div className="absolute top-[140px] left-0 float-2 z-20"><ChannelCard kind="email"/></div>
          <div className="absolute top-[280px] right-0 float-3 z-30"><ChannelCard kind="voice"/></div>
          <div className="absolute bottom-0 left-12 float-4 z-20"><ChannelCard kind="web"/></div>
          {/* connector dots */}
          <svg className="absolute inset-0 w-full h-full pointer-events-none opacity-30" viewBox="0 0 500 520">
            <path d="M 350 80 Q 200 200 120 220" stroke="#5B61F0" strokeWidth="1" strokeDasharray="2 6" fill="none"/>
            <path d="M 100 220 Q 250 350 380 320" stroke="#5B61F0" strokeWidth="1" strokeDasharray="2 6" fill="none"/>
            <path d="M 380 320 Q 250 420 130 460" stroke="#5B61F0" strokeWidth="1" strokeDasharray="2 6" fill="none"/>
          </svg>
        </div>
      </div>

      {/* mobile channel preview */}
      <div className="lg:hidden mt-10 flex gap-4 overflow-x-auto no-scrollbar -mx-6 px-6 pb-4">
        <ChannelCard kind="whatsapp"/>
        <ChannelCard kind="email"/>
        <ChannelCard kind="voice"/>
        <ChannelCard kind="web"/>
      </div>
    </Container>
  </section>
);

// ===================== LOGO STRIP =====================
const LogoStrip = () => {
  const logos = ['Inmob. Costa', 'Roca&Asoc', 'Mediterránea', 'Plaza Real', 'Alicante Homes', 'Valencia Living', 'Madrid Estate', 'Levante Pisos'];
  return (
    <section className="py-14 border-y border-ink-900/5 bg-white/40">
      <Container>
        <p className="text-center text-[12px] uppercase tracking-[0.18em] text-ink-500 font-mono mb-7">
          Probado por inmobiliarias en Alicante, Valencia y Madrid
        </p>
        <div className="overflow-hidden mask-fade">
          <div className="flex gap-12 ticker">
            {[...logos, ...logos].map((l, i) => (
              <div key={i} className="flex-shrink-0 flex items-center gap-2 text-ink-500/60 font-display font-bold text-[20px] tracking-tight whitespace-nowrap">
                <div className="w-2.5 h-2.5 rounded-sm bg-ink-300/50"/>
                {l}
              </div>
            ))}
          </div>
        </div>
      </Container>
      <style>{`.mask-fade { mask-image: linear-gradient(to right, transparent, black 12%, black 88%, transparent); }`}</style>
    </section>
  );
};

// ===================== PROBLEM / SOLUTION =====================
const ProblemSolution = () => (
  <Section id="por-que-selai" className="py-24 md:py-32">
    <Container>
      <Reveal><EyebrowH2 eyebrow="Por qué selai" title="El 78% de los leads se enfrían antes del desayuno." sub="No es por mala atención. Es porque nadie puede responder WhatsApp, email y web simultáneamente — a las 23:47 un martes."/></Reveal>

      <div className="mt-14 grid md:grid-cols-2 gap-5">
        {/* Problem */}
        <Reveal>
          <div className="relative bg-ink-900 grain text-selai-50 rounded-3xl p-7 md:p-10 h-full overflow-hidden">
            <div className="flex items-center justify-between mb-6">
              <Pill tone="dark"><span className="w-1.5 h-1.5 rounded-full bg-red-400"/>Sin selai</Pill>
              <span className="font-mono text-[11px] text-selai-100/50">Mar 23:47</span>
            </div>
            <div className="bg-[#0B141A] rounded-2xl p-4 ring-1 ring-white/5">
              <div className="bg-white/8 rounded-lg rounded-tl-sm px-3.5 py-2 max-w-[85%] text-[14px]">
                Hola, sigue disponible el de Plaza Nueva?
                <div className="text-[10px] text-white/40 text-right mt-1 font-mono">23:47 ✓✓</div>
              </div>
              <div className="text-center my-6 text-[11px] text-white/30 font-mono uppercase tracking-wider">— 14 horas de silencio —</div>
              <div className="bg-white/8 rounded-lg rounded-tl-sm px-3.5 py-2 max-w-[85%] text-[14px] opacity-60">
                Vale, gracias. Voy a llamar a Idealista.
                <div className="text-[10px] text-white/40 text-right mt-1 font-mono">13:51 ✓</div>
              </div>
            </div>
            <p className="mt-6 text-[15px] text-selai-100/70 leading-relaxed">
              <span className="text-red-300 font-mono">78%</span> de los leads se enfrían en menos de 1 hora. El resto, antes del desayuno.
            </p>
          </div>
        </Reveal>

        {/* Solution */}
        <Reveal delay={0.1}>
          <div className="relative bg-paper rounded-3xl p-7 md:p-10 h-full ring-1 ring-ink-900/5 overflow-hidden">
            <div className="flex items-center justify-between mb-6">
              <Pill tone="selai"><span className="w-1.5 h-1.5 rounded-full bg-selai-500 animate-pulse"/>Con selai</Pill>
              <span className="font-mono text-[11px] text-ink-500">Mar 23:47:12</span>
            </div>
            <div className="space-y-2">
              <div className="bg-white rounded-lg rounded-tl-sm px-3.5 py-2 max-w-[85%] text-[14px] text-ink-900 ring-1 ring-ink-900/5">
                Hola, sigue disponible el de Plaza Nueva?
              </div>
              <div className="flex items-center gap-2 text-[10px] text-ink-500 font-mono">
                <span className="w-1 h-1 rounded-full bg-selai-500 animate-pulse"/>selai responde · 12s
              </div>
              <div className="bg-selai-500 text-white rounded-lg rounded-tr-sm px-3.5 py-2 max-w-[88%] ml-auto text-[14px]">
                ¡Hola Marta! Sí, Plaza Nueva 14, 3º — 245k. ¿Te paso ficha y agendamos visita mañana?
              </div>
              <div className="bg-white rounded-2xl p-3 ring-1 ring-ink-900/5 ml-auto max-w-[88%] flex gap-3">
                <div className="w-12 h-12 rounded-lg bg-gradient-to-br from-selai-200 to-selai-100 flex items-center justify-center"><IHouse size={20} className="text-selai-600"/></div>
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] font-semibold text-ink-900 truncate">Plaza Nueva, 14 — 3º</div>
                  <div className="text-[11px] text-ink-500">3 hab · 92m² · terraza</div>
                  <div className="text-[12px] font-mono text-selai-600 mt-0.5">245.000 €</div>
                </div>
              </div>
              <div className="bg-white rounded-2xl p-3 ring-1 ring-selai-200 ml-auto max-w-[88%] flex items-center gap-3">
                <div className="w-9 h-9 rounded-lg bg-selai-100 flex items-center justify-center"><ICalendar size={16} className="text-selai-600"/></div>
                <div className="flex-1">
                  <div className="text-[12px] font-semibold text-ink-900">Visita confirmada</div>
                  <div className="text-[11px] text-ink-500 font-mono">Mié 10:30 · agente: Pablo</div>
                </div>
                <ICheck size={18} className="text-selai-500"/>
              </div>
            </div>
            <p className="mt-6 text-[15px] text-ink-500 leading-relaxed">
              El mismo lead tiene <span className="text-ink-900 font-medium">visita agendada antes del desayuno</span>.
            </p>
          </div>
        </Reveal>
      </div>
    </Container>
  </Section>
);

// ===================== HOW IT WORKS =====================
const StepCard = ({ n, title, body, children, delay = 0 }) => {
  const [ref, inView] = useInView();
  return (
    <div ref={ref} style={{ opacity: inView ? 1 : 0, transform: inView ? 'translateY(0)' : 'translateY(24px)', transition: `all 0.8s cubic-bezier(0.16,1,0.3,1) ${delay}s` }}>
      <div className="bg-white rounded-3xl p-7 ring-1 ring-ink-900/5 shadow-card h-full flex flex-col">
        <div className="flex items-center gap-3 mb-5">
          <span className="font-mono text-[11px] text-selai-600 bg-selai-100 px-2 py-1 rounded-md">PASO {n}</span>
        </div>
        <div className={`relative h-[180px] mb-6 ${inView ? 'in-view' : ''}`}>{children}</div>
        <h3 className="font-display font-bold text-[24px] leading-tight text-ink-900 mb-2">{title}</h3>
        <p className="text-[15px] text-ink-500 leading-relaxed">{body}</p>
      </div>
    </div>
  );
};

const StepGfx1 = ({ inView }) => (
  <svg viewBox="0 0 280 180" className="w-full h-full">
    <defs>
      <linearGradient id="g1" x1="0" x2="1"><stop offset="0" stopColor="#5B61F0"/><stop offset="1" stopColor="#FF7A59"/></linearGradient>
    </defs>
    {/* central hub */}
    <circle cx="140" cy="90" r="22" fill="white" stroke="#5B61F0" strokeWidth="1.5"/>
    <text x="140" y="95" textAnchor="middle" fontFamily="Fraunces" fontWeight="700" fontSize="14" fill="#5B61F0">selai</text>
    {/* channel nodes */}
    {[
      { x: 40,  y: 40,  l: 'WA', c: '#25D366' },
      { x: 240, y: 40,  l: '@',  c: '#3B82F6' },
      { x: 40,  y: 140, l: '☎',  c: '#A855F7' },
      { x: 240, y: 140, l: '◐',  c: '#FF7A59' },
    ].map((n, i) => (
      <g key={i}>
        <path d={`M ${n.x} ${n.y} L 140 90`} stroke="url(#g1)" strokeWidth="1.5" className="draw" style={{animationDelay:`${i*0.15}s`}} pathLength="1"/>
        <circle cx={n.x} cy={n.y} r="18" fill={n.c} fillOpacity="0.12" stroke={n.c} strokeWidth="1.2"/>
        <text x={n.x} y={n.y+5} textAnchor="middle" fontSize="14" fill={n.c} fontWeight="700">{n.l}</text>
      </g>
    ))}
  </svg>
);

const StepGfx2 = () => (
  <svg viewBox="0 0 280 180" className="w-full h-full">
    {/* stack of past conversations */}
    {[0,1,2,3,4].map(i => (
      <g key={i} transform={`translate(${30 + i*8} ${30 + i*6})`}>
        <rect width="160" height="22" rx="6" fill="white" stroke="#EAEEFF" strokeWidth="1"/>
        <circle cx="11" cy="11" r="4" fill="#5B61F0" fillOpacity={0.2 + i*0.15}/>
        <rect x="22" y="7" width={80 - i*10} height="3" rx="1.5" fill="#1A1B33" fillOpacity="0.3"/>
        <rect x="22" y="13" width={50 - i*5} height="2" rx="1" fill="#1A1B33" fillOpacity="0.15"/>
      </g>
    ))}
    {/* arrow */}
    <path d="M 200 80 L 240 80" stroke="#5B61F0" strokeWidth="1.5" markerEnd="url(#arr)" className="draw" pathLength="1" style={{animationDelay:'0.4s'}}/>
    <defs><marker id="arr" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#5B61F0"/></marker></defs>
    {/* brain output */}
    <g transform="translate(232 60)">
      <rect width="40" height="40" rx="10" fill="#5B61F0"/>
      <path d="M 8 20 Q 8 12 16 12 Q 24 12 24 20 Q 32 20 32 28 Q 32 36 24 36 L 12 36 Q 4 36 4 28 Q 4 22 8 20 Z" fill="white" fillOpacity="0.95"/>
      <circle cx="14" cy="22" r="1.5" fill="#5B61F0"/>
      <circle cx="22" cy="22" r="1.5" fill="#5B61F0"/>
    </g>
    <text x="252" y="125" textAnchor="middle" fontSize="9" fill="#4A4F66" fontFamily="JetBrains Mono">+50 conv.</text>
  </svg>
);

const StepGfx3 = () => (
  <svg viewBox="0 0 280 180" className="w-full h-full">
    {/* timeline */}
    <line x1="20" y1="90" x2="260" y2="90" stroke="#EAEEFF" strokeWidth="2"/>
    <line x1="20" y1="90" x2="260" y2="90" stroke="#5B61F0" strokeWidth="2" className="draw" pathLength="1"/>
    {/* events */}
    {[
      { x: 40,  l: 'Lead', sub: '00:00' },
      { x: 110, l: 'Responde', sub: '00:15', hl: true },
      { x: 180, l: 'Califica', sub: '01:20' },
      { x: 250, l: 'Visita ✓', sub: '02:00', hl: true },
    ].map((e, i) => (
      <g key={i}>
        <circle cx={e.x} cy="90" r={e.hl ? 6 : 4} fill={e.hl ? '#FF7A59' : '#5B61F0'}/>
        {e.hl && <circle cx={e.x} cy="90" r="10" fill="#FF7A59" fillOpacity="0.2" className="animate-pulse"/>}
        <text x={e.x} y="70" textAnchor="middle" fontSize="11" fill="#0B0D17" fontWeight="600">{e.l}</text>
        <text x={e.x} y="115" textAnchor="middle" fontSize="9" fill="#4A4F66" fontFamily="JetBrains Mono">{e.sub}</text>
      </g>
    ))}
  </svg>
);

const HowItWorks = () => (
  <Section id="como-funciona" className="py-24 md:py-32 bg-gradient-to-b from-paper via-selai-50/40 to-paper">
    <Container>
      <Reveal><EyebrowH2 eyebrow="Cómo funciona" title="Tres pasos. Sin developers, sin meses de implementación." /></Reveal>
      <div className="mt-14 grid md:grid-cols-3 gap-5">
        <StepCard n="01" title="Conectas tus canales" body="WhatsApp Business, Gmail/Outlook, web chat e incluso un número de teléfono. Sin tocar tu CRM."><StepGfx1/></StepCard>
        <StepCard n="02" title="selai aprende de ti" body="Analiza tus últimas 50 conversaciones cerradas con éxito y replica tu tono, tus argumentos, tu forma de cerrar." delay={0.1}><StepGfx2/></StepCard>
        <StepCard n="03" title="Atiende cada lead" body="Responde, califica, agenda visitas y pasa el cliente al humano con todo el contexto. 24/7, sin festivos." delay={0.2}><StepGfx3/></StepCard>
      </div>
    </Container>
  </Section>
);

// ===================== BENEFITS COUNTERS =====================
const BenefitCard = ({ icon: I, value, label, sub, color, delay = 0 }) => (
  <Reveal delay={delay}>
    <div className="relative bg-white rounded-3xl p-6 md:p-7 ring-1 ring-ink-900/5 shadow-card h-full overflow-hidden">
      <div className={`absolute -top-12 -right-12 w-40 h-40 rounded-full ${color} blur-2xl opacity-40`}/>
      <div className="relative">
        <div className={`w-11 h-11 rounded-xl ${color} flex items-center justify-center mb-5`}><I size={20} className="text-ink-900"/></div>
        <div className="font-display font-bold text-[44px] md:text-[52px] leading-none text-ink-900">{value}</div>
        <div className="mt-3 font-medium text-[15px] text-ink-900">{label}</div>
        <div className="mt-1 text-[13px] text-ink-500">{sub}</div>
      </div>
    </div>
  </Reveal>
);

const Benefits = () => (
  <Section className="py-24 md:py-32">
    <Container>
      <Reveal><EyebrowH2 eyebrow="Resultados" title="Los números, no el storytelling." sub="Datos de los pilotos cerrados con 3 inmobiliarias en Alicante y Valencia entre enero y marzo de 2026."/></Reveal>
      <div className="mt-12 grid grid-cols-2 lg:grid-cols-4 gap-4">
        <BenefitCard icon={IBolt} value={<><Counter to={15} />s</>} label="Tiempo medio de respuesta" sub="vs 2h en la industria" color="bg-amber-100"/>
        <BenefitCard icon={ITrend} value={<>+<Counter to={38}/>%</>} label="Leads convertidos en visita" sub="piloto cerrado, 3 agencias" color="bg-emerald-100" delay={0.1}/>
        <BenefitCard icon={IInfinity} value="24/7" label="Sin descanso, sin festivos" sub="ni domingos, ni Navidad" color="bg-selai-100" delay={0.2}/>
        <BenefitCard icon={ITranslate} value={<><Counter to={5}/></>} label="Idiomas nativos" sub="ES · CA · EN · FR · DE" color="bg-rose-100" delay={0.3}/>
      </div>
    </Container>
  </Section>
);

// ===================== CHANNELS TABS =====================
const ChannelsTabs = () => {
  const tabs = [
    { id: 'whatsapp', label: 'WhatsApp', icon: IWhats, color: 'text-emerald-500' },
    { id: 'email',    label: 'Email',    icon: IMail,  color: 'text-blue-500' },
    { id: 'voice',    label: 'Voz',      icon: IPhone, color: 'text-violet-500' },
    { id: 'web',      label: 'Web chat', icon: IChat,  color: 'text-accent' },
    { id: 'idealista',label: 'Idealista',icon: IHouse, color: 'text-selai-500' },
  ];
  const [active, setActive] = React.useState('whatsapp');
  const content = {
    whatsapp: { title: 'Donde el 92% de tus leads ya está.', body: 'WhatsApp Business API oficial. selai responde con voz humana, manda fotos, agenda visitas y reserva al cliente para tu mejor agente.', bullets: ['API oficial Meta', 'Plantillas pre-aprobadas', 'Multi-número, multi-agente'] },
    email:    { title: 'Cero leads perdidos en spam.', body: 'Conecta Gmail o Outlook. selai redacta respuestas con el tono de tu mejor comercial, adjunta fichas y firma con tu marca.', bullets: ['Gmail / Outlook OAuth', 'Detecta intención y urgencia', 'Threading limpio'] },
    voice:    { title: 'Atiende llamadas a las 3 a.m.', body: 'Número virtual o desvío del tuyo. selai contesta, entiende, califica y pasa la llamada al humano con un brief de 3 líneas.', bullets: ['Voz natural en ES, CA, EN', '<700ms latencia', 'Resumen al humano'] },
    web:      { title: 'Cada visita web, una conversación.', body: 'Snippet en tu web. selai sabe qué propiedad están viendo, sugiere alternativas y captura el contacto sin formularios.', bullets: ['1 línea de código', 'Context-aware por URL', 'Personalizable al pixel'] },
    idealista:{ title: 'Responde a Idealista sin abrir Idealista.', body: 'Sincroniza tu bandeja de Idealista, Fotocasa y portales. selai responde como tú lo harías — y consolida el lead en tu CRM.', bullets: ['Idealista, Fotocasa, Habitaclia', 'Detecta lead duplicado', 'Push a tu CRM'] },
  };
  const c = content[active];
  return (
    <Section className="py-24 md:py-32 bg-ink-900 grain text-selai-50">
      <Container>
        <Reveal><EyebrowH2 dark eyebrow="Canales" title="Un solo cerebro. Cinco bocas." sub="Cada canal con sus reglas, su tono, su latencia óptima. Y todo en una sola bandeja consolidada para tu equipo."/></Reveal>
        <div className="mt-12">
          <div className="flex gap-2 overflow-x-auto no-scrollbar pb-2 -mx-2 px-2">
            {tabs.map(t => {
              const I = t.icon;
              const on = active === t.id;
              return (
                <button key={t.id} onClick={()=>setActive(t.id)} className={`flex-shrink-0 inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-[14px] font-medium transition ${on ? 'bg-selai-500 text-white shadow-soft' : 'bg-white/5 text-selai-100/70 hover:bg-white/10 ring-1 ring-white/10'}`}>
                  <I size={16} className={on ? 'text-white' : t.color}/>{t.label}
                </button>
              );
            })}
          </div>
          <div key={active} className="mt-6 grid lg:grid-cols-[0.95fr_1.05fr] gap-8 bg-white/5 ring-1 ring-white/10 rounded-3xl p-7 md:p-10 animate-rise">
            <div>
              <h3 className="font-display font-bold text-[32px] md:text-[40px] leading-tight text-selai-50" style={{textWrap:'balance'}}>{c.title}</h3>
              <p className="mt-4 text-[16px] text-selai-100/75 leading-relaxed">{c.body}</p>
              <ul className="mt-6 space-y-2">
                {c.bullets.map(b => (
                  <li key={b} className="flex items-center gap-3 text-[14px] text-selai-100/90"><div className="w-5 h-5 rounded-full bg-selai-500/30 flex items-center justify-center"><ICheck size={12}/></div>{b}</li>
                ))}
              </ul>
            </div>
            <div className="bg-ink-800/60 rounded-2xl p-6 flex items-center justify-center min-h-[280px] ring-1 ring-white/5">
              <ChannelCard kind={active === 'idealista' ? 'web' : active}/>
            </div>
          </div>
        </div>
      </Container>
    </Section>
  );
};

// ===================== LIVE DEMO CHAT =====================
const LiveDemoChat = () => {
  const [msgs, setMsgs] = React.useState([
    { who: 'ai', t: '¡Hola! Soy selai. Pregúntame lo que quieras saber sobre cómo funciono — RGPD, integraciones, precio, lo que sea.' },
  ]);
  const [input, setInput] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(()=>{ ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: 'smooth'}); }, [msgs, busy]);

  const suggestions = ['¿Qué pasa si dices algo mal?', '¿Cuánto cuesta?', '¿Soportáis Idealista?', '¿Y si quiero hablar con un humano?'];
  const fakeAnswer = (q) => {
    const lower = q.toLowerCase();
    if (lower.includes('precio') || lower.includes('cuesta') || lower.includes('cost')) return 'Plan Starter desde 49€/mes (1 canal, 200 leads). Growth 149€/mes con todos los canales y 1.000 leads. Sin permanencia. Tienes 14 días gratis para probar.';
    if (lower.includes('rgpd') || lower.includes('privac') || lower.includes('dato')) return 'RGPD desde el día 1. Datos en EU (Frankfurt), DPA firmable en el onboarding, redacción automática de PII en logs y guardrail de salida antes de cada respuesta a tu lead.';
    if (lower.includes('idealista') || lower.includes('fotocasa') || lower.includes('portal')) return 'Sí — Idealista, Fotocasa y Habitaclia. Sincronizamos la bandeja del portal, evitamos leads duplicados y empujamos al CRM con un único hilo por contacto.';
    if (lower.includes('human') || lower.includes('escal') || lower.includes('agente')) return 'En cualquier momento. Si el lead pide humano, si la IA detecta sentimiento negativo, o si tú lo configuras por horario — te llega con un brief de 3 líneas para que retomes en frío.';
    if (lower.includes('mal') || lower.includes('error') || lower.includes('equiv')) return 'Tres capas: guardrail determinista (regex de PII y compromisos), guardrail LLM en cada respuesta y "tono auditor" — un humano revisa una muestra cada semana. Si algo se cuela, hay log inmutable y rollback de 1 clic.';
    if (lower.includes('crm')) return 'HubSpot, Pipedrive, Inmovilla y Witei nativos. Para el resto, webhook genérico. Te montamos la integración en la onboarding sin coste extra.';
    return 'Buena pregunta. Para entrar en detalle te paso con el equipo en una llamada de 20 minutos — sin compromiso. ¿Te va el jueves a las 11?';
  };

  const send = (text) => {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    setInput('');
    setMsgs(m => [...m, { who: 'user', t: q }]);
    setBusy(true);
    setTimeout(() => {
      setMsgs(m => [...m, { who: 'ai', t: fakeAnswer(q) }]);
      setBusy(false);
    }, 700 + Math.random() * 600);
  };

  return (
    <Section className="py-24 md:py-32">
      <Container>
        <Reveal>
          <EyebrowH2 eyebrow="Demo en vivo" title="Pregúntale a selai sobre selai." sub="Sí, esto es la misma IA que atendería a tus leads. Si te convence aquí, te convencerá con tus clientes."/>
        </Reveal>
        <Reveal delay={0.1}>
          <div className="mt-10 mx-auto max-w-[760px] bg-white rounded-3xl ring-1 ring-ink-900/5 shadow-card overflow-hidden">
            <div className="flex items-center gap-3 px-5 py-4 border-b border-ink-900/5 bg-paper/50">
              <div className="w-9 h-9 rounded-xl bg-selai-500 flex items-center justify-center"><SelaiLogo dark className="text-[12px]"/></div>
              <div>
                <div className="font-medium text-[14px] text-ink-900">selai</div>
                <div className="text-[11px] text-emerald-500 font-mono flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"/>en línea</div>
              </div>
              <div className="ml-auto font-mono text-[10px] text-ink-300 uppercase tracking-wider">demo · agency: selai-demo</div>
            </div>
            <div ref={ref} className="px-5 py-5 h-[360px] overflow-y-auto space-y-3 bg-gradient-to-b from-white to-selai-50/30">
              {msgs.map((m, i) => (
                <div key={i} className={`flex ${m.who === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-[78%] px-4 py-2.5 rounded-2xl text-[14px] leading-relaxed ${m.who === 'user' ? 'bg-ink-900 text-white rounded-tr-sm' : 'bg-white ring-1 ring-ink-900/5 text-ink-900 rounded-tl-sm shadow-sm'}`}>{m.t}</div>
                </div>
              ))}
              {busy && (
                <div className="flex justify-start">
                  <div className="bg-white ring-1 ring-ink-900/5 rounded-2xl rounded-tl-sm px-4 py-3 flex gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-ink-300 dot"/>
                    <span className="w-1.5 h-1.5 rounded-full bg-ink-300 dot"/>
                    <span className="w-1.5 h-1.5 rounded-full bg-ink-300 dot"/>
                  </div>
                </div>
              )}
            </div>
            <div className="px-5 py-3 border-t border-ink-900/5 bg-paper/40">
              <div className="flex flex-wrap gap-1.5 mb-3">
                {suggestions.map(s => (
                  <button key={s} onClick={()=>send(s)} className="text-[12px] px-2.5 py-1.5 rounded-full bg-selai-100 text-selai-700 hover:bg-selai-200 transition">{s}</button>
                ))}
              </div>
              <form onSubmit={(e)=>{e.preventDefault(); send();}} className="flex gap-2">
                <input value={input} onChange={(e)=>setInput(e.target.value)} placeholder="Escribe tu pregunta…" className="flex-1 px-4 py-3 rounded-xl bg-white ring-1 ring-ink-900/10 focus:ring-selai-400 text-[14px] outline-none"/>
                <button type="submit" disabled={!input || busy} className="px-4 rounded-xl bg-accent text-white disabled:opacity-40 hover:bg-[#FF6A45]"><ISend size={16}/></button>
              </form>
            </div>
          </div>
        </Reveal>
      </Container>
    </Section>
  );
};

// ===================== TESTIMONIAL =====================
const Testimonial = () => (
  <Section className="py-24 md:py-32 bg-gradient-to-b from-paper to-selai-50/40">
    <Container>
      <Reveal>
        <figure className="max-w-4xl mx-auto text-center">
          <div className="font-display text-[80px] md:text-[120px] leading-none text-selai-200 select-none">“</div>
          <blockquote className="font-display font-medium text-[28px] md:text-[44px] leading-[1.18] text-ink-900 -mt-10 md:-mt-14" style={{textWrap:'balance'}}>
            Antes perdíamos leads por la noche. Ahora mi mejor agente trabaja a las 3 a.m. y no se queja.
          </blockquote>
          <figcaption className="mt-8 flex items-center justify-center gap-3">
            <div className="w-11 h-11 rounded-full bg-gradient-to-br from-selai-300 to-accent ring-2 ring-white shadow-card"/>
            <div className="text-left">
              <div className="font-medium text-[14px] text-ink-900">María G.</div>
              <div className="text-[12px] text-ink-500 font-mono">Dirección · Inmobiliaria piloto · Alicante</div>
            </div>
          </figcaption>
        </figure>
      </Reveal>
    </Container>
  </Section>
);

// ===================== PRICING =====================
const Pricing = () => {
  const [annual, setAnnual] = React.useState(true);
  const factor = annual ? 0.8 : 1;
  const tiers = [
    { id:'starter', name:'Starter', price: 49,  desc:'Para abrir un canal y validar.', features:['1 canal a elección','200 leads/mes','Memoria de cliente','Handoff a humano','Dashboard básico','Soporte por email'], cta:'Empezar 14 días gratis' },
    { id:'growth',  name:'Growth',  price: 149, desc:'Lo que recomendamos para una inmobiliaria activa.', features:['Todos los canales (WA, email, voz, web)','1.000 leads/mes','Aprendizaje de tus ventas','Integración con tu CRM','Idealista + Fotocasa','5 idiomas','Soporte prioritario'], cta:'Empezar 14 días gratis', recommended: true },
    { id:'scale',   name:'Scale',   price: null, desc:'Múltiples oficinas, SLA, custom.', features:['Leads ilimitados','SSO + permisos por rol','Modelo dedicado','Integraciones a medida','SLA 99.9%','CSM dedicado','DPA personalizado'], cta:'Hablemos' },
  ];
  return (
    <Section id="precios" className="py-24 md:py-32">
      <Container>
        <Reveal><EyebrowH2 center eyebrow="Precios" title="Empieza por 49 €. Cambia cuando quieras." sub="Sin permanencia, sin setup, sin sorpresas. Cancela en 1 clic."/></Reveal>
        <Reveal delay={0.05}>
          <div className="mt-8 flex items-center justify-center gap-3">
            <span className={`text-[13px] font-medium ${!annual ? 'text-ink-900' : 'text-ink-500'}`}>Mensual</span>
            <button onClick={()=>setAnnual(!annual)} className={`relative w-12 h-6 rounded-full transition ${annual ? 'bg-selai-500' : 'bg-ink-900/20'}`}>
              <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${annual ? 'left-[26px]' : 'left-0.5'}`}/>
            </button>
            <span className={`text-[13px] font-medium ${annual ? 'text-ink-900' : 'text-ink-500'}`}>Anual</span>
            <Pill tone="accent" className="ml-1">−20%</Pill>
          </div>
        </Reveal>
        <div className="mt-12 grid md:grid-cols-3 gap-5">
          {tiers.map((t, i) => (
            <Reveal key={t.id} delay={i*0.08}>
              <div className={`relative rounded-3xl p-7 md:p-8 h-full flex flex-col ${t.recommended ? 'bg-ink-900 text-selai-50 ring-2 ring-selai-500 shadow-glow' : 'bg-white ring-1 ring-ink-900/5 shadow-card'}`}>
                {t.recommended && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                    <Pill tone="accent">Recomendado</Pill>
                  </div>
                )}
                <div className="font-display font-bold text-[24px] mb-1">{t.name}</div>
                <div className={`text-[14px] mb-5 ${t.recommended ? 'text-selai-100/70' : 'text-ink-500'}`}>{t.desc}</div>
                <div className="flex items-baseline gap-1.5 mb-6">
                  {t.price ? (
                    <>
                      <span className="font-display font-bold text-[52px] leading-none">{Math.round(t.price * factor)}</span>
                      <span className={`text-[15px] ${t.recommended ? 'text-selai-100/70' : 'text-ink-500'}`}>€/mes</span>
                    </>
                  ) : (
                    <span className="font-display font-bold text-[40px] leading-none">A medida</span>
                  )}
                </div>
                <ul className="space-y-2.5 flex-1 mb-6">
                  {t.features.map(f => (
                    <li key={f} className={`flex items-start gap-3 text-[14px] ${t.recommended ? 'text-selai-100/90' : 'text-ink-700'}`}>
                      <div className={`mt-0.5 w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0 ${t.recommended ? 'bg-selai-500/30 text-selai-100' : 'bg-selai-100 text-selai-600'}`}><ICheck size={11}/></div>
                      {f}
                    </li>
                  ))}
                </ul>
                <Btn variant={t.recommended ? 'primary' : 'ghost'} size="md" className="w-full">{t.cta}<IArrow size={14}/></Btn>
              </div>
            </Reveal>
          ))}
        </div>
        <Reveal delay={0.3}>
          <p className="mt-10 text-center text-[13px] text-ink-500 font-mono">Sin permanencia · Cancela cuando quieras · RGPD desde el día 1 · Datos en EU</p>
        </Reveal>
      </Container>
    </Section>
  );
};

// ===================== FAQ =====================
const FAQ = () => {
  const items = [
    ['¿Qué pasa si selai dice algo incorrecto a un cliente?', 'Tres capas de control: guardrail determinista (bloquea PII y compromisos no autorizados), guardrail LLM que revisa cada respuesta antes de enviarla, y log inmutable de todo. Si algo se cuela, lo ves, lo reescribes y aprendes de ello.'],
    ['¿Cómo trata mis datos? ¿Cumplís RGPD?', 'Sí — RGPD desde el día 1. Datos alojados en la UE (Frankfurt). DPA firmable en el onboarding. PII redactada en logs. Cero entrenamiento de modelos con tus datos.'],
    ['¿Se integra con mi CRM?', 'HubSpot, Pipedrive, Inmovilla y Witei son nativos. Para el resto, webhook genérico. Te ayudamos en la onboarding.'],
    ['¿Qué portales soportáis?', 'Idealista, Fotocasa y Habitaclia. Sincronizamos bandeja, evitamos duplicados y consolidamos el lead en un único hilo.'],
    ['¿En qué idiomas habla?', 'Castellano, catalán, inglés, francés y alemán de forma nativa. Detección automática del idioma del lead.'],
    ['¿Puedo escalar a un humano?', 'En cualquier momento. Lo configuras por horario, por palabra clave, por sentimiento, o el cliente lo pide. Llega con brief de 3 líneas.'],
    ['¿Cuánto tarda en estar listo?', 'Onboarding de 30 minutos. selai analiza tus últimas 50 conversaciones cerradas y aprende tu tono antes de salir a producción.'],
    ['¿Qué pasa con mis conversaciones cuando me voy?', 'Export completo en JSON y CSV. Borrado certificado en 30 días. Sin lock-in.'],
    ['¿Hay permanencia?', 'No. Mensual o anual (con −20%). Cancela cuando quieras desde el dashboard.'],
    ['¿Quién está detrás de selai?', 'Un equipo pequeño en Alicante. Construido en la Universidad de Alicante. Frustración real con el sector inmobiliario español.'],
    ['¿Personalizáis el tono?', 'selai aprende del tuyo automáticamente. Y tú puedes editar plantillas, frases prohibidas y "no-go zones" en el dashboard.'],
    ['¿Qué pasa si supero el límite de leads?', 'Te avisamos al 80%. Puedes subir de plan o pagar overage por lead (0.05 €). Nunca cortamos servicio sin avisar.'],
    ['¿Soporte humano de verdad?', 'Sí. Email en Starter, prioritario en Growth, CSM dedicado en Scale. Por humanos, en español, en horario laboral CET.'],
    ['¿Qué incluye la prueba gratuita?', '14 días, todas las funciones de Growth, sin tarjeta. Cancelas y nada queda guardado.'],
    ['¿Cómo se factura?', 'Mensual o anual. Stripe. Factura electrónica con tu CIF. Recargo de equivalencia y autónomos contemplados.'],
    ['¿Puedo ver una demo antes?', 'Claro — 20 minutos por videollamada. Te enseñamos selai con datos sintéticos de inmobiliaria y respondemos lo que sea.'],
  ];
  const [open, setOpen] = React.useState(0);
  return (
    <Section id="faq" className="py-24 md:py-32 bg-paper">
      <Container>
        <Reveal>
          <div className="grid lg:grid-cols-[0.9fr_1.1fr] gap-12">
            <div>
              <Pill tone="selai" className="mb-5">FAQ</Pill>
              <h2 className="font-display font-bold text-[40px] md:text-[52px] leading-[1.05] text-ink-900" style={{textWrap:'balance'}}>Las dudas razonables.</h2>
              <p className="mt-5 text-[16px] text-ink-500 leading-relaxed">Si te falta alguna, escribe a <a href="mailto:hola@selai.es" className="text-selai-600 underline decoration-dotted">hola@selai.es</a> o reserva una demo. Sin guion, sin presión.</p>
              <div className="mt-8 hidden lg:block">
                <Btn variant="dark">Reservar demo<IArrow size={14}/></Btn>
              </div>
            </div>
            <div className="space-y-2">
              {items.map(([q, a], i) => {
                const isOpen = open === i;
                return (
                  <div key={i} className={`rounded-2xl ring-1 transition ${isOpen ? 'ring-selai-200 bg-white' : 'ring-ink-900/5 bg-white/60 hover:bg-white'}`}>
                    <button onClick={()=>setOpen(isOpen ? -1 : i)} className="w-full flex items-center justify-between gap-4 px-5 py-4 text-left">
                      <span className={`text-[15px] font-medium ${isOpen ? 'text-ink-900' : 'text-ink-700'}`}>{q}</span>
                      <span className={`flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center transition ${isOpen ? 'bg-selai-500 text-white' : 'bg-ink-900/5 text-ink-700'}`}>
                        {isOpen ? <IMinus size={14}/> : <IPlus size={14}/>}
                      </span>
                    </button>
                    <div className="grid transition-all duration-300" style={{gridTemplateRows: isOpen ? '1fr' : '0fr'}}>
                      <div className="overflow-hidden">
                        <p className="px-5 pb-5 text-[14px] text-ink-500 leading-relaxed">{a}</p>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </Reveal>
      </Container>
    </Section>
  );
};

// ===================== ABOUT US =====================
const AboutUs = () => (
  <Section id="sobre-nosotros" className="py-24 md:py-32">
    <Container>
      <div className="grid lg:grid-cols-[0.95fr_1.05fr] gap-12 items-start">
        <Reveal>
          <Pill tone="selai" className="mb-5">Sobre nosotros</Pill>
          <h2 className="font-display font-bold text-[40px] md:text-[52px] leading-[1.05] text-ink-900" style={{textWrap:'balance'}}>Lo construimos porque nadie nos contestaba al WhatsApp.</h2>
          <div className="mt-6 space-y-4 text-[16px] text-ink-500 leading-[1.65]" style={{textWrap:'pretty'}}>
            <p>Estábamos buscando piso en Alicante. Cinco inmobiliarias no contestaron al WhatsApp en 3 días. Pensamos: si nos pasa a nosotros, le pasa a todos.</p>
            <p>Construimos selai en la Universidad de Alicante con la convicción de que un agente comercial no debería perder leads por dormir. Probamos con tres inmobiliarias del sector. Funcionó.</p>
            <p>Ahora otras inmobiliarias lo usan — y nosotros seguimos en Alicante, escuchando lo que falta.</p>
          </div>
          <div className="mt-7 flex flex-wrap gap-2 font-mono text-[12px] text-ink-500">
            <span className="px-2.5 py-1 rounded-md bg-ink-900/5">Univ. Alicante</span>
            <span className="px-2.5 py-1 rounded-md bg-ink-900/5">2025 →</span>
            <span className="px-2.5 py-1 rounded-md bg-ink-900/5">Bootstrapped</span>
            <span className="px-2.5 py-1 rounded-md bg-ink-900/5">3 piloto · Q1 26</span>
          </div>
        </Reveal>
        <Reveal delay={0.1}>
          <div className="grid grid-cols-2 gap-4">
            {[
              { name: 'Co-founder · Producto', role: 'Producto & GTM', from: 'from-selai-300 to-selai-500' },
              { name: 'Co-founder · IA', role: 'IA & Backend', from: 'from-accent to-amber-400' },
              { name: 'Lead engineer', role: 'Frontend & DX', from: 'from-emerald-300 to-selai-400' },
              { name: 'Asesor inmobiliario', role: 'Domain expert', from: 'from-rose-300 to-accent' },
            ].map((p, i) => (
              <div key={i} className="bg-white rounded-2xl p-5 ring-1 ring-ink-900/5 shadow-card">
                <div className={`aspect-square rounded-xl bg-gradient-to-br ${p.from} mb-4 relative overflow-hidden`}>
                  <div className="absolute inset-0 noise opacity-20"/>
                  <div className="absolute bottom-2 right-2 font-mono text-[10px] text-white/70">// foto</div>
                </div>
                <div className="font-medium text-[14px] text-ink-900">{p.name}</div>
                <div className="text-[12px] text-ink-500 font-mono">{p.role}</div>
                <a className="mt-2 inline-flex items-center gap-1 text-[12px] text-selai-600 hover:text-selai-700"><ILinkedIn size={12}/>LinkedIn</a>
              </div>
            ))}
          </div>
        </Reveal>
      </div>
    </Container>
  </Section>
);

// ===================== CTA FINAL =====================
const CTABook = () => (
  <Section className="py-24 md:py-32 bg-ink-900 text-selai-50 grain relative overflow-hidden">
    <div className="absolute -top-40 left-1/2 -translate-x-1/2 w-[700px] h-[700px] rounded-full bg-selai-500/30 blur-3xl pointer-events-none"/>
    <div className="absolute bottom-0 right-0 w-[400px] h-[400px] rounded-full bg-accent/20 blur-3xl pointer-events-none"/>
    <Container className="relative">
      <div className="grid lg:grid-cols-[1.1fr_0.9fr] gap-10 items-center">
        <div>
          <Pill tone="dark" className="mb-5"><span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse"/>20 minutos</Pill>
          <h2 className="font-display font-bold text-[44px] md:text-[64px] leading-[1.02] text-selai-50" style={{textWrap:'balance'}}>20 minutos para ver si selai vende por ti.</h2>
          <p className="mt-5 text-[17px] text-selai-100/70 leading-relaxed max-w-[560px]" style={{textWrap:'pretty'}}>Sin guion, sin presión, sin "vamos a explorar sinergias". Te enseñamos selai con datos sintéticos y respondemos lo que sea.</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Btn variant="primary" size="lg">Reservar demo<IArrow size={16}/></Btn>
            <Btn variant="ghostDark" size="lg" as="a" href="mailto:hola@selai.es">hola@selai.es</Btn>
          </div>
        </div>
        <div className="bg-ink-800/60 rounded-3xl p-6 ring-1 ring-white/10 backdrop-blur">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2 text-[13px] text-selai-100/80"><ICalendar size={14}/>Octubre 2026</div>
            <div className="flex gap-1">
              <button className="w-7 h-7 rounded-md bg-white/10 hover:bg-white/15 flex items-center justify-center"><IArrow size={12} className="rotate-180"/></button>
              <button className="w-7 h-7 rounded-md bg-white/10 hover:bg-white/15 flex items-center justify-center"><IArrow size={12}/></button>
            </div>
          </div>
          <div className="grid grid-cols-7 gap-1 text-center text-[10px] text-selai-100/50 font-mono uppercase mb-2">
            {['L','M','X','J','V','S','D'].map(d=>(<div key={d}>{d}</div>))}
          </div>
          <div className="grid grid-cols-7 gap-1 text-center text-[12px] mb-5">
            {Array.from({length: 35}, (_, i) => {
              const day = i - 2;
              const isAvail = [3, 5, 7, 10, 12, 14, 17, 19, 21, 24, 26].includes(day);
              const isHot = day === 7;
              if (day < 1 || day > 31) return <div key={i}/>;
              return (
                <button key={i} className={`aspect-square rounded-lg font-mono ${isHot ? 'bg-accent text-white' : isAvail ? 'bg-white/10 hover:bg-selai-500 text-selai-50' : 'text-selai-100/30 cursor-not-allowed'}`}>
                  {day}
                </button>
              );
            })}
          </div>
          <div className="border-t border-white/10 pt-4">
            <div className="text-[12px] text-selai-100/60 mb-2 font-mono">JUE 7 OCT · disponibles</div>
            <div className="grid grid-cols-3 gap-1.5">
              {['09:30', '11:00', '12:30', '15:00', '16:30', '18:00'].map((s, i) => (
                <button key={s} className={`px-2 py-2 rounded-lg text-[12px] font-mono ${i === 1 ? 'bg-accent text-white' : 'bg-white/8 hover:bg-white/15 text-selai-100/90'}`}>{s}</button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </Container>
  </Section>
);

// ===================== FOOTER =====================
const Footer = () => (
  <footer className="bg-paper border-t border-ink-900/5 pt-16 pb-10">
    <Container>
      <div className="grid md:grid-cols-[1.4fr_0.8fr_0.8fr_0.8fr_0.8fr] gap-10 mb-12">
        <div>
          <SelaiLogo className="text-[28px]"/>
          <p className="mt-4 text-[14px] text-ink-500 leading-relaxed max-w-[280px]">El asistente comercial 24/7 para tu inmobiliaria. Hecho en Alicante.</p>
          <div className="mt-5 flex gap-2">
            <a className="w-9 h-9 rounded-lg bg-ink-900/5 hover:bg-ink-900/10 flex items-center justify-center text-ink-700"><ILinkedIn size={16}/></a>
            <a className="w-9 h-9 rounded-lg bg-ink-900/5 hover:bg-ink-900/10 flex items-center justify-center text-ink-700 font-bold text-[14px]">𝕏</a>
          </div>
        </div>
        {[
          ['Producto', ['Cómo funciona','Canales','Precios','Demo en vivo','Changelog']],
          ['Empresa', ['Sobre nosotros','Blog','Casos de éxito','Empleo']],
          ['Legal',   ['Términos','Privacidad','DPA','Cookies']],
          ['Contacto',['hola@selai.es','+34 965 00 00 00','Alicante, España']],
        ].map(([title, items]) => (
          <div key={title}>
            <div className="text-[12px] uppercase tracking-[0.14em] text-ink-500 font-mono mb-4">{title}</div>
            <ul className="space-y-2.5">
              {items.map(it => <li key={it}><a className="text-[14px] text-ink-700 hover:text-ink-900">{it}</a></li>)}
            </ul>
          </div>
        ))}
      </div>
      <div className="pt-6 border-t border-ink-900/5 flex flex-wrap items-center justify-between gap-3 text-[12px] text-ink-500 font-mono">
        <div>© 2026 selai · Hecho con cabeza en Alicante</div>
        <div className="flex items-center gap-4">
          <span>RGPD-ready</span>
          <span>·</span>
          <span>Datos en EU</span>
          <span>·</span>
          <span>v1.0</span>
        </div>
      </div>
    </Container>
  </footer>
);

Object.assign(window, { Header, Hero, LogoStrip, ProblemSolution, HowItWorks, Benefits, ChannelsTabs, LiveDemoChat, Testimonial, Pricing, FAQ, AboutUs, CTABook, Footer });

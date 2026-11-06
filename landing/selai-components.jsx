// Shared components

const SelaiLogo = ({ className = '', dark = false }) => (
  <span className={`font-display font-extrabold tracking-[-0.06em] leading-none ${className}`}>
    <span className={dark ? 'text-selai-50' : 'text-selai-900'}>sel</span>
    <span className="text-accent">ai</span>
    <span className="text-accent">.</span>
  </span>
);

// Pill / eyebrow
const Pill = ({ children, tone = 'selai', className = '' }) => {
  const tones = {
    selai: 'bg-selai-100 text-selai-700 ring-1 ring-selai-200',
    dark:  'bg-white/10 text-selai-100 ring-1 ring-white/15',
    accent:'bg-accent/10 text-accent ring-1 ring-accent/20',
    paper: 'bg-paper text-ink-700 ring-1 ring-black/5',
  };
  return (
    <span className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-[12px] font-medium ${tones[tone]} ${className}`}>
      {children}
    </span>
  );
};

// Buttons
const Btn = ({ children, variant = 'primary', size = 'md', as = 'button', href, className = '', ...rest }) => {
  const sizes = { sm: 'px-3 py-2 text-[13px]', md: 'px-5 py-3 text-[14px]', lg: 'px-6 py-3.5 text-[15px]' };
  const variants = {
    primary: 'bg-accent text-white hover:bg-[#FF6A45] shadow-accent',
    dark:    'bg-ink-900 text-white hover:bg-ink-800',
    selai:   'bg-selai-500 text-white hover:bg-selai-600 shadow-soft',
    ghost:   'bg-transparent text-ink-900 hover:bg-ink-900/5 ring-1 ring-ink-900/10',
    ghostDark:'bg-transparent text-selai-100 hover:bg-white/10 ring-1 ring-white/15',
    link:    'text-selai-600 hover:text-selai-700',
  };
  const Tag = as;
  return (
    <Tag href={href} className={`inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-all duration-200 ${sizes[size]} ${variants[variant]} ${className}`} {...rest}>
      {children}
    </Tag>
  );
};

// Word-by-word stagger reveal
const StaggerWords = ({ text, className = '', delay = 0, wordDelay = 0.045 }) => {
  const words = text.split(' ');
  return (
    <span className={`stagger ${className}`}>
      {words.map((w, i) => (
        <React.Fragment key={i}>
          <span style={{ animationDelay: `${delay + i * wordDelay}s` }}>{w}</span>
          {i < words.length - 1 && <span className="space"> </span>}
        </React.Fragment>
      ))}
    </span>
  );
};

// In-view reveal hook
const useInView = (opts = {}) => {
  const ref = React.useRef(null);
  const [inView, setInView] = React.useState(false);
  React.useEffect(() => {
    if (!ref.current) return;
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) { setInView(true); if (opts.once !== false) obs.disconnect(); }
      });
    }, { rootMargin: opts.margin || '-80px', threshold: opts.threshold || 0.1 });
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);
  return [ref, inView];
};

// Reveal wrapper
const Reveal = ({ children, delay = 0, className = '', y = 24 }) => {
  const [ref, inView] = useInView();
  return (
    <div ref={ref} className={className}
      style={{
        opacity: inView ? 1 : 0,
        transform: inView ? 'translateY(0)' : `translateY(${y}px)`,
        transition: `opacity 0.8s cubic-bezier(0.16,1,0.3,1) ${delay}s, transform 0.8s cubic-bezier(0.16,1,0.3,1) ${delay}s`,
      }}
    >
      {children}
    </div>
  );
};

// Animated counter
const Counter = ({ to, prefix = '', suffix = '', duration = 1400, decimals = 0 }) => {
  const [ref, inView] = useInView();
  const [v, setV] = React.useState(0);
  React.useEffect(() => {
    if (!inView) return;
    const start = performance.now();
    let raf;
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setV(to * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [inView]);
  return <span ref={ref} className="font-mono tabular">{prefix}{v.toFixed(decimals)}{suffix}</span>;
};

// Section frame
const Section = ({ id, children, className = '', dark = false }) => (
  <section id={id} className={`relative ${dark ? 'bg-ink-900 text-selai-50 grain' : ''} ${className}`}>
    {children}
  </section>
);

// Container
const Container = ({ children, className = '' }) => (
  <div className={`mx-auto max-w-[1200px] px-6 md:px-10 ${className}`}>{children}</div>
);

// Eyebrow header
const EyebrowH2 = ({ eyebrow, title, sub, dark = false, center = false }) => (
  <div className={`max-w-3xl ${center ? 'mx-auto text-center' : ''}`}>
    {eyebrow && <Pill tone={dark ? 'dark' : 'selai'} className="mb-5">{eyebrow}</Pill>}
    <h2 className={`font-display font-bold text-[40px] md:text-[56px] leading-[1.02] ${dark ? 'text-selai-50' : 'text-ink-900'}`} style={{textWrap:'balance'}}>{title}</h2>
    {sub && <p className={`mt-5 text-[17px] leading-[1.6] ${dark ? 'text-selai-100/75' : 'text-ink-500'}`} style={{textWrap:'pretty'}}>{sub}</p>}
  </div>
);

Object.assign(window, { SelaiLogo, Pill, Btn, StaggerWords, useInView, Reveal, Counter, Section, Container, EyebrowH2 });

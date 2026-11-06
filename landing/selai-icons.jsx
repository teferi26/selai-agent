// Custom inline icons (line-art and duotone). Kept simple — circles, squares, paths.
const Icon = ({ children, size = 20, stroke = 1.6, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" strokeWidth={stroke}
       stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" className={className}>
    {children}
  </svg>
);

const IBolt   = (p) => <Icon {...p}><path d="M13 3 4 14h7l-1 7 9-11h-7z"/></Icon>;
const ICheck  = (p) => <Icon {...p}><path d="M4 12l5 5L20 6"/></Icon>;
const IArrow  = (p) => <Icon {...p}><path d="M5 12h14M13 6l6 6-6 6"/></Icon>;
const IClock  = (p) => <Icon {...p}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></Icon>;
const IShield = (p) => <Icon {...p}><path d="M12 3 4 6v6c0 5 4 8 8 9 4-1 8-4 8-9V6z"/><path d="M9 12l2 2 4-4"/></Icon>;
const IGlobe  = (p) => <Icon {...p}><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></Icon>;
const ITrend = (p) => <Icon {...p}><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></Icon>;
const IInfinity = (p) => <Icon {...p}><path d="M6.5 12c0-2 1.5-4 4-4 2 0 3 1.5 5 4s3 4 5 4 4-2 4-4-2-4-4-4-3 1.5-5 4-3 4-5 4-4-2-4-4z"/></Icon>;
const IMail   = (p) => <Icon {...p}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 7 9-7"/></Icon>;
const IPhone  = (p) => <Icon {...p}><path d="M5 4h4l2 5-3 2a12 12 0 0 0 5 5l2-3 5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/></Icon>;
const IChat   = (p) => <Icon {...p}><path d="M21 12a8 8 0 1 1-3-6.2L21 5l-1 3.5A8 8 0 0 1 21 12z"/></Icon>;
const IWhats  = (p) => <Icon {...p}><path d="M3 21l1.7-5A8 8 0 1 1 8 19.3z"/><path d="M9 9c0 3 3 6 6 6l1.5-1-2-1-1 .8c-1-.5-2-1.5-2.5-2.5l.8-1-1-2z" fill="currentColor"/></Icon>;
const IMenu   = (p) => <Icon {...p}><path d="M4 7h16M4 12h16M4 17h16"/></Icon>;
const IX      = (p) => <Icon {...p}><path d="M6 6l12 12M18 6 6 18"/></Icon>;
const ISpark  = (p) => <Icon {...p}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l3 3M15 15l3 3M18 6l-3 3M9 15l-3 3"/></Icon>;
const ICompass= (p) => <Icon {...p}><circle cx="12" cy="12" r="9"/><path d="M15.5 8.5 13 13l-4.5 2.5L11 11z" fill="currentColor" opacity="0.2"/><path d="M15.5 8.5 13 13l-4.5 2.5L11 11z"/></Icon>;
const IPath   = (p) => <Icon {...p}><circle cx="6" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 6h6a4 4 0 0 1 0 8h-4a4 4 0 0 0 0 8"/></Icon>;
const ITranslate = (p) => <Icon {...p}><path d="M3 6h10M8 4v2c0 4-2 8-5 10M5 9c0 3 4 6 8 6"/><path d="M14 21l4-10 4 10M16 17h6"/></Icon>;
const IPlay   = (p) => <Icon {...p}><path d="M7 5v14l12-7z" fill="currentColor"/></Icon>;
const IPlus   = (p) => <Icon {...p}><path d="M12 5v14M5 12h14"/></Icon>;
const IMinus  = (p) => <Icon {...p}><path d="M5 12h14"/></Icon>;
const IStar   = (p) => <Icon {...p}><path d="m12 3 2.6 6 6.4.6-4.8 4.4 1.4 6.4L12 17l-5.6 3.4L7.8 14 3 9.6 9.4 9z"/></Icon>;
const IHouse  = (p) => <Icon {...p}><path d="m3 11 9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/></Icon>;
const ICalendar = (p) => <Icon {...p}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></Icon>;
const IPin    = (p) => <Icon {...p}><path d="M12 22s7-7 7-12a7 7 0 0 0-14 0c0 5 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/></Icon>;
const IGhost  = (p) => <Icon {...p}><path d="M5 11a7 7 0 0 1 14 0v9l-2-2-2 2-2-2-2 2-2-2-2 2-2-2z"/><circle cx="9" cy="11" r="1" fill="currentColor"/><circle cx="15" cy="11" r="1" fill="currentColor"/></Icon>;
const IAtom   = (p) => <Icon {...p}><circle cx="12" cy="12" r="2" fill="currentColor"/><ellipse cx="12" cy="12" rx="10" ry="4"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(120 12 12)"/></Icon>;
const ILinkedIn = (p) => <Icon {...p}><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 10v7M8 7v.01M12 17v-4a2 2 0 0 1 4 0v4M12 13v4" fill="currentColor"/></Icon>;
const ISend   = (p) => <Icon {...p}><path d="M22 3 11 14M22 3l-7 18-4-7-7-4z"/></Icon>;

Object.assign(window, {
  Icon, IBolt, ICheck, IArrow, IClock, IShield, IGlobe, ITrend, IInfinity,
  IMail, IPhone, IChat, IWhats, IMenu, IX, ISpark, ICompass, IPath,
  ITranslate, IPlay, IPlus, IMinus, IStar, IHouse, ICalendar, IPin, IGhost,
  IAtom, ILinkedIn, ISend
});

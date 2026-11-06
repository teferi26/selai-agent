// ---------------------------------------------------------------------------
// selai mobile design system — SINGLE source of truth for visual tokens.
//
// Nothing outside this file should hardcode a color. Screens import tokens
// (colors, spacing, radius, type, shadow) and semantic maps (temperature,
// channel, status pills) from here.
//
// Palette: porcelain background, white surfaces, deep ink text, refined
// indigo primary, discreet gold reserved for premium moments only.
// ---------------------------------------------------------------------------
import { Platform, TextStyle, ViewStyle } from "react-native";
import type { ComponentProps } from "react";
import type { Ionicons } from "@expo/vector-icons";

/** Valid Ionicons glyph name — icon maps below are typed with this so an
 * invalid name fails at compile time instead of rendering "?" at runtime. */
export type IconName = ComponentProps<typeof Ionicons>["name"];

// --- Color tokens -----------------------------------------------------------

export const colors = {
  /** Porcelain app background — warm, never dead gray. */
  bg: "#F7F7F5",
  /** Card / sheet surface. */
  surface: "#FFFFFF",
  /** Primary text. */
  ink: "#101223",
  /** Secondary text and metadata. */
  inkSecondary: "#5A5E6E",
  /** Refined indigo — primary actions and brand. */
  primary: "#4F55E5",
  /** Soft indigo wash for pills, active states and AI bubbles. */
  primarySoft: "#EDEEFC",
  /** Discreet gold — ONLY for premium moments (hot badges, completion). */
  gold: "#B99A5B",
  /** Soft gold wash behind gold pills. */
  goldSoft: "#F6F0E3",
  /** Dark bronze for TEXT on goldSoft/porcelain (colors.gold fails contrast). */
  goldInk: "#7E672F",
  /** Success green — solid fills and icons. */
  success: "#1E9E6A",
  successSoft: "#E5F3EC",
  /** Dark green for TEXT on soft/white backgrounds (AA contrast). */
  successInk: "#0F7A4F",
  /** Soft red for destructive / needs-human states — solid fills and icons. */
  danger: "#D8504A",
  dangerSoft: "#FAEAE9",
  /** Dark red for TEXT on soft/white backgrounds (AA contrast). */
  dangerInk: "#B03A34",
  /** Amber — waits and caveats that depend on third parties (Meta's business
   * verification, a phone number we still have to provision). Deliberately a
   * separate family from `gold`: gold means "premium moment", amber means
   * "read this before you expect it to work". */
  warning: "#C08A2E",
  warningSoft: "#FBF1DF",
  /** Dark amber for TEXT on warningSoft/porcelain (AA contrast, 6.0:1). */
  warningInk: "#7A5410",
  /** Hairline borders. */
  hairline: "#E8E8E4",
  /** Light neutral bubble (lead messages) and skeleton base. */
  neutralSoft: "#EEEEEA",
  /** WhatsApp brand green (deep-link actions only). */
  whatsapp: "#1DA855",
  white: "#FFFFFF",
  /** Modal scrim (ink at 50%). */
  overlay: "rgba(16,18,35,0.5)",
};

// --- Spacing (4pt grid) -----------------------------------------------------

/** spacing(1..8) → 4, 8, 12, 16, 20, 24, 28, 32 */
export function spacing(n: number): number {
  return n * 4;
}

// --- Radii ------------------------------------------------------------------

export const radius = {
  card: 16,
  button: 14,
  input: 14,
  small: 10,
  pill: 999,
};

// --- Elevation --------------------------------------------------------------

/** Very subtle card shadow: elevation 1 / shadowOpacity 0.06. */
export const shadow = {
  card: Platform.select<ViewStyle>({
    ios: {
      shadowColor: colors.ink,
      shadowOpacity: 0.06,
      shadowRadius: 8,
      shadowOffset: { width: 0, height: 2 },
    },
    default: { elevation: 1 },
  }) as ViewStyle,
};

// --- Typography -------------------------------------------------------------

export const type = {
  /** Screen titles 22-28, tight tracking. */
  title: {
    fontSize: 24,
    fontWeight: "700",
    letterSpacing: -0.5,
    color: colors.ink,
  } as TextStyle,
  titleLarge: {
    fontSize: 28,
    fontWeight: "700",
    letterSpacing: -0.5,
    color: colors.ink,
  } as TextStyle,
  heading: {
    fontSize: 17,
    fontWeight: "600",
    letterSpacing: -0.2,
    color: colors.ink,
  } as TextStyle,
  /** Body copy 15-16 / 400. */
  body: {
    fontSize: 15,
    fontWeight: "400",
    color: colors.ink,
  } as TextStyle,
  bodySecondary: {
    fontSize: 15,
    fontWeight: "400",
    color: colors.inkSecondary,
  } as TextStyle,
  /** Metadata 13 / 500 secondary. */
  meta: {
    fontSize: 13,
    fontWeight: "500",
    color: colors.inkSecondary,
  } as TextStyle,
  /** Big metric numbers with tabular figures. */
  metric: {
    fontSize: 32,
    fontWeight: "700",
    letterSpacing: -0.5,
    color: colors.ink,
    fontVariant: ["tabular-nums"],
  } as TextStyle,
  /** Verbatim values the user must copy or dial (tokens, URLs, GSM codes).
   * Monospaced so a 1 and an l can't be confused when retyping by hand. */
  mono: {
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }),
    fontSize: 13,
    color: colors.ink,
  } as TextStyle,
};

// --- Buttons (shared specs) -------------------------------------------------

export const button = {
  primaryHeight: 52,
  radius: radius.button,
};

// --- Semantic: lead temperature ---------------------------------------------

export type PillTone = "gold" | "indigo" | "danger" | "success" | "warning" | "neutral";

export const pillTone: Record<PillTone, { bg: string; fg: string }> = {
  gold: { bg: colors.goldSoft, fg: colors.goldInk },
  indigo: { bg: colors.primarySoft, fg: colors.primary },
  danger: { bg: colors.dangerSoft, fg: colors.dangerInk },
  success: { bg: colors.successSoft, fg: colors.successInk },
  warning: { bg: colors.warningSoft, fg: colors.warningInk },
  neutral: { bg: colors.neutralSoft, fg: colors.inkSecondary },
};

export const temperatureTone: Record<string, PillTone> = {
  hot: "gold",
  warm: "indigo",
  cold: "neutral",
};

export const temperatureLabel: Record<string, string> = {
  hot: "Caliente",
  warm: "Templado",
  cold: "Frío",
};

/** Ionicons name per temperature (filter chips). */
export const temperatureIcon: Record<string, IconName> = {
  hot: "flame-outline",
  warm: "sunny-outline",
  cold: "snow-outline",
};

/** Accent color used for temperature rings/score. hot uses gold on purpose. */
export const temperatureColor: Record<string, string> = {
  hot: colors.gold,
  warm: colors.primary,
  cold: colors.inkSecondary,
};

// --- Semantic: channels -----------------------------------------------------

/** Ionicons name per channel. Never use emojis as UI icons. */
export const channelIcon: Record<string, IconName> = {
  whatsapp: "logo-whatsapp",
  email: "mail-outline",
  voice: "call-outline",
  sms: "chatbox-ellipses-outline",
  web: "globe-outline",
};

export const channelLabel: Record<string, string> = {
  whatsapp: "WhatsApp",
  email: "Email",
  voice: "Voz",
  sms: "SMS",
  web: "Web",
};

// --- Semantic: conversation status pill -------------------------------------
// Backend ConversationStatus: active | snoozed | handed_off | closed.

export function conversationPill(
  status: string,
  temperature: string
): { label: string; tone: PillTone } {
  if (status === "handed_off") return { label: "Necesita humano", tone: "danger" };
  if (status === "closed") return { label: "Cerrada", tone: "neutral" };
  if (temperature === "hot") return { label: "Caliente", tone: "gold" };
  return { label: "Atendiendo IA", tone: "indigo" };
}

// --- Semantic: property status ----------------------------------------------
// Backend PropertyStatus: draft | available | reserved | sold | rented |
// archived. Mismo vocabulario que el panel (properties/PropertiesClient.tsx).

export const propertyStatusLabel: Record<string, string> = {
  draft: "Borrador",
  available: "Disponible",
  reserved: "Reservada",
  sold: "Vendida",
  rented: "Alquilada",
  archived: "Archivada",
};

export const propertyStatusTone: Record<string, PillTone> = {
  draft: "neutral",
  available: "success",
  reserved: "warning",
  sold: "neutral",
  rented: "neutral",
  archived: "neutral",
};

// --- Semantic: visit status --------------------------------------------------
// Backend VisitStatus: requested | confirmed | rescheduled | cancelled |
// no_show | completed. Mismo vocabulario que el panel (visitas/page.tsx).

export const visitStatusLabel: Record<string, string> = {
  requested: "Por confirmar",
  confirmed: "Confirmada",
  rescheduled: "Reprogramada",
  cancelled: "Cancelada",
  no_show: "No se presentó",
  completed: "Realizada",
};

export const visitStatusTone: Record<string, PillTone> = {
  requested: "warning",
  confirmed: "success",
  rescheduled: "indigo",
  cancelled: "neutral",
  no_show: "danger",
  completed: "neutral",
};

// --- Avatars ----------------------------------------------------------------

const AVATAR_PALETTE: { bg: string; fg: string }[] = [
  { bg: colors.primarySoft, fg: colors.primary },
  { bg: colors.goldSoft, fg: colors.goldInk },
  { bg: colors.successSoft, fg: colors.successInk },
  { bg: colors.dangerSoft, fg: colors.dangerInk },
  { bg: colors.neutralSoft, fg: colors.inkSecondary },
];

/** Deterministic soft background + foreground derived from the name. */
export function avatarColors(name: string | null | undefined): { bg: string; fg: string } {
  const s = (name || "?").trim();
  let hash = 0;
  for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) >>> 0;
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

/** Up to two initials from a full name ("María López" → "ML"). */
export function initialsOf(name: string | null | undefined): string {
  const s = (name || "").trim();
  if (!s) return "?";
  const parts = s.split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "";
  const second = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + second).toUpperCase() || "?";
}

// Shared UI kit for the selai mobile app. All visual tokens come from
// src/lib/theme.ts — no hardcoded colors here beyond token lookups.
import { ReactNode, useEffect, useRef } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  Easing,
  Linking,
  Pressable,
  StyleProp,
  StyleSheet,
  Text,
  TextStyle,
  View,
  ViewStyle,
} from "react-native";
import * as Clipboard from "expo-clipboard";
import { Ionicons } from "@expo/vector-icons";
import {
  avatarColors,
  button,
  colors,
  IconName,
  initialsOf,
  pillTone,
  PillTone,
  radius,
  shadow,
  spacing,
  type,
} from "@/lib/theme";
import { tapHaptic } from "@/lib/haptics";
import { contextMailtoUrl, SUPPORT_EMAIL } from "@/lib/support";

export type { IconName };

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle | ViewStyle[] }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

export function PrimaryButton({
  label,
  onPress,
  icon,
  disabled,
  loading,
  tone = "primary",
  style,
}: {
  label: string;
  onPress: () => void;
  icon?: IconName;
  disabled?: boolean;
  loading?: boolean;
  /** primary (indigo) | success (green) | danger (soft red) */
  tone?: "primary" | "success" | "danger";
  style?: ViewStyle;
}) {
  const bg =
    tone === "success" ? colors.success : tone === "danger" ? colors.danger : colors.primary;
  return (
    <Pressable
      accessibilityRole="button"
      style={({ pressed }) => [
        styles.primaryBtn,
        { backgroundColor: bg },
        disabled || loading ? styles.btnDisabled : null,
        pressed && !disabled ? styles.btnPressed : null,
        style,
      ]}
      onPress={() => {
        if (disabled || loading) return;
        tapHaptic();
        onPress();
      }}
      disabled={disabled || loading}
    >
      {loading ? (
        <ActivityIndicator color={colors.white} />
      ) : (
        <>
          {icon ? <Ionicons name={icon} size={19} color={colors.white} /> : null}
          <Text style={styles.primaryBtnText}>{label}</Text>
        </>
      )}
    </Pressable>
  );
}

export function SecondaryButton({
  label,
  onPress,
  icon,
  disabled,
  style,
}: {
  label: string;
  onPress: () => void;
  icon?: IconName;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      style={({ pressed }) => [
        styles.secondaryBtn,
        disabled ? styles.btnDisabled : null,
        pressed && !disabled ? styles.btnPressed : null,
        style,
      ]}
      onPress={() => {
        if (disabled) return;
        tapHaptic();
        onPress();
      }}
      disabled={disabled}
    >
      {icon ? <Ionicons name={icon} size={19} color={colors.ink} /> : null}
      <Text style={styles.secondaryBtnText}>{label}</Text>
    </Pressable>
  );
}

/** Discreet text action for a third option that no debe competir con el botón
 * principal (por ejemplo "Reportar un problema" bajo un error). */
export function LinkButton({
  label,
  onPress,
  icon,
}: {
  label: string;
  onPress: () => void;
  icon?: IconName;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      hitSlop={8}
      style={({ pressed }) => [styles.linkBtn, pressed ? styles.btnPressed : null]}
      onPress={() => {
        tapHaptic();
        onPress();
      }}
    >
      {icon ? <Ionicons name={icon} size={16} color={colors.primary} /> : null}
      <Text style={styles.linkBtnText}>{label}</Text>
    </Pressable>
  );
}

/** Round 40pt icon action (call / WhatsApp on lead rows). */
export function IconCircleButton({
  icon,
  onPress,
  color = colors.primary,
  disabled,
  accessibilityLabel,
}: {
  icon: IconName;
  onPress: () => void;
  color?: string;
  disabled?: boolean;
  accessibilityLabel: string;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      hitSlop={6}
      style={({ pressed }) => [
        styles.iconCircle,
        disabled ? styles.btnDisabled : null,
        pressed && !disabled ? styles.btnPressed : null,
      ]}
      onPress={() => {
        if (disabled) return;
        tapHaptic();
        onPress();
      }}
      disabled={disabled}
    >
      <Ionicons name={icon} size={19} color={disabled ? colors.inkSecondary : color} />
    </Pressable>
  );
}

// ---------------------------------------------------------------------------
// Dirección de soporte
//
// "Escríbenos a hola@selai.es" aparecía como texto plano en media docena de
// sitios: había que memorizar la dirección, salir de la app y escribirla a
// mano. Estos dos ayudantes la convierten en algo que se toca.
//
// Importa `@/lib/support` (funciones puras + mailto), no
// `@/components/support` (el formulario): el kit de UI no puede depender del
// modal, que a su vez usa este kit.
// ---------------------------------------------------------------------------

/** La dirección de soporte, pulsable: abre el correo ya dirigido a nosotros. */
export function SupportEmail({
  context,
  style,
}: {
  /** Pantalla o paso donde está el usuario, para situar el mensaje. */
  context?: string;
  style?: StyleProp<TextStyle>;
}) {
  return (
    <Text
      accessibilityRole="link"
      accessibilityLabel={`Escribir a ${SUPPORT_EMAIL}`}
      accessibilityHint="Abre tu aplicación de correo"
      suppressHighlighting
      style={[styles.supportEmail, style]}
      onPress={() => void openSupportMail(context)}
    >
      {SUPPORT_EMAIL}
    </Text>
  );
}

async function openSupportMail(context?: string): Promise<void> {
  tapHaptic();
  try {
    await Linking.openURL(contextMailtoUrl(context));
  } catch {
    // Sin aplicación de correo configurada el enlace no lleva a ninguna parte.
    // Dejar la dirección en el portapapeles es lo único que sigue sirviendo.
    await Clipboard.setStringAsync(SUPPORT_EMAIL).catch(() => {});
    Alert.alert(
      "No hemos podido abrir tu correo",
      `Hemos copiado ${SUPPORT_EMAIL} al portapapeles para que puedas escribirnos desde donde prefieras.`
    );
  }
}

/**
 * Convierte un texto que menciona la dirección de soporte en algo pulsable.
 *
 * Existe porque varios de estos avisos son cadenas construidas en un `catch`
 * (mensajes de error de la API) y no JSX: partir la cadena al pintarla evita
 * reescribir esos manejadores para que devuelvan elementos.
 */
export function withSupportEmail(text: string, context?: string): ReactNode {
  const parts = text.split(SUPPORT_EMAIL);
  if (parts.length === 1) return text;
  return parts.map((part, i) => (
    <Text key={i}>
      {part}
      {i < parts.length - 1 ? <SupportEmail context={context} /> : null}
    </Text>
  ));
}

// ---------------------------------------------------------------------------
// Pills, chips, avatars
// ---------------------------------------------------------------------------

export function Pill({ label, tone = "neutral" }: { label: string; tone?: PillTone }) {
  const t = pillTone[tone];
  return (
    <View style={[styles.pill, { backgroundColor: t.bg }]}>
      <Text style={[styles.pillText, { color: t.fg }]}>{label}</Text>
    </View>
  );
}

export function Chip({
  label,
  active,
  onPress,
  icon,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
  icon?: IconName;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      onPress={() => {
        tapHaptic();
        onPress();
      }}
      style={[styles.chip, active ? styles.chipActive : null]}
    >
      {icon ? (
        <Ionicons
          name={icon}
          size={14}
          color={active ? colors.white : colors.inkSecondary}
        />
      ) : null}
      <Text style={[styles.chipText, active ? styles.chipTextActive : null]}>{label}</Text>
    </Pressable>
  );
}

export function Avatar({
  name,
  size = 44,
}: {
  name: string | null | undefined;
  size?: number;
}) {
  const c = avatarColors(name);
  return (
    <View
      style={[
        styles.avatar,
        { width: size, height: size, borderRadius: size / 2, backgroundColor: c.bg },
      ]}
    >
      <Text style={[styles.avatarText, { color: c.fg, fontSize: size * 0.36 }]}>
        {initialsOf(name)}
      </Text>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Skeletons — animated opacity pulse, no full-screen spinners.
// ---------------------------------------------------------------------------

function usePulse() {
  const opacity = useRef(new Animated.Value(0.5)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 1,
          duration: 700,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 0.5,
          duration: 700,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return opacity;
}

export function Skeleton({
  width,
  height,
  round,
  style,
}: {
  width?: number | `${number}%`;
  height: number;
  round?: boolean;
  style?: ViewStyle;
}) {
  const opacity = usePulse();
  return (
    <Animated.View
      style={[
        {
          width: width ?? "100%",
          height,
          borderRadius: round ? height / 2 : radius.small,
          backgroundColor: colors.neutralSoft,
          opacity,
        },
        style,
      ]}
    />
  );
}

/** Card-shaped skeleton row that mirrors inbox/lead cards. */
export function SkeletonCardRow() {
  return (
    <View style={[styles.card, styles.skeletonRow]}>
      <Skeleton width={44} height={44} round />
      <View style={{ flex: 1, gap: spacing(2) }}>
        <Skeleton width={"55%"} height={14} />
        <Skeleton width={"85%"} height={12} />
        <Skeleton width={"40%"} height={12} />
      </View>
    </View>
  );
}

export function SkeletonList({ rows = 6 }: { rows?: number }) {
  return (
    <View style={styles.skeletonList}>
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonCardRow key={i} />
      ))}
    </View>
  );
}

/** 2-column grid of metric-card skeletons. */
export function SkeletonMetrics() {
  return (
    <View style={styles.skeletonGrid}>
      {Array.from({ length: 6 }, (_, i) => (
        <View key={i} style={[styles.card, styles.skeletonMetricCard]}>
          <Skeleton width={28} height={28} round />
          <Skeleton width={"50%"} height={26} />
          <Skeleton width={"70%"} height={12} />
        </View>
      ))}
    </View>
  );
}

// ---------------------------------------------------------------------------
// Full-screen states
// ---------------------------------------------------------------------------

export function LoadingView({ label = "Cargando..." }: { label?: string }) {
  return (
    <View style={styles.center}>
      <ActivityIndicator size="large" color={colors.primary} />
      <Text style={styles.stateSubtitle}>{label}</Text>
    </View>
  );
}

/**
 * Error state: always offers a retry and, when provided, a second exit so the
 * user is never stuck on a dead screen.
 *
 * `onReport` añade la tercera salida: contárnoslo. Se pasa desde las pantallas
 * que montan el SupportModal (src/components/support.tsx) — este archivo no lo
 * importa para no crear un ciclo entre el kit de UI y el formulario.
 */
export function ErrorView({
  title = "No hemos podido cargar los datos",
  message = "Comprueba tu conexión e inténtalo de nuevo.",
  onRetry,
  exitLabel,
  onExit,
  onReport,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
  exitLabel?: string;
  onExit?: () => void;
  onReport?: () => void;
}) {
  return (
    <View style={styles.center}>
      <View style={styles.stateIconCircle}>
        <Ionicons name="cloud-offline-outline" size={30} color={colors.inkSecondary} />
      </View>
      <Text style={styles.stateTitle}>{title}</Text>
      <Text style={styles.stateSubtitle}>{message}</Text>
      <View style={styles.stateActions}>
        {onRetry ? (
          <PrimaryButton label="Reintentar" icon="refresh-outline" onPress={onRetry} />
        ) : null}
        {onExit && exitLabel ? <SecondaryButton label={exitLabel} onPress={onExit} /> : null}
        {onReport ? (
          <LinkButton
            label="Reportar un problema"
            icon="chatbubble-ellipses-outline"
            onPress={onReport}
          />
        ) : null}
      </View>
    </View>
  );
}

export function EmptyView({
  icon = "file-tray-outline",
  title,
  subtitle,
  ctaLabel,
  onCta,
}: {
  icon?: IconName;
  title: string;
  subtitle?: string;
  ctaLabel?: string;
  onCta?: () => void;
}) {
  return (
    <View style={styles.center}>
      <View style={styles.stateIconCircle}>
        <Ionicons name={icon} size={30} color={colors.primary} />
      </View>
      <Text style={styles.stateTitle}>{title}</Text>
      {subtitle ? <Text style={styles.stateSubtitle}>{subtitle}</Text> : null}
      {ctaLabel && onCta ? (
        <View style={styles.stateActions}>
          <SecondaryButton label={ctaLabel} onPress={onCta} />
        </View>
      ) : null}
    </View>
  );
}

// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    ...shadow.card,
  },
  primaryBtn: {
    height: button.primaryHeight,
    borderRadius: button.radius,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: spacing(2),
    paddingHorizontal: spacing(5),
  },
  primaryBtnText: { color: colors.white, fontWeight: "600", fontSize: 16 },
  secondaryBtn: {
    height: button.primaryHeight,
    borderRadius: button.radius,
    backgroundColor: "transparent",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: spacing(2),
    paddingHorizontal: spacing(5),
  },
  secondaryBtnText: { color: colors.ink, fontWeight: "600", fontSize: 16 },
  linkBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing(2),
    paddingVertical: spacing(3),
  },
  linkBtnText: { color: colors.primary, fontWeight: "600", fontSize: 15 },
  // Hereda tamaño y altura de línea del texto que la envuelve: la dirección
  // vive dentro de frases con estilos muy distintos (aviso, error, nota).
  supportEmail: { color: colors.primary, fontWeight: "600" },
  btnDisabled: { opacity: 0.5 },
  btnPressed: { opacity: 0.85 },
  iconCircle: {
    width: 40,
    height: 40,
    borderRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  pill: {
    paddingHorizontal: spacing(2),
    paddingVertical: 3,
    borderRadius: radius.pill,
    alignSelf: "flex-start",
  },
  pillText: { fontSize: 11, fontWeight: "600" },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(2),
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { ...type.meta },
  chipTextActive: { color: colors.white },
  avatar: { alignItems: "center", justifyContent: "center" },
  avatarText: { fontWeight: "700" },
  skeletonList: { padding: spacing(4), gap: spacing(3) },
  skeletonRow: {
    flexDirection: "row",
    gap: spacing(3),
    padding: spacing(4),
    alignItems: "center",
  },
  skeletonGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing(3),
    padding: spacing(4),
  },
  skeletonMetricCard: {
    width: "47%",
    flexGrow: 1,
    padding: spacing(4),
    gap: spacing(2),
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing(8),
    gap: spacing(2),
    backgroundColor: colors.bg,
  },
  stateIconCircle: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing(1),
  },
  stateTitle: { ...type.heading, textAlign: "center" },
  stateSubtitle: { ...type.bodySecondary, textAlign: "center", lineHeight: 21 },
  stateActions: { marginTop: spacing(3), gap: spacing(2), alignSelf: "stretch" },
});

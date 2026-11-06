// ---------------------------------------------------------------------------
// UI atoms shared by the 9 wizard steps. Every colour, radius and type scale
// comes from src/lib/theme.ts; icons are Ionicons only.
// ---------------------------------------------------------------------------
import { ReactNode, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleProp,
  StyleSheet,
  Text,
  TextInput,
  TextStyle,
  View,
  ViewStyle,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";
import { IconName, Skeleton } from "@/components/ui";
import { successHaptic, tapHaptic } from "@/lib/haptics";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

export function StepLead({ children }: { children: ReactNode }) {
  return <Text style={styles.lead}>{children}</Text>;
}

export function Paragraph({
  children,
  style,
}: {
  children: ReactNode;
  style?: StyleProp<TextStyle>;
}) {
  return <Text style={[styles.paragraph, style]}>{children}</Text>;
}

export function Strong({ children }: { children: ReactNode }) {
  return <Text style={styles.strong}>{children}</Text>;
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <Text style={styles.sectionTitle}>{children}</Text>;
}

/** Small print that qualifies what the app can and cannot do. */
export function HonestNote({ children }: { children: ReactNode }) {
  return <Text style={styles.honest}>{children}</Text>;
}

// ---------------------------------------------------------------------------
// Containers
// ---------------------------------------------------------------------------

export function Panel({
  children,
  style,
}: {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  return <View style={[styles.panel, style]}>{children}</View>;
}

export type NoticeTone = "warning" | "info" | "success" | "danger";

const NOTICE_TONE: Record<NoticeTone, { bg: string; fg: string; icon: IconName }> = {
  warning: { bg: colors.warningSoft, fg: colors.warningInk, icon: "time-outline" },
  info: { bg: colors.primarySoft, fg: colors.primary, icon: "information-circle-outline" },
  success: { bg: colors.successSoft, fg: colors.successInk, icon: "checkmark-circle-outline" },
  danger: { bg: colors.dangerSoft, fg: colors.dangerInk, icon: "alert-circle-outline" },
};

export function Notice({
  tone = "info",
  icon,
  title,
  children,
}: {
  tone?: NoticeTone;
  icon?: IconName;
  title?: string;
  children?: ReactNode;
}) {
  const t = NOTICE_TONE[tone];
  return (
    <View style={[styles.notice, { backgroundColor: t.bg }]}>
      <Ionicons name={icon ?? t.icon} size={18} color={t.fg} style={styles.noticeIcon} />
      <View style={styles.noticeBody}>
        {title ? <Text style={[styles.noticeTitle, { color: t.fg }]}>{title}</Text> : null}
        {typeof children === "string" ? (
          <Text style={[styles.noticeText, { color: t.fg }]}>{children}</Text>
        ) : (
          children
        )}
      </View>
    </View>
  );
}

/** Text styled to sit inside a <Notice> as a child element. */
export function NoticeText({ tone = "info", children }: { tone?: NoticeTone; children: ReactNode }) {
  return <Text style={[styles.noticeText, { color: NOTICE_TONE[tone].fg }]}>{children}</Text>;
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

export function Field({
  label,
  value,
  onChangeText,
  placeholder,
  hint,
  optional,
  keyboardType,
  autoCapitalize = "sentences",
  secureTextEntry,
  mono,
  multiline,
  editable = true,
  onSubmitEditing,
  returnKeyType,
  maxLength,
}: {
  label: string;
  value: string;
  onChangeText: (v: string) => void;
  placeholder?: string;
  hint?: string;
  optional?: boolean;
  keyboardType?: "default" | "url" | "phone-pad" | "email-address";
  autoCapitalize?: "none" | "sentences" | "words" | "characters";
  secureTextEntry?: boolean;
  mono?: boolean;
  multiline?: boolean;
  editable?: boolean;
  onSubmitEditing?: () => void;
  returnKeyType?: "done" | "next" | "go" | "send";
  /** Mismo tope que el backend, para que el fallo sea "no cabe más" mientras
   * escribes y no un 422 genérico al guardar. */
  maxLength?: number;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>
        {label}
        {optional ? <Text style={styles.fieldOptional}> (opcional)</Text> : null}
      </Text>
      <TextInput
        style={[
          styles.input,
          mono ? styles.inputMono : null,
          multiline ? styles.inputMultiline : null,
          editable ? null : styles.inputDisabled,
        ]}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.inkSecondary}
        keyboardType={keyboardType}
        autoCapitalize={autoCapitalize}
        autoCorrect={!mono}
        secureTextEntry={secureTextEntry}
        multiline={multiline}
        editable={editable}
        maxLength={maxLength}
        onSubmitEditing={onSubmitEditing}
        returnKeyType={returnKeyType}
        accessibilityLabel={label}
      />
      {hint ? <Text style={styles.fieldHint}>{hint}</Text> : null}
    </View>
  );
}

/** Zones as chips: type a zone, press "Añadir" (or the return key). */
export function ChipInput({
  label,
  hint,
  placeholder,
  values,
  onChange,
  /** Tope del backend (AgencySettingsPatch.zones: 30 elementos de 80). */
  max = 30,
  maxItemLength = 80,
}: {
  label: string;
  hint?: string;
  placeholder?: string;
  values: string[];
  onChange: (next: string[]) => void;
  max?: number;
  maxItemLength?: number;
}) {
  const [draft, setDraft] = useState("");
  const full = values.length >= max;

  function add() {
    const clean = draft.trim().replace(/,+$/, "").trim();
    if (!clean) return;
    if (values.some((v) => v.toLowerCase() === clean.toLowerCase())) {
      setDraft("");
      return;
    }
    // Silencioso a propósito: el aviso de "ya no caben más" está debajo, y
    // dejar que se envíen 31 sería un 422 sin explicación al guardar.
    if (full) return;
    tapHaptic();
    onChange([...values, clean.slice(0, maxItemLength)]);
    setDraft("");
  }

  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <View style={styles.chipInputRow}>
        <TextInput
          style={[styles.input, styles.chipInputText]}
          value={draft}
          onChangeText={(t) => {
            // Typing a comma is how people write lists: treat it as "añadir".
            if (t.endsWith(",")) {
              setDraft(t.slice(0, -1));
              setTimeout(add, 0);
              return;
            }
            setDraft(t);
          }}
          placeholder={full ? "Ya no caben más" : placeholder}
          placeholderTextColor={colors.inkSecondary}
          onSubmitEditing={add}
          returnKeyType="done"
          blurOnSubmit={false}
          editable={!full}
          maxLength={maxItemLength}
          accessibilityLabel={label}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Añadir zona"
          onPress={add}
          disabled={!draft.trim() || full}
          style={({ pressed }) => [
            styles.chipAddBtn,
            !draft.trim() || full ? styles.disabled : null,
            pressed && draft.trim() && !full ? styles.pressed : null,
          ]}
        >
          <Ionicons name="add" size={20} color={colors.white} />
        </Pressable>
      </View>
      {values.length > 0 ? (
        <View style={styles.chipWrap}>
          {values.map((v) => (
            <Pressable
              key={v}
              accessibilityRole="button"
              accessibilityLabel={`Quitar ${v}`}
              onPress={() => {
                tapHaptic();
                onChange(values.filter((x) => x !== v));
              }}
              style={({ pressed }) => [styles.valueChip, pressed ? styles.pressed : null]}
            >
              <Text style={styles.valueChipText}>{v}</Text>
              <Ionicons name="close" size={13} color={colors.primary} />
            </Pressable>
          ))}
        </View>
      ) : null}
      {full ? (
        <Text style={styles.fieldHint}>
          Has llegado al máximo de {max} zonas. Quita alguna para añadir otra.
        </Text>
      ) : hint ? (
        <Text style={styles.fieldHint}>{hint}</Text>
      ) : null}
    </View>
  );
}

export function CheckRow({
  label,
  checked,
  onToggle,
  busy,
}: {
  label: string;
  checked: boolean;
  onToggle: () => void;
  busy?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityState={{ checked }}
      onPress={() => {
        if (busy) return;
        tapHaptic();
        onToggle();
      }}
      style={({ pressed }) => [styles.checkRow, pressed ? styles.pressed : null]}
    >
      {busy ? (
        <ActivityIndicator size="small" color={colors.primary} style={styles.checkBox} />
      ) : (
        <Ionicons
          name={checked ? "checkbox" : "square-outline"}
          size={22}
          color={checked ? colors.primary : colors.inkSecondary}
          style={styles.checkBox}
        />
      )}
      <Text style={styles.checkLabel}>{label}</Text>
    </Pressable>
  );
}

// ---------------------------------------------------------------------------
// Copyable values
// ---------------------------------------------------------------------------

function useCopy(): [boolean, (value: string) => void] {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  return [
    copied,
    (value: string) => {
      void Clipboard.setStringAsync(value).then(() => {
        successHaptic();
        setCopied(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), 1800);
      });
    },
  ];
}

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, copy] = useCopy();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label ? `Copiar ${label}` : "Copiar"}
      onPress={() => copy(value)}
      style={({ pressed }) => [styles.copyBtn, pressed ? styles.pressed : null]}
    >
      <Ionicons
        name={copied ? "checkmark" : "copy-outline"}
        size={15}
        color={copied ? colors.successInk : colors.primary}
      />
      <Text style={[styles.copyBtnText, copied ? styles.copyBtnTextDone : null]}>
        {copied ? "Copiado" : "Copiar"}
      </Text>
    </Pressable>
  );
}

/** Label + verbatim value + copy. Used for tokens, URLs and GSM codes. */
export function CopyRow({ label, value }: { label?: string; value: string }) {
  return (
    <View style={styles.copyRow}>
      {label ? <Text style={styles.copyRowLabel}>{label}</Text> : null}
      <View style={styles.copyRowBody}>
        <Text style={styles.copyRowValue} selectable numberOfLines={3}>
          {value}
        </Text>
        <CopyButton value={value} label={label} />
      </View>
    </View>
  );
}

/** Dark block for code the user pastes somewhere else (the widget snippet). */
export function CodeBlock({ value }: { value: string }) {
  return (
    <View style={styles.codeBlock}>
      <Text style={styles.codeBlockText} selectable>
        {value}
      </Text>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Disclosure + tabs
// ---------------------------------------------------------------------------

export function Collapsible({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <View style={styles.collapsible}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => {
          tapHaptic();
          setOpen((o) => !o);
        }}
        style={({ pressed }) => [styles.collapsibleHead, pressed ? styles.pressed : null]}
      >
        <Text style={styles.collapsibleTitle}>{title}</Text>
        <Ionicons
          name={open ? "chevron-up" : "chevron-down"}
          size={18}
          color={colors.primary}
        />
      </Pressable>
      {open ? <View style={styles.collapsibleBody}>{children}</View> : null}
    </View>
  );
}

export function Tabs({
  items,
  value,
  onChange,
}: {
  items: { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
}) {
  return (
    <View style={styles.tabs}>
      {items.map((it) => {
        const active = it.key === value;
        return (
          <Pressable
            key={it.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            onPress={() => {
              tapHaptic();
              onChange(it.key);
            }}
            style={({ pressed }) => [
              styles.tab,
              active ? styles.tabActive : null,
              pressed ? styles.pressed : null,
            ]}
          >
            <Text style={[styles.tabText, active ? styles.tabTextActive : null]}>
              {it.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/** Numbered instructions (the portal menu paths, the Meta guide). */
export function NumberedList({ items }: { items: ReactNode[] }) {
  return (
    <View style={styles.numberedList}>
      {items.map((item, i) => (
        <View key={i} style={styles.numberedItem}>
          <View style={styles.numberBadge}>
            <Text style={styles.numberBadgeText}>{i + 1}</Text>
          </View>
          <View style={styles.numberedBody}>
            {typeof item === "string" ? <Text style={styles.paragraph}>{item}</Text> : item}
          </View>
        </View>
      ))}
    </View>
  );
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

/** Skeleton shown while a step loads its own data. */
export function StepSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <View style={styles.skeleton}>
      <Skeleton width={"70%"} height={14} />
      <Skeleton width={"90%"} height={12} />
      {Array.from({ length: rows }, (_, i) => (
        <View key={i} style={styles.skeletonCard}>
          <Skeleton width={"55%"} height={14} />
          <Skeleton width={"85%"} height={12} />
          <Skeleton width={"40%"} height={12} />
        </View>
      ))}
    </View>
  );
}

/**
 * Error inside a step: always a retry, and the wizard footer right below
 * still offers "Saltar por ahora" — nobody gets stuck.
 */
export function StepError({
  message = "No hemos podido cargar esta parte. Comprueba tu conexión.",
  onRetry,
  retryLabel = "Reintentar",
}: {
  message?: string;
  onRetry: () => void;
  retryLabel?: string;
}) {
  return (
    <View style={styles.stepError}>
      <Ionicons name="cloud-offline-outline" size={22} color={colors.inkSecondary} />
      <Text style={styles.stepErrorText}>{message}</Text>
      <Pressable
        accessibilityRole="button"
        onPress={() => {
          tapHaptic();
          onRetry();
        }}
        style={({ pressed }) => [styles.retryBtn, pressed ? styles.pressed : null]}
      >
        <Ionicons name="refresh-outline" size={16} color={colors.primary} />
        <Text style={styles.retryBtnText}>{retryLabel}</Text>
      </Pressable>
    </View>
  );
}

/** Inline result of an action (connect, test, verify). */
export function ResultLine({
  tone,
  children,
}: {
  tone: "success" | "danger" | "warning";
  children: ReactNode;
}) {
  const fg =
    tone === "success"
      ? colors.successInk
      : tone === "danger"
        ? colors.dangerInk
        : colors.warningInk;
  const icon: IconName =
    tone === "success" ? "checkmark-circle" : tone === "danger" ? "close-circle" : "time-outline";
  return (
    <View style={styles.resultLine}>
      <Ionicons name={icon} size={16} color={fg} style={{ marginTop: 1 }} />
      <Text style={[styles.resultText, { color: fg }]}>{children}</Text>
    </View>
  );
}

/** Small "done" chip with a check, for states the backend confirmed. */
export function DoneBadge({ children }: { children: ReactNode }) {
  return (
    <View style={styles.doneBadge}>
      <Ionicons name="checkmark-circle" size={16} color={colors.successInk} />
      <Text style={styles.doneBadgeText}>{children}</Text>
    </View>
  );
}

// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  lead: { ...type.bodySecondary, lineHeight: 22 },
  paragraph: { ...type.body, lineHeight: 22 },
  strong: { fontWeight: "700" },
  sectionTitle: { ...type.heading, fontSize: 15 },
  honest: { ...type.meta, fontWeight: "400", fontSize: 12, lineHeight: 18 },

  panel: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    gap: spacing(3),
    ...shadow.card,
  },

  notice: {
    flexDirection: "row",
    gap: spacing(2),
    borderRadius: radius.small,
    padding: spacing(3),
  },
  noticeIcon: { marginTop: 1 },
  noticeBody: { flex: 1, gap: spacing(1) },
  noticeTitle: { fontSize: 14, fontWeight: "700" },
  noticeText: { fontSize: 13, lineHeight: 19 },

  field: { gap: spacing(2) },
  fieldLabel: { ...type.meta, color: colors.ink, fontSize: 14, fontWeight: "600" },
  fieldOptional: { color: colors.inkSecondary, fontWeight: "400" },
  fieldHint: { ...type.meta, fontWeight: "400", fontSize: 12, lineHeight: 17 },
  input: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.bg,
    borderRadius: radius.input,
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(3),
    fontSize: 15,
    color: colors.ink,
    minHeight: 48,
  },
  inputMono: { ...type.mono, fontSize: 13, minHeight: 48 },
  inputMultiline: { minHeight: 88, textAlignVertical: "top" },
  inputDisabled: { opacity: 0.6 },

  chipInputRow: { flexDirection: "row", gap: spacing(2), alignItems: "center" },
  chipInputText: { flex: 1 },
  chipAddBtn: {
    width: 48,
    height: 48,
    borderRadius: radius.input,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: spacing(2) },
  valueChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    backgroundColor: colors.primarySoft,
    paddingLeft: spacing(3),
    paddingRight: spacing(2),
    paddingVertical: spacing(2),
    borderRadius: radius.pill,
  },
  valueChipText: { fontSize: 13, fontWeight: "600", color: colors.primary },

  checkRow: { flexDirection: "row", alignItems: "center", gap: spacing(2), paddingVertical: spacing(1) },
  checkBox: { width: 22 },
  checkLabel: { ...type.body, flex: 1, fontSize: 14 },

  copyBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(2),
    borderRadius: radius.small,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
  },
  copyBtnText: { fontSize: 13, fontWeight: "600", color: colors.primary },
  copyBtnTextDone: { color: colors.successInk },
  copyRow: { gap: spacing(2) },
  copyRowLabel: { ...type.meta, fontSize: 12 },
  copyRowBody: { flexDirection: "row", alignItems: "center", gap: spacing(2) },
  copyRowValue: {
    ...type.mono,
    flex: 1,
    backgroundColor: colors.neutralSoft,
    borderRadius: radius.small,
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(3),
  },
  codeBlock: {
    backgroundColor: colors.ink,
    borderRadius: radius.small,
    padding: spacing(3),
  },
  codeBlockText: { ...type.mono, color: colors.white, fontSize: 12, lineHeight: 18 },

  collapsible: {
    borderRadius: radius.small,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.bg,
    overflow: "hidden",
  },
  collapsibleHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing(2),
    padding: spacing(3),
  },
  collapsibleTitle: { ...type.body, flex: 1, fontSize: 14, fontWeight: "600" },
  collapsibleBody: {
    paddingHorizontal: spacing(3),
    paddingBottom: spacing(3),
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
    paddingTop: spacing(3),
  },

  tabs: { flexDirection: "row", flexWrap: "wrap", gap: spacing(2) },
  tab: {
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(2),
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
  },
  tabActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  tabText: { ...type.meta },
  tabTextActive: { color: colors.white },

  numberedList: { gap: spacing(3) },
  numberedItem: { flexDirection: "row", gap: spacing(3) },
  numberBadge: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
  },
  numberBadgeText: { fontSize: 12, fontWeight: "700", color: colors.primary },
  numberedBody: { flex: 1, gap: spacing(2) },

  skeleton: { gap: spacing(3) },
  skeletonCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    gap: spacing(2),
  },

  stepError: {
    alignItems: "center",
    gap: spacing(2),
    padding: spacing(5),
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
  },
  stepErrorText: { ...type.bodySecondary, textAlign: "center", lineHeight: 21 },
  retryBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(2),
    borderRadius: radius.small,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
  },
  retryBtnText: { fontSize: 14, fontWeight: "600", color: colors.primary },

  resultLine: { flexDirection: "row", gap: spacing(2), alignItems: "flex-start" },
  resultText: { fontSize: 13, lineHeight: 19, flex: 1 },

  doneBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    backgroundColor: colors.successSoft,
    borderRadius: radius.small,
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(2),
  },
  doneBadgeText: { flex: 1, fontSize: 13, fontWeight: "600", color: colors.successInk, lineHeight: 19 },

  disabled: { opacity: 0.4 },
  pressed: { opacity: 0.85 },
});

// ---------------------------------------------------------------------------
// "Comportamiento de la IA" — el equivalente móvil de /settings/ia en el panel.
//
// Hasta ahora la app no exponía NINGÚN ajuste del agente: se podía leer todo
// lo que la IA había contestado, pero no cambiar cómo habla, qué no puede
// decir ni cuándo atiende. Eso obligaba a abrir el ordenador para lo único que
// un dueño quiere tocar en caliente ("no digas que la comisión es negociable").
//
// Guarda con PATCH /settings, sección a sección y de forma optimista: el
// cambio ya está pintado cuando sale la petición y, si falla, se revierte y se
// dice. Excepción: las instrucciones libres son un texto largo y se guardan
// con un botón — un PATCH por tecla sería absurdo, y revertirlas al fallar
// borraría lo que el dueño acaba de escribir.
//
// Los textos y las reglas son los del panel web a propósito (mismos tonos,
// mismas sugerencias, misma escala de días): ver
// dashboard/src/app/(app)/settings/ia/IAClient.tsx.
// ---------------------------------------------------------------------------
import { useCallback, useEffect, useRef, useState } from "react";
import {
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useAgency } from "@/context/AgencyContext";
import { ErrorView, PrimaryButton, SkeletonList } from "@/components/ui";
import {
  AgencySettings,
  AgencySettingsPatch,
  fetchAgencySettings,
  isTimeout,
  patchAgencySettings,
} from "@/lib/api";
import {
  AiMode,
  AiTone,
  BusinessHours,
  DAYS,
  DEFAULT_HOURS,
  MAX_AI_INSTRUCTIONS,
  MAX_FORBIDDEN_PHRASE,
  checkPhrase,
  hourOptions,
  readBusinessHours,
  readInstructions,
  readMode,
  readPhrases,
  readRecordingNotice,
  readTone,
} from "@/lib/ai-settings";
import { successHaptic, tapHaptic, warningHaptic } from "@/lib/haptics";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

/** Sugerencias del sector, las mismas que el panel. */
const SUGGESTED_PHRASES = [
  "regalamos la comisión",
  "el precio es negociable seguro",
  "garantizamos la venta en 30 días",
];

const INSTRUCTION_EXAMPLES = [
  "No des nunca una valoración del piso por escrito: ofrece que pase un agente a valorarlo sin compromiso.",
  "Antes de proponer una visita, pregunta si necesita hipoteca.",
  "Trabajamos toda la zona centro, aunque el cliente pregunte por un barrio concreto.",
  "Los sábados solo hacemos visitas por la mañana.",
];

type SectionKey = "tone" | "phrases" | "instructions" | "mode" | "recording";

// ---------------------------------------------------------------------------
// Piezas
// ---------------------------------------------------------------------------

function Section({
  title,
  subtitle,
  state,
  error,
  children,
}: {
  title: string;
  subtitle: string;
  state: "idle" | "saving" | "saved";
  /** El fallo se pinta DENTRO de la sección que no ha podido guardar, no en
   * una franja arriba: la pantalla es larga y un aviso al principio queda
   * fuera de la vista justo cuando el usuario está tocando el ajuste. */
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHead}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {state === "saving" ? (
          <Text style={styles.savingMark}>Guardando...</Text>
        ) : state === "saved" ? (
          <View style={styles.savedMark}>
            <Ionicons name="checkmark" size={13} color={colors.successInk} />
            <Text style={styles.savedMarkText}>Guardado</Text>
          </View>
        ) : null}
      </View>
      <Text style={styles.sectionSubtitle}>{subtitle}</Text>
      {error ? (
        <View style={styles.errorBanner} accessibilityLiveRegion="polite">
          <Ionicons name="alert-circle-outline" size={16} color={colors.dangerInk} />
          <Text style={styles.errorBannerText}>{error}</Text>
        </View>
      ) : null}
      <View style={styles.card}>{children}</View>
    </View>
  );
}

/** Tarjeta de opción única (tono, modo de atención). */
function OptionCard({
  title,
  description,
  selected,
  onPress,
  last,
}: {
  title: string;
  description: string;
  selected: boolean;
  onPress: () => void;
  last?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ selected }}
      onPress={() => {
        tapHaptic();
        onPress();
      }}
      style={({ pressed }) => [
        styles.optionRow,
        last ? styles.optionRowLast : null,
        pressed ? styles.pressed : null,
      ]}
    >
      <Ionicons
        name={selected ? "radio-button-on" : "radio-button-off"}
        size={20}
        color={selected ? colors.primary : colors.inkSecondary}
        style={{ marginTop: 1 }}
      />
      <View style={{ flex: 1 }}>
        <Text style={[styles.optionTitle, selected ? { color: colors.primary } : null]}>
          {title}
        </Text>
        <Text style={styles.optionDesc}>{description}</Text>
      </View>
    </Pressable>
  );
}

/** Lista de horas en media en media; el valor guardado siempre está dentro. */
function HourPicker({
  visible,
  title,
  selected,
  onSelect,
  onClose,
}: {
  visible: boolean;
  title: string;
  selected: string;
  onSelect: (value: string) => void;
  onClose: () => void;
}) {
  const options = hourOptions(selected);
  const index = Math.max(options.indexOf(selected), 0);
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.modalBackdrop} onPress={onClose}>
        <Pressable style={styles.modalCard} onPress={(e) => e.stopPropagation()}>
          <Text style={styles.modalTitle}>{title}</Text>
          <FlatList
            data={options}
            keyExtractor={(h) => h}
            style={styles.modalList}
            initialScrollIndex={index}
            getItemLayout={(_, i) => ({ length: 44, offset: 44 * i, index: i })}
            renderItem={({ item }) => (
              <Pressable
                style={[styles.hourRow, item === selected ? styles.hourRowActive : null]}
                onPress={() => {
                  tapHaptic();
                  onSelect(item);
                  onClose();
                }}
              >
                <Text
                  style={[styles.hourText, item === selected ? styles.hourTextActive : null]}
                >
                  {item}
                </Text>
              </Pressable>
            )}
          />
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Pantalla
// ---------------------------------------------------------------------------

export default function AiSettingsScreen() {
  const { me } = useAgency();
  const agencyId = me?.agency_id;
  const agencyName = me?.agency_name || "tu inmobiliaria";

  const [settings, setSettings] = useState<AgencySettings | null>(null);
  const [loadError, setLoadError] = useState(false);

  const [tone, setTone] = useState<AiTone>("cercano");
  const [phrases, setPhrases] = useState<string[]>([]);
  const [newPhrase, setNewPhrase] = useState("");
  /** Por qué no se ha añadido la última frase escrita. */
  const [phraseHint, setPhraseHint] = useState<string | null>(null);
  // Dos estados para el mismo texto: lo que se está escribiendo y lo último
  // que aceptó el servidor. La diferencia es lo que habilita el botón.
  const [instructions, setInstructions] = useState("");
  const [storedInstructions, setStoredInstructions] = useState("");
  const [mode, setMode] = useState<AiMode>("always");
  const [hours, setHours] = useState<BusinessHours>(DEFAULT_HOURS);
  const [recording, setRecording] = useState(true);

  const [saving, setSaving] = useState<SectionKey | null>(null);
  const [saved, setSaved] = useState<SectionKey | null>(null);
  /** Qué sección ha fallado y por qué; el aviso se pinta dentro de ella. */
  const [error, setError] = useState<{ section: SectionKey; message: string } | null>(null);
  const [picker, setPicker] = useState<"start" | "end" | null>(null);

  // El "Guardado ✓" es un acuse, no un estado: si se quedase fijo, al volver
  // media hora después parecería que hay algo pendiente de mirar.
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    []
  );

  const load = useCallback(async () => {
    if (!agencyId) return;
    setLoadError(false);
    try {
      const data = await fetchAgencySettings(agencyId);
      const s = data.settings;
      setSettings(data);
      setTone(readTone(s));
      setPhrases(readPhrases(s));
      setInstructions(readInstructions(s));
      setStoredInstructions(readInstructions(s));
      setMode(readMode(s));
      setHours(readBusinessHours(s));
      setRecording(readRecordingNotice(s));
    } catch {
      setLoadError(true);
    }
  }, [agencyId]);

  useEffect(() => {
    void load();
  }, [load]);

  /**
   * PATCH optimista. El estado ya está pintado cuando llegamos aquí; si falla,
   * `rollback` lo devuelve a donde estaba y se explica por qué. Devuelve si se
   * guardó, para que quien lo necesite (las instrucciones) encadene.
   */
  const save = useCallback(
    async (
      section: SectionKey,
      patch: AgencySettingsPatch,
      rollback: () => void
    ): Promise<boolean> => {
      if (!agencyId) return false;
      setSaving(section);
      setError(null);
      setSaved(null);
      try {
        const updated = await patchAgencySettings(agencyId, patch);
        setSettings(updated);
        successHaptic();
        setSaved(section);
        if (savedTimer.current) clearTimeout(savedTimer.current);
        savedTimer.current = setTimeout(() => setSaved(null), 2500);
        return true;
      } catch (e) {
        rollback();
        warningHaptic();
        setError({
          section,
          message: isTimeout(e)
            ? "Está tardando demasiado y no hemos podido guardar el cambio. Comprueba tu conexión e inténtalo otra vez."
            : "No hemos podido guardar el cambio. Comprueba tu conexión e inténtalo otra vez.",
        });
        return false;
      } finally {
        setSaving(null);
      }
    },
    [agencyId]
  );

  // ── Tono ──
  const chooseTone = useCallback(
    (next: AiTone) => {
      if (next === tone || saving) return;
      const prev = tone;
      setTone(next);
      void save("tone", { ai_tone: next }, () => setTone(prev));
    },
    [tone, saving, save]
  );

  // ── Frases prohibidas ──
  const savePhrases = useCallback(
    (next: string[]) => {
      const prev = phrases;
      setPhrases(next);
      void save("phrases", { forbidden_phrases: next }, () => setPhrases(prev));
    },
    [phrases, save]
  );

  const addPhrase = useCallback(
    (raw: string) => {
      const phrase = raw.trim();
      const { accept, hint } = checkPhrase(phrase, phrases);
      if (!accept) {
        // Con motivo, se explica; sin él (repetida o vacía) el campo se limpia
        // y no se dice nada: la frase ya está donde el usuario quería.
        setPhraseHint(hint);
        if (!hint) setNewPhrase("");
        return;
      }
      setNewPhrase("");
      setPhraseHint(null);
      savePhrases([...phrases, phrase]);
    },
    [phrases, savePhrases]
  );

  // ── Instrucciones ──
  // Sin rollback a propósito: si el PATCH falla, lo último que queremos es
  // borrarle al dueño el texto que acaba de escribir. Se queda en pantalla,
  // sin guardar, y el botón sigue ofreciendo reintentar.
  const saveInstructions = useCallback(async () => {
    const next = instructions.trim();
    const ok = await save("instructions", { ai_instructions: next }, () => {});
    if (ok) setStoredInstructions(next);
  }, [instructions, save]);

  // ── Modo de atención ──
  const chooseMode = useCallback(
    (next: AiMode) => {
      if (next === mode || saving) return;
      const prev = mode;
      setMode(next);
      void save(
        "mode",
        // Con "siempre" el horario se borra en el servidor (mismo criterio que
        // el panel web): no pinta nada un horario que no se aplica. El que hay
        // en pantalla se conserva en memoria, así que volver a "solo fuera de
        // horario" en la misma visita lo reenvía tal cual en lugar de
        // devolverte al horario por defecto.
        { ai_mode: next, business_hours: next === "out_of_hours" ? hours : null },
        () => setMode(prev)
      );
    },
    [mode, hours, saving, save]
  );

  const saveHours = useCallback(
    (next: BusinessHours) => {
      const prev = hours;
      setHours(next);
      void save("mode", { ai_mode: "out_of_hours", business_hours: next }, () =>
        setHours(prev)
      );
    },
    [hours, save]
  );

  const toggleDay = useCallback(
    (day: number) => {
      const next = hours.days.includes(day)
        ? hours.days.filter((d) => d !== day)
        : [...hours.days, day].sort((a, b) => a - b);
      // El backend exige al menos un día (BusinessHours.days, min_length=1).
      // Sin esta guarda el PATCH da 422 y la pantalla diría "inténtalo de
      // nuevo", que es un consejo imposible de cumplir.
      if (next.length === 0) return;
      saveHours({ ...hours, days: next });
    },
    [hours, saveHours]
  );

  // ── Aviso de grabación ──
  const toggleRecording = useCallback(() => {
    const next = !recording;
    setRecording(next);
    void save("recording", { recording_notice: next }, () => setRecording(!next));
  }, [recording, save]);

  const sectionState = (k: SectionKey): "idle" | "saving" | "saved" =>
    saving === k ? "saving" : saved === k ? "saved" : "idle";

  const sectionError = (k: SectionKey): string | null =>
    error?.section === k ? error.message : null;

  if (loadError) {
    return (
      <ErrorView
        title="No hemos podido cargar los ajustes de tu IA"
        message="Sin ellos no podemos enseñarte cómo está configurada. Comprueba tu conexión."
        onRetry={() => void load()}
        exitLabel="Volver a Ajustes"
        onExit={() => router.back()}
      />
    );
  }
  if (!settings) return <SkeletonList rows={4} />;

  const onlyOneDay = hours.days.length === 1;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
    >
      <Text style={styles.lead}>
        Decide cómo habla tu asistente, qué no puede decir y cuándo atiende. Los
        cambios valen para WhatsApp, email, el chat de tu web y las llamadas.
      </Text>

      {/* ── Tono ── */}
      <Section
        title="Tono del asistente"
        subtitle="Cómo quieres que suene con tus clientes. Se guarda al momento."
        state={sectionState("tone")}
        error={sectionError("tone")}
      >
        <OptionCard
          title="Cercano"
          description="Tutea y suena natural, como un compañero de la agencia."
          selected={tone === "cercano"}
          onPress={() => chooseTone("cercano")}
        />
        <OptionCard
          title="Formal"
          description="Trata de usted y mantiene un tono profesional clásico."
          selected={tone === "formal"}
          onPress={() => chooseTone("formal")}
          last
        />
        <View style={styles.sampleWrap}>
          <Text style={styles.sampleLabel}>ASÍ SONARÁ</Text>
          <View style={styles.sampleBubble}>
            <Text style={styles.sampleText}>
              {tone === "cercano"
                ? `¡Hola! Soy el asistente de ${agencyName}. ¿Buscas comprar o alquilar? Cuéntame qué necesitas y te ayudo.`
                : `Buenos días. Le atiende el asistente de ${agencyName}. ¿En qué puedo ayudarle? Si lo desea, puedo informarle sobre nuestras propiedades disponibles.`}
            </Text>
          </View>
        </View>
      </Section>

      {/* ── Frases prohibidas ── */}
      <Section
        title="Frases prohibidas"
        subtitle="El asistente nunca dirá estas frases, aunque el cliente insista. Revisamos cada mensaje antes de enviarlo: si alguna se le escapa, el mensaje no sale, la conversación pasa a tu equipo y te avisamos."
        state={sectionState("phrases")}
        error={sectionError("phrases")}
      >
        <View style={styles.cardBody}>
          {phrases.length > 0 ? (
            <View style={styles.chipWrap}>
              {phrases.map((p) => (
                <Pressable
                  key={p}
                  accessibilityRole="button"
                  accessibilityLabel={`Quitar la frase ${p}`}
                  onPress={() => {
                    tapHaptic();
                    savePhrases(phrases.filter((x) => x !== p));
                  }}
                  style={({ pressed }) => [styles.chip, pressed ? styles.pressed : null]}
                >
                  <Text style={styles.chipText}>«{p}»</Text>
                  <Ionicons name="close" size={13} color={colors.inkSecondary} />
                </Pressable>
              ))}
            </View>
          ) : null}

          <View style={styles.addRow}>
            <TextInput
              style={styles.addInput}
              value={newPhrase}
              onChangeText={(t) => {
                setNewPhrase(t);
                setPhraseHint(null);
              }}
              placeholder={`Ej. «${SUGGESTED_PHRASES[0]}»`}
              placeholderTextColor={colors.inkSecondary}
              maxLength={MAX_FORBIDDEN_PHRASE}
              onSubmitEditing={() => addPhrase(newPhrase)}
              returnKeyType="done"
              accessibilityLabel="Nueva frase prohibida"
            />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Añadir frase prohibida"
              onPress={() => {
                tapHaptic();
                addPhrase(newPhrase);
              }}
              disabled={!newPhrase.trim() || saving === "phrases"}
              style={({ pressed }) => [
                styles.addBtn,
                !newPhrase.trim() || saving === "phrases" ? styles.disabled : null,
                pressed ? styles.pressed : null,
              ]}
            >
              <Ionicons name="add" size={20} color={colors.white} />
            </Pressable>
          </View>
          {phraseHint ? <Text style={styles.warnHint}>{phraseHint}</Text> : null}
          <Text style={styles.hint}>
            Da igual mayúsculas, tildes, singular o plural y los signos de puntuación.
            Escribe frases, no palabras sueltas: cuanto más concreta, menos
            conversaciones se te pasan al equipo sin hacer falta.
          </Text>

          {SUGGESTED_PHRASES.some(
            (sug) => !phrases.some((p) => p.toLowerCase() === sug.toLowerCase())
          ) ? (
            <View style={styles.suggestions}>
              <Text style={styles.hint}>Sugerencias típicas del sector — toca para añadir:</Text>
              <View style={styles.chipWrap}>
                {SUGGESTED_PHRASES.filter(
                  (sug) => !phrases.some((p) => p.toLowerCase() === sug.toLowerCase())
                ).map((sug) => (
                  <Pressable
                    key={sug}
                    accessibilityRole="button"
                    accessibilityLabel={`Añadir la frase ${sug}`}
                    onPress={() => {
                      tapHaptic();
                      addPhrase(sug);
                    }}
                    style={({ pressed }) => [
                      styles.suggestChip,
                      pressed ? styles.pressed : null,
                    ]}
                  >
                    <Ionicons name="add" size={13} color={colors.primary} />
                    <Text style={styles.suggestChipText}>«{sug}»</Text>
                  </Pressable>
                ))}
              </View>
            </View>
          ) : null}
        </View>
      </Section>

      {/* ── Instrucciones libres ── */}
      <Section
        title="Instrucciones para tu asistente"
        subtitle="Cuéntale cómo trabajáis vosotros, con tus palabras. Lo tendrá en cuenta en todas las conversaciones, desde ahora y hasta que lo cambies."
        state={sectionState("instructions")}
        error={sectionError("instructions")}
      >
        <View style={styles.cardBody}>
          <TextInput
            style={styles.textArea}
            value={instructions}
            onChangeText={(t) => setInstructions(t.slice(0, MAX_AI_INSTRUCTIONS))}
            placeholder="Ej. Antes de proponer una visita, pregunta siempre si necesita hipoteca."
            placeholderTextColor={colors.inkSecondary}
            multiline
            textAlignVertical="top"
            maxLength={MAX_AI_INSTRUCTIONS}
            accessibilityLabel="Instrucciones para el asistente"
          />
          <Text
            style={[
              styles.counter,
              instructions.length > MAX_AI_INSTRUCTIONS - 100
                ? { color: colors.warningInk }
                : null,
            ]}
          >
            {instructions.length}/{MAX_AI_INSTRUCTIONS} caracteres
          </Text>
          <PrimaryButton
            label="Guardar instrucciones"
            icon="save-outline"
            loading={saving === "instructions"}
            disabled={instructions.trim() === storedInstructions}
            onPress={() => void saveInstructions()}
          />

          {INSTRUCTION_EXAMPLES.some((ex) => !instructions.includes(ex)) ? (
            <View style={styles.suggestions}>
              <Text style={styles.hint}>
                Ejemplos que suelen funcionar — toca para añadirlos:
              </Text>
              {INSTRUCTION_EXAMPLES.filter((ex) => !instructions.includes(ex)).map((ex) => (
                <Pressable
                  key={ex}
                  accessibilityRole="button"
                  accessibilityLabel={`Añadir el ejemplo: ${ex}`}
                  onPress={() => {
                    tapHaptic();
                    setInstructions((cur) =>
                      (cur.trim() ? `${cur.trim()}\n${ex}` : ex).slice(0, MAX_AI_INSTRUCTIONS)
                    );
                  }}
                  style={({ pressed }) => [
                    styles.exampleRow,
                    pressed ? styles.pressed : null,
                  ]}
                >
                  <Ionicons name="add" size={15} color={colors.primary} />
                  <Text style={styles.exampleText}>{ex}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}

          <Text style={styles.hint}>
            Estas instrucciones ajustan cómo trabaja, no sustituyen a sus normas:
            seguirá sin inventarse pisos ni precios, sin ocultar que es una IA y sin
            decir ninguna de tus frases prohibidas, aunque aquí escribas lo contrario.
          </Text>
        </View>
      </Section>

      {/* ── Modo de atención ── */}
      <Section
        title="¿Cuándo atiende tu asistente?"
        subtitle="Puede atender siempre, o solo cuando tu equipo no está disponible."
        state={sectionState("mode")}
        error={sectionError("mode")}
      >
        <OptionCard
          title="Siempre (24/7)"
          description="El asistente responde a cualquier hora, todos los días."
          selected={mode === "always"}
          onPress={() => chooseMode("always")}
        />
        <OptionCard
          title="Solo fuera de horario"
          description="Dentro de tu horario atiende tu equipo; fuera, entra el asistente."
          selected={mode === "out_of_hours"}
          onPress={() => chooseMode("out_of_hours")}
          last
        />

        {mode === "out_of_hours" ? (
          <View style={styles.hoursBlock}>
            <Text style={styles.hoursTitle}>
              Tu horario de atención (el asistente cubre el resto)
            </Text>
            <View style={styles.hoursRow}>
              <Text style={styles.hoursLabel}>De</Text>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Hora de inicio, ${hours.start}`}
                onPress={() => {
                  tapHaptic();
                  setPicker("start");
                }}
                style={({ pressed }) => [styles.hourBtn, pressed ? styles.pressed : null]}
              >
                <Text style={styles.hourBtnText}>{hours.start}</Text>
              </Pressable>
              <Text style={styles.hoursLabel}>a</Text>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Hora de fin, ${hours.end}`}
                onPress={() => {
                  tapHaptic();
                  setPicker("end");
                }}
                style={({ pressed }) => [styles.hourBtn, pressed ? styles.pressed : null]}
              >
                <Text style={styles.hourBtnText}>{hours.end}</Text>
              </Pressable>
            </View>

            <View style={styles.daysRow}>
              {DAYS.map((d) => {
                const active = hours.days.includes(d.value);
                const isLast = active && onlyOneDay;
                return (
                  <Pressable
                    key={d.value}
                    accessibilityRole="button"
                    accessibilityLabel={d.name}
                    accessibilityState={{ selected: active, disabled: isLast }}
                    disabled={isLast}
                    onPress={() => {
                      tapHaptic();
                      toggleDay(d.value);
                    }}
                    style={({ pressed }) => [
                      styles.dayBtn,
                      active ? styles.dayBtnActive : null,
                      isLast ? styles.disabled : null,
                      pressed && !isLast ? styles.pressed : null,
                    ]}
                  >
                    <Text style={[styles.dayText, active ? styles.dayTextActive : null]}>
                      {d.short}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
            {onlyOneDay ? (
              <Text style={styles.hint}>
                Tiene que quedar al menos un día. Marca otro antes de quitar este.
              </Text>
            ) : null}
          </View>
        ) : null}
      </Section>

      {/* ── Aviso de grabación ── */}
      <Section
        title="Aviso de grabación en llamadas"
        subtitle="Si lo activas, el asistente avisa al empezar la llamada de que puede ser grabada."
        state={sectionState("recording")}
        error={sectionError("recording")}
      >
        <View style={styles.switchRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.optionTitle}>Avisar de que la llamada puede ser grabada</Text>
            <Text style={styles.optionDesc}>
              Recomendado: la normativa exige informar de la grabación a quien llama.
            </Text>
          </View>
          <Switch
            value={recording}
            onValueChange={() => {
              tapHaptic();
              toggleRecording();
            }}
            disabled={saving === "recording"}
            trackColor={{ true: colors.primary, false: colors.hairline }}
            thumbColor={colors.white}
          />
        </View>
      </Section>

      <HourPicker
        visible={picker === "start"}
        title="Empezáis a atender a las"
        selected={hours.start}
        onSelect={(v) => saveHours({ ...hours, start: v })}
        onClose={() => setPicker(null)}
      />
      <HourPicker
        visible={picker === "end"}
        title="Termináis de atender a las"
        selected={hours.end}
        onSelect={(v) => saveHours({ ...hours, end: v })}
        onClose={() => setPicker(null)}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing(4), paddingBottom: spacing(12), gap: spacing(2) },
  lead: { ...type.bodySecondary, lineHeight: 21, marginBottom: spacing(2) },

  section: { marginBottom: spacing(5) },
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    marginBottom: spacing(1),
  },
  sectionTitle: { ...type.heading, fontSize: 16, flexShrink: 1 },
  savingMark: { ...type.meta, fontSize: 12, fontWeight: "400" },
  savedMark: { flexDirection: "row", alignItems: "center", gap: 2 },
  savedMarkText: { fontSize: 12, fontWeight: "600", color: colors.successInk },
  sectionSubtitle: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 12.5,
    lineHeight: 18,
    marginBottom: spacing(3),
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    overflow: "hidden",
    ...shadow.card,
  },
  cardBody: { padding: spacing(4), gap: spacing(3) },

  optionRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing(3),
    padding: spacing(4),
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  optionRowLast: { borderBottomWidth: 0 },
  optionTitle: { ...type.body, fontWeight: "600", fontSize: 15 },
  optionDesc: { ...type.meta, fontWeight: "400", fontSize: 12.5, lineHeight: 18, marginTop: 2 },

  sampleWrap: {
    padding: spacing(4),
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
    backgroundColor: colors.bg,
    gap: spacing(2),
  },
  sampleLabel: { ...type.meta, fontSize: 10.5, letterSpacing: 0.6 },
  sampleBubble: {
    backgroundColor: colors.primarySoft,
    borderRadius: radius.card,
    borderBottomLeftRadius: radius.small / 2,
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(3),
  },
  sampleText: { ...type.body, color: colors.primary, fontSize: 14, lineHeight: 20 },

  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: spacing(2) },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    backgroundColor: colors.neutralSoft,
    paddingLeft: spacing(3),
    paddingRight: spacing(2),
    paddingVertical: spacing(2),
    borderRadius: radius.pill,
  },
  chipText: { fontSize: 13, color: colors.ink, flexShrink: 1 },
  addRow: { flexDirection: "row", gap: spacing(2), alignItems: "center" },
  addInput: {
    flex: 1,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.input,
    paddingHorizontal: spacing(3),
    height: 48,
    backgroundColor: colors.bg,
    color: colors.ink,
    fontSize: 15,
  },
  addBtn: {
    width: 48,
    height: 48,
    borderRadius: radius.input,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  suggestions: { gap: spacing(2) },
  suggestChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    borderWidth: StyleSheet.hairlineWidth,
    borderStyle: "dashed",
    borderColor: colors.hairline,
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(2),
    borderRadius: radius.pill,
  },
  suggestChipText: { fontSize: 13, color: colors.primary, flexShrink: 1 },
  exampleRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing(2),
    borderWidth: StyleSheet.hairlineWidth,
    borderStyle: "dashed",
    borderColor: colors.hairline,
    borderRadius: radius.small,
    padding: spacing(3),
  },
  exampleText: { ...type.bodySecondary, fontSize: 13, lineHeight: 19, flex: 1 },

  textArea: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.input,
    backgroundColor: colors.bg,
    color: colors.ink,
    fontSize: 15,
    minHeight: 130,
    padding: spacing(3),
  },
  counter: { ...type.meta, fontWeight: "400", fontSize: 11.5, textAlign: "right" },

  hoursBlock: {
    padding: spacing(4),
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
    backgroundColor: colors.bg,
    gap: spacing(3),
  },
  hoursTitle: { ...type.body, fontWeight: "600", fontSize: 14 },
  hoursRow: { flexDirection: "row", alignItems: "center", gap: spacing(3) },
  hoursLabel: { ...type.bodySecondary, fontSize: 14 },
  hourBtn: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.small,
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(2),
    backgroundColor: colors.surface,
  },
  hourBtnText: { color: colors.primary, fontWeight: "600", fontSize: 15 },
  daysRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing(2) },
  dayBtn: {
    width: 38,
    height: 38,
    borderRadius: 19,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  dayBtnActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  dayText: { fontSize: 14, fontWeight: "600", color: colors.inkSecondary },
  dayTextActive: { color: colors.white },

  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(3),
    padding: spacing(4),
  },

  hint: { ...type.meta, fontWeight: "400", fontSize: 12, lineHeight: 18 },
  warnHint: { fontSize: 12.5, lineHeight: 18, color: colors.warningInk },
  errorBanner: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing(2),
    backgroundColor: colors.dangerSoft,
    borderRadius: radius.small,
    padding: spacing(3),
    marginBottom: spacing(3),
  },
  errorBannerText: {
    ...type.bodySecondary,
    color: colors.dangerInk,
    fontSize: 13,
    lineHeight: 18,
    flexShrink: 1,
  },

  modalBackdrop: {
    flex: 1,
    backgroundColor: colors.overlay,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing(8),
  },
  modalCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    padding: spacing(4),
    width: "100%",
    maxWidth: 320,
    maxHeight: "70%",
  },
  modalTitle: {
    ...type.heading,
    fontSize: 16,
    marginBottom: spacing(2),
    textAlign: "center",
  },
  modalList: { flexGrow: 0 },
  hourRow: {
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radius.small,
  },
  hourRowActive: { backgroundColor: colors.primarySoft },
  hourText: { ...type.body },
  hourTextActive: { color: colors.primary, fontWeight: "700" },

  disabled: { opacity: 0.4 },
  pressed: { opacity: 0.85 },
});

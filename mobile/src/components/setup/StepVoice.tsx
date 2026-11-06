// Paso 2 · La voz de tu IA — catálogo real (GET /voice/catalog), preescucha
// para las voces de ElevenLabs (POST /voice/preview → audio/mpeg, se descarga
// a la caché y se reproduce con expo-audio) y PUT /voice al elegir.
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { setAudioModeAsync, useAudioPlayer } from "expo-audio";
import { isTimeout } from "@/lib/api";
import { useSetupWizard } from "@/context/SetupWizardContext";
import {
  PreviewUnavailableError,
  VoiceCatalogItem,
  downloadVoicePreview,
  fetchVoiceCatalog,
  setAgencyVoice,
} from "@/lib/setup-api";
import {
  DoneBadge,
  HonestNote,
  Notice,
  NoticeText,
  StepError,
  StepLead,
  StepSkeleton,
} from "@/components/setup/kit";
import { SupportEmail } from "@/components/ui";
import { successHaptic, tapHaptic } from "@/lib/haptics";
import { colors, radius, shadow, spacing, type } from "@/lib/theme";

const GENDER_LABEL: Record<string, string> = {
  male: "Masculina",
  female: "Femenina",
  neutral: "Neutra",
};

/** Viaja en el correo de soporte para que nos llegue ya situado. */
const SUPPORT_CONTEXT = "Puesta en marcha · Paso 2 (voz)";

export default function StepVoice() {
  const { agencyId, settings, refreshSettings } = useSetupWizard();
  const selectedId =
    (typeof settings?.settings.voice_id === "string" ? settings.settings.voice_id : null) ||
    (typeof settings?.settings.elevenlabs_voice_id === "string"
      ? settings.settings.elevenlabs_voice_id
      : null);

  const [catalog, setCatalog] = useState<VoiceCatalogItem[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  /** El backend responde `applied_to_vapi: false` cuando ha guardado la voz
   * pero no ha podido aplicarla al asistente que atiende las llamadas (no hay
   * número todavía, o Vapi no contestó). Decirlo es la diferencia entre
   * "elegida" y "elegida y sonando". */
  const [pendingVoice, setPendingVoice] = useState(false);

  const [loadingPreviewId, setLoadingPreviewId] = useState<string | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // El hook mantiene un único reproductor y lo libera automáticamente al
  // desmontar. Cambiar de voz reemplaza la fuente en vez de acumular players.
  const previewPlayer = useAudioPlayer(null, { updateInterval: 250 });
  const playbackSubscriptionRef = useRef<{ remove: () => void } | null>(null);
  const playbackStartedRef = useRef(false);
  // El estado de React sirve para pintar; este ref bloquea inmediatamente una
  // segunda pulsación antes de que llegue el siguiente render.
  const activePreviewIdRef = useRef<string | null>(null);
  /** Sube en cada escucha (y en cada parada). Una descarga que termina con un
   * número viejo ya no manda: el usuario pidió otra cosa o se fue del paso. */
  const previewTokenRef = useRef(0);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    setLoadError(false);
    setCatalog(null);
    try {
      setCatalog(await fetchVoiceCatalog());
    } catch {
      setLoadError(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Nunca dejar sonando una voz al salir del paso. Invalidamos también el
  // token: si la muestra estaba descargándose, al llegar se descarta en vez
  // de empezar a sonar sobre la pantalla siguiente.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      previewTokenRef.current += 1;
      playbackSubscriptionRef.current?.remove();
      playbackSubscriptionRef.current = null;
      playbackStartedRef.current = false;
      activePreviewIdRef.current = null;
      try {
        previewPlayer.pause();
        previewPlayer.replace(null);
      } catch {
        // El hook puede haber liberado ya el player durante el desmontaje.
      }
    };
  }, [previewPlayer]);

  /** Descarta la reproducción actual y cualquier descarga en vuelo. */
  const stopPreview = useCallback(async () => {
    previewTokenRef.current += 1;
    playbackSubscriptionRef.current?.remove();
    playbackSubscriptionRef.current = null;
    playbackStartedRef.current = false;
    activePreviewIdRef.current = null;
    if (mountedRef.current) {
      setPlayingId(null);
      setLoadingPreviewId(null);
    }
    try {
      previewPlayer.pause();
    } catch {
      // Ya estaba parada: nada que hacer.
    }
    try {
      // Libera inmediatamente la fuente; el hook libera el player completo
      // cuando el paso desaparece.
      previewPlayer.replace(null);
    } catch {
      // Idem.
    }
  }, [previewPlayer]);

  const playPreview = useCallback(
    async (voice: VoiceCatalogItem) => {
      if (!agencyId) return;
      const wasActive = activePreviewIdRef.current === voice.id;
      await stopPreview();
      if (wasActive) {
        if (mountedRef.current) setLoadingPreviewId(null);
        return;
      }

      const token = previewTokenRef.current;
      activePreviewIdRef.current = voice.id;
      setPreviewError(null);
      setLoadingPreviewId(voice.id);
      try {
        // Sin esto no se oye nada en un iPhone con el silencio puesto.
        await setAudioModeAsync({ playsInSilentMode: true });
        const uri = await downloadVoicePreview(agencyId, voice.provider, voice.id);
        if (!mountedRef.current || previewTokenRef.current !== token) {
          // El usuario se fue del paso, paró, o pidió otra voz mientras esto
          // se descargaba: no activamos esa fuente obsoleta.
          return;
        }

        playbackStartedRef.current = false;
        const subscription = previewPlayer.addListener(
          "playbackStatusUpdate",
          (status) => {
            if (!mountedRef.current || previewTokenRef.current !== token) return;
            if (status.playing) playbackStartedRef.current = true;
            if (status.error) {
              setPreviewError(
                `La reproducción de ${voice.name} se ha interrumpido. Inténtalo de nuevo.`
              );
              void stopPreview();
            } else if (status.didJustFinish && playbackStartedRef.current) {
              void stopPreview();
            }
          }
        );
        playbackSubscriptionRef.current = subscription;
        previewPlayer.replace({ uri });
        previewPlayer.play();
        setPlayingId(voice.id);
      } catch (e) {
        if (!mountedRef.current || previewTokenRef.current !== token) return;
        await stopPreview();
        if (e instanceof PreviewUnavailableError) {
          setPreviewError(
            `La preescucha de ${voice.name} no está disponible ahora mismo. Puedes elegirla igualmente y cambiarla luego.`
          );
        } else if (isTimeout(e)) {
          setPreviewError(
            `La muestra de ${voice.name} está tardando demasiado. Inténtalo de nuevo en un minuto.`
          );
        } else {
          setPreviewError(
            `No hemos podido reproducir la muestra de ${voice.name}. Inténtalo de nuevo.`
          );
        }
      } finally {
        if (mountedRef.current && previewTokenRef.current === token) {
          setLoadingPreviewId(null);
        }
      }
    },
    [agencyId, previewPlayer, stopPreview]
  );

  const chooseVoice = useCallback(
    async (voice: VoiceCatalogItem) => {
      if (!agencyId || savingId) return;
      await stopPreview();
      setSaveError(null);
      setSavingId(voice.id);
      try {
        const out = await setAgencyVoice(agencyId, {
          provider: voice.provider,
          voice_id: voice.id,
          voice_name: voice.name,
        });
        setPendingVoice(!out.applied_to_vapi);
        await refreshSettings();
        successHaptic();
      } catch (e) {
        if (!mountedRef.current) return;
        setSaveError(
          isTimeout(e)
            ? "Está tardando demasiado. Comprueba tu conexión y vuelve a tocar la voz."
            : "No hemos podido guardar la voz. Comprueba tu conexión y vuelve a tocarla."
        );
      } finally {
        if (mountedRef.current) setSavingId(null);
      }
    },
    [agencyId, savingId, stopPreview, refreshSettings]
  );

  if (loadError) {
    return (
      <View style={{ gap: spacing(4) }}>
        <StepLead>Así sonará tu asistente cuando atienda una llamada.</StepLead>
        <StepError
          message="No hemos podido cargar el catálogo de voces."
          onRetry={() => void load()}
        />
      </View>
    );
  }

  if (catalog === null) {
    return (
      <View style={{ gap: spacing(4) }}>
        <StepLead>Así sonará tu asistente cuando atienda una llamada.</StepLead>
        <StepSkeleton rows={3} />
      </View>
    );
  }

  return (
    <View style={{ gap: spacing(4) }}>
      <StepLead>
        Así sonará tu asistente cuando atienda una llamada. Toca la que más te guste:
        se guarda al momento y puedes cambiarla siempre que quieras.
      </StepLead>

      {selectedId ? <DoneBadge>Ya tienes una voz elegida.</DoneBadge> : null}
      {pendingVoice ? (
        <Notice tone="warning" title="Guardada, pendiente de aplicarse">
          <NoticeText tone="warning">
            Hemos guardado tu elección, pero todavía no la hemos podido poner en el
            asistente que atiende las llamadas. Se aplicará sola en cuanto el teléfono
            de tu IA esté activo (paso 3). Si ya lo tienes activo, escríbenos a{" "}
            <SupportEmail context={SUPPORT_CONTEXT} />.
          </NoticeText>
        </Notice>
      ) : null}
      {saveError ? <Notice tone="danger">{saveError}</Notice> : null}
      {previewError ? <Notice tone="warning">{previewError}</Notice> : null}

      {catalog.length === 0 ? (
        <Notice tone="info">
          <NoticeText tone="info">
            Ahora mismo no hay voces disponibles. Escríbenos a{" "}
            <SupportEmail context={SUPPORT_CONTEXT} /> y lo miramos.
          </NoticeText>
        </Notice>
      ) : null}

      <View style={{ gap: spacing(3) }}>
        {catalog.map((v) => {
          const isSelected = v.id === selectedId;
          const hasPreview = v.provider === "11labs";
          const isPlaying = playingId === v.id;
          const isLoadingPreview = loadingPreviewId === v.id;
          return (
            <Pressable
              key={v.id}
              accessibilityRole="button"
              accessibilityState={{ selected: isSelected }}
              accessibilityLabel={`Elegir la voz ${v.name}`}
              onPress={() => {
                tapHaptic();
                void chooseVoice(v);
              }}
              style={({ pressed }) => [
                styles.voiceCard,
                isSelected ? styles.voiceCardSelected : null,
                pressed ? { opacity: 0.9 } : null,
              ]}
            >
              <View style={styles.voiceHead}>
                <Text style={styles.voiceName}>{v.name}</Text>
                {savingId === v.id ? (
                  <ActivityIndicator size="small" color={colors.primary} />
                ) : isSelected ? (
                  <View style={styles.selectedPill}>
                    <Ionicons name="checkmark" size={13} color={colors.primary} />
                    <Text style={styles.selectedPillText}>Tu voz</Text>
                  </View>
                ) : null}
              </View>

              <Text style={styles.voiceDescription}>{v.description}</Text>

              <View style={styles.voiceMetaRow}>
                <View style={styles.metaChip}>
                  <Text style={styles.metaChipText}>
                    {GENDER_LABEL[v.gender] ?? v.gender}
                  </Text>
                </View>
                <View style={styles.metaChip}>
                  <Text style={styles.metaChipText}>
                    {v.language === "es" ? "Español" : v.language}
                  </Text>
                </View>

                {hasPreview ? (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={
                      isPlaying ? `Parar la muestra de ${v.name}` : `Escuchar a ${v.name}`
                    }
                    hitSlop={6}
                    onPress={(e) => {
                      e.stopPropagation();
                      tapHaptic();
                      void playPreview(v);
                    }}
                    style={({ pressed }) => [
                      styles.listenBtn,
                      isPlaying ? styles.listenBtnActive : null,
                      pressed ? { opacity: 0.85 } : null,
                    ]}
                  >
                    {isLoadingPreview ? (
                      <ActivityIndicator size="small" color={colors.primary} />
                    ) : (
                      <Ionicons
                        name={isPlaying ? "stop" : "play"}
                        size={14}
                        color={isPlaying ? colors.white : colors.primary}
                      />
                    )}
                    <Text
                      style={[
                        styles.listenBtnText,
                        isPlaying ? styles.listenBtnTextActive : null,
                      ]}
                    >
                      {isLoadingPreview ? "Cargando" : isPlaying ? "Sonando" : "Escuchar"}
                    </Text>
                  </Pressable>
                ) : (
                  <Text style={styles.noPreview}>Sin preescucha</Text>
                )}
              </View>
            </Pressable>
          );
        })}
      </View>

      <HonestNote>
        Las voces marcadas «Sin preescucha» no se pueden oír antes de elegirlas: su
        proveedor no ofrece muestras. La única forma de escucharlas es elegirlas y
        llamar al número de tu IA.
      </HonestNote>

      <Notice tone="info" icon="mic-outline" title="¿Prefieres que suene como tú?">
        Clonar tu voz exige subir varios audios largos, y eso desde el móvil no sale
        bien. Se hace desde el panel web, en Ajustes → Voz.
      </Notice>
    </View>
  );
}

const styles = StyleSheet.create({
  voiceCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    gap: spacing(2),
    ...shadow.card,
  },
  voiceCardSelected: { borderColor: colors.primary, borderWidth: 1.5 },
  voiceHead: { flexDirection: "row", alignItems: "center", gap: spacing(2) },
  voiceName: { ...type.heading, flex: 1 },
  selectedPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
    backgroundColor: colors.primarySoft,
    paddingHorizontal: spacing(2),
    paddingVertical: 3,
    borderRadius: radius.pill,
  },
  selectedPillText: { fontSize: 11, fontWeight: "700", color: colors.primary },
  voiceDescription: { ...type.bodySecondary, fontSize: 14, lineHeight: 20 },
  voiceMetaRow: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: spacing(2),
    marginTop: spacing(1),
  },
  metaChip: {
    backgroundColor: colors.neutralSoft,
    paddingHorizontal: spacing(2),
    paddingVertical: 3,
    borderRadius: radius.pill,
  },
  metaChipText: { fontSize: 11, fontWeight: "600", color: colors.inkSecondary },
  listenBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(1),
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(2),
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    backgroundColor: colors.surface,
    marginLeft: "auto",
  },
  listenBtnActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  listenBtnText: { fontSize: 12, fontWeight: "700", color: colors.primary },
  listenBtnTextActive: { color: colors.white },
  noPreview: { ...type.meta, fontSize: 11, marginLeft: "auto" },
});

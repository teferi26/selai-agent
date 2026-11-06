// Paso 6 · Tu catálogo — elegir el CSV con expo-document-picker (Archivos,
// Drive, lo que el móvil ofrezca) y subirlo de verdad a
// POST /properties/import, con el desglose exacto que devuelve el backend.
import { useCallback, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as DocumentPicker from "expo-document-picker";
import { ApiError, isTimeout } from "@/lib/api";
import { useSetupWizard } from "@/context/SetupWizardContext";
import { ImportResult, importPropertiesCsv } from "@/lib/setup-api";
import { PrimaryButton, SecondaryButton, withSupportEmail } from "@/components/ui";
import {
  Collapsible,
  DoneBadge,
  HonestNote,
  Notice,
  NoticeText,
  Panel,
  SectionTitle,
  StepLead,
} from "@/components/setup/kit";
import { successHaptic, tapHaptic, warningHaptic } from "@/lib/haptics";
// La dirección se interpola en vez de escribirse a mano: `withSupportEmail`
// parte el texto por esta misma constante para volverla pulsable, y una copia
// literal dejaría de casar el día que cambie.
import { SUPPORT_EMAIL } from "@/lib/support";
import { colors, radius, spacing, type } from "@/lib/theme";

type PickedFile = { uri: string; name: string; mimeType: string | null; size: number | null };

function formatSize(bytes: number | null): string {
  if (bytes === null || Number.isNaN(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ResultBreakdown({ result }: { result: ImportResult }) {
  const rows: { icon: "add-circle" | "sync-circle" | "remove-circle"; tone: string; label: string; value: number }[] = [
    { icon: "add-circle", tone: colors.successInk, label: "nuevas", value: result.created },
    { icon: "sync-circle", tone: colors.primary, label: "actualizadas", value: result.updated },
    { icon: "remove-circle", tone: colors.inkSecondary, label: "sin importar", value: result.skipped },
  ];
  return (
    <View style={{ gap: spacing(3) }}>
      <View style={styles.breakdown}>
        {rows.map((r) => (
          <View key={r.label} style={styles.breakdownItem}>
            <Ionicons name={r.icon} size={18} color={r.tone} />
            <Text style={[styles.breakdownValue, { color: r.tone }]}>{r.value}</Text>
            <Text style={styles.breakdownLabel}>{r.label}</Text>
          </View>
        ))}
      </View>
      {result.errors.length > 0 ? (
        <Collapsible title={`Ver qué no ha entrado (${result.errors.length})`}>
          <View style={{ gap: spacing(2) }}>
            {result.errors.map((err, i) => (
              <Text key={i} style={styles.errorLine}>
                {err}
              </Text>
            ))}
          </View>
        </Collapsible>
      ) : null}
    </View>
  );
}

export default function StepCatalog() {
  const { agencyId, hasProperties, refreshProperties } = useSetupWizard();

  const [file, setFile] = useState<PickedFile | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pick = useCallback(async () => {
    setError(null);
    setResult(null);
    try {
      const picked = await DocumentPicker.getDocumentAsync({
        // Sin filtro estricto a propósito: cada gestor de archivos etiqueta los
        // CSV de una forma y filtrar de más deja al usuario sin poder elegir
        // su propio fichero.
        type: "*/*",
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (picked.canceled || !picked.assets?.[0]) return;
      const asset = picked.assets[0];
      tapHaptic();
      setFile({
        uri: asset.uri,
        name: asset.name || "catalogo.csv",
        mimeType: asset.mimeType ?? null,
        size: asset.size ?? null,
      });
    } catch {
      setError("No hemos podido abrir tus archivos. Inténtalo de nuevo.");
    }
  }, []);

  const upload = useCallback(async () => {
    if (!agencyId || !file || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const out = await importPropertiesCsv(agencyId, file);
      setResult(out);
      // El backend responde 200 aunque no entre ni una fila (cabeceras que no
      // reconoce, filas sin precio). Celebrar eso sería mentir: solo hay
      // enhorabuena si algo ha llegado de verdad al catálogo.
      if (out.created + out.updated > 0) {
        successHaptic();
        await refreshProperties();
      } else {
        warningHaptic();
      }
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      const inactive = e instanceof ApiError && e.message.includes("subscription_inactive");
      setError(
        status === 400
          ? "Ese archivo está vacío o no tiene el formato esperado. Debe ser un CSV con una fila de cabecera y una propiedad por línea."
          : status === 402
            ? inactive
              ? `Tu suscripción no está activa ahora mismo, así que no podemos importar. Revisa el pago en el panel web o escríbenos a ${SUPPORT_EMAIL}.`
              : `Tu plan no admite más propiedades. Escríbenos a ${SUPPORT_EMAIL} y lo ampliamos.`
            : status === 422
              ? "No hemos podido leer el archivo. Comprueba que es el CSV que exporta tu programa de gestión."
              : isTimeout(e)
                ? "La subida está tardando demasiado. Prueba con wifi, o desde el ordenador si el archivo es grande."
                : "No hemos podido subir el archivo. Comprueba tu conexión e inténtalo de nuevo."
      );
    } finally {
      setBusy(false);
    }
  }, [agencyId, file, busy, refreshProperties]);

  const looksLikeCsv = file ? /\.csv$/i.test(file.name) : true;

  return (
    <View style={{ gap: spacing(4) }}>
      <StepLead>
        Con tus pisos cargados, el asistente responde dudas concretas, filtra por
        presupuesto y propone visitas de inmuebles que existen de verdad.
      </StepLead>

      {hasProperties ? (
        <DoneBadge>
          Ya tienes propiedades en tu catálogo. Puedes subir más cuando quieras: se
          actualizan las que ya existen y se añaden las nuevas.
        </DoneBadge>
      ) : null}

      <Panel>
        <SectionTitle>Sube tu archivo CSV</SectionTitle>
        <Text style={styles.body}>
          Es el archivo que exporta tu programa de gestión. Puedes cogerlo de Archivos,
          Drive o de donde lo tengas en el móvil.
        </Text>

        {file ? (
          <View style={styles.fileRow}>
            <View style={styles.fileIcon}>
              <Ionicons name="document-text-outline" size={18} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.fileName} numberOfLines={1}>
                {file.name}
              </Text>
              <Text style={styles.fileMeta}>
                {formatSize(file.size) || "Listo para subir"}
              </Text>
            </View>
            <SecondaryButton label="Cambiar" onPress={() => void pick()} style={styles.changeBtn} />
          </View>
        ) : (
          <SecondaryButton
            label="Elegir archivo CSV"
            icon="folder-open-outline"
            onPress={() => void pick()}
          />
        )}

        {file && !looksLikeCsv ? (
          <Notice tone="warning">
            Este archivo no acaba en .csv. Si tu programa lo exporta con otra
            extensión puedes intentarlo igualmente, pero si falla vuelve a exportarlo
            como CSV.
          </Notice>
        ) : null}

        {file ? (
          <PrimaryButton
            label={busy ? "Subiendo..." : "Importar catálogo"}
            icon="cloud-upload-outline"
            loading={busy}
            onPress={() => void upload()}
          />
        ) : null}

        {/* El texto se construye en el `catch`, así que la dirección de soporte
            se vuelve pulsable al pintarla, no al escribirla. */}
        {error ? (
          <Notice tone="danger">
            <NoticeText tone="danger">
              {withSupportEmail(error, "Puesta en marcha · Paso 6 (catálogo)")}
            </NoticeText>
          </Notice>
        ) : null}
      </Panel>

      {result ? (
        <Panel>
          <SectionTitle>Resultado de la importación</SectionTitle>
          {result.created + result.updated === 0 ? (
            <Notice tone="warning" title="No ha entrado ninguna propiedad">
              Hemos leído el archivo pero no hemos podido usar ninguna fila. Abajo
              tienes el motivo exacto: lo habitual es que falten las columnas de
              título y precio, o que el archivo no sea el export del programa.
            </Notice>
          ) : null}
          <ResultBreakdown result={result} />
          <HonestNote>
            Las propiedades se identifican por su referencia: volver a subir el mismo
            archivo actualiza las que ya tenías en lugar de duplicarlas.
          </HonestNote>
        </Panel>
      ) : null}

      <Notice tone="info" icon="desktop-outline" title="Para archivos grandes">
        Desde el ordenador es más cómodo: el panel web permite arrastrar el archivo y
        aguanta mejor los catálogos de cientos de inmuebles. Desde aquí funciona
        igual, solo que subir por datos móviles tarda más.
      </Notice>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { ...type.body, lineHeight: 22 },
  fileRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(3),
    backgroundColor: colors.bg,
    borderRadius: radius.small,
    padding: spacing(3),
  },
  fileIcon: {
    width: 36,
    height: 36,
    borderRadius: radius.pill,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
  },
  fileName: { ...type.body, fontSize: 14, fontWeight: "600" },
  fileMeta: { ...type.meta, fontWeight: "400", fontSize: 12 },
  changeBtn: { height: 40, paddingHorizontal: spacing(3) },
  breakdown: { flexDirection: "row", gap: spacing(3) },
  breakdownItem: {
    flex: 1,
    alignItems: "center",
    gap: 2,
    backgroundColor: colors.bg,
    borderRadius: radius.small,
    paddingVertical: spacing(3),
  },
  breakdownValue: { ...type.metric, fontSize: 24 },
  breakdownLabel: { ...type.meta, fontSize: 11 },
  errorLine: { ...type.meta, fontWeight: "400", fontSize: 12, lineHeight: 18 },
});

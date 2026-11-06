// Catálogo en el móvil: buscar un inmueble y mandárselo a un cliente.
//
// El buscador filtra en local sobre lo ya descargado (GET /properties no
// acepta búsqueda) — un catálogo cabe de sobra en memoria y así el filtro es
// instantáneo y funciona sin cobertura una vez cargado.
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FlatList,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { fetchProperties, Property } from "@/lib/api";
import { useAgency } from "@/context/AgencyContext";
import { EmptyView, ErrorView, Pill, SkeletonList } from "@/components/ui";
import { formatEuros } from "@/lib/format";
import { tapHaptic } from "@/lib/haptics";
import {
  colors,
  propertyStatusLabel,
  propertyStatusTone,
  radius,
  shadow,
  spacing,
  type,
} from "@/lib/theme";

/** Título del inmueble, nunca vacío. Un catálogo importado puede traer la
 * columna en blanco, y una tarjeta sin primera línea (o un mensaje que empieza
 * por un salto de línea) es peor que decir que falta. Mismo texto que usa la
 * agenda para el inmueble de una visita. */
function titleOf(p: Property): string {
  return p.title.trim() || "Inmueble sin título";
}

/** Precio, o el aviso de que no lo tenemos. `price` no admite nulos en el
 * backend, así que un inmueble sin precio llega como 0 — y "0 €" en el
 * WhatsApp de un cliente es una oferta, no un dato que falta. */
function priceOf(p: Property): string {
  return p.price > 0 ? formatEuros(p.price) : "Precio a consultar";
}

/** Texto que se comparte. Nada inventado: selai no publica todavía una ficha
 * pública por inmueble, así que el "enlace" es la primera foto del catálogo
 * cuando la hay, y cuando no la hay simplemente no se pone. Cada línea que
 * saldría vacía se omite: el cliente no debe recibir "Referencia " a secas. */
function buildShareMessage(p: Property): string {
  const lines: string[] = [titleOf(p), priceOf(p)];

  const place = [p.neighborhood, p.city].filter(Boolean).join(", ");
  const features = [
    p.bedrooms ? `${p.bedrooms} hab.` : null,
    p.area_sqm ? `${p.area_sqm} m²` : null,
  ].filter(Boolean);
  const detail = [place || null, features.length ? features.join(" · ") : null]
    .filter(Boolean)
    .join(" · ");
  if (detail) lines.push(detail);

  const reference = p.reference.trim();
  if (reference) lines.push(`Referencia ${reference}`);

  const photo = p.image_urls.find((url) => /^https?:\/\//i.test(url));
  if (photo) lines.push(photo);

  return lines.join("\n");
}

function PropertyCard({ item }: { item: Property }) {
  async function share() {
    tapHaptic();
    try {
      // Share nativo: el propio menú del teléfono ofrece WhatsApp, y así el
      // agente elige el chat sin que la app tenga que pedir contactos.
      await Share.share({ message: buildShareMessage(item), title: item.title });
    } catch {
      // El usuario cerró el menú, o el sistema lo rechazó. No hay nada que
      // contarle: no ha pasado nada.
    }
  }

  const place = [item.neighborhood, item.city].filter(Boolean).join(", ");
  const features = [
    item.bedrooms ? `${item.bedrooms} hab.` : null,
    item.area_sqm ? `${item.area_sqm} m²` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const reference = item.reference.trim();

  return (
    <View style={styles.card}>
      <View style={styles.cardHead}>
        <Text style={styles.cardTitle} numberOfLines={2}>
          {titleOf(item)}
        </Text>
        <Pill
          label={propertyStatusLabel[item.status] || item.status}
          tone={propertyStatusTone[item.status] || "neutral"}
        />
      </View>
      <Text style={styles.cardPrice}>{priceOf(item)}</Text>
      {place ? (
        <Text style={styles.cardMeta} numberOfLines={1}>
          {place}
        </Text>
      ) : null}
      {features ? <Text style={styles.cardMeta}>{features}</Text> : null}
      {reference ? <Text style={styles.cardRef}>Ref. {reference}</Text> : null}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Compartir ${titleOf(item)}`}
        style={({ pressed }) => [styles.shareBtn, pressed ? { opacity: 0.85 } : null]}
        onPress={() => void share()}
      >
        <Ionicons name="logo-whatsapp" size={17} color={colors.white} />
        <Text style={styles.shareBtnText}>Compartir por WhatsApp</Text>
      </Pressable>
    </View>
  );
}

/** Normaliza para buscar sin acentos ni mayúsculas: en un catálogo español
 * "malaga" tiene que encontrar "Málaga". */
// Bloque Unicode de marcas combinantes (U+0300–U+036F). Se construye por
// código y no como literal en la expresión regular a propósito: escrito a
// pelo el rango son caracteres invisibles en el código, y basta con que un
// editor los reordene o los normalice para que el buscador deje de quitar
// tildes sin que nadie lo note.
const DIACRITICS = new RegExp(
  `[${String.fromCharCode(0x300)}-${String.fromCharCode(0x36f)}]`,
  "g"
);

function normalize(s: string): string {
  // NFD separa la tilde de la letra para poder tirar solo la tilde.
  return s.toLowerCase().normalize("NFD").replace(DIACRITICS, "");
}

export default function PropertiesScreen() {
  const { me } = useAgency();
  const agencyId = me?.agency_id;
  const [items, setItems] = useState<Property[] | null>(null);
  const [query, setQuery] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      if (!agencyId) return;
      if (!opts.silent) setItems(null);
      setError(false);
      try {
        setItems(await fetchProperties(agencyId));
      } catch {
        setError(true);
      }
    },
    [agencyId]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load({ silent: true });
    setRefreshing(false);
  }, [load]);

  const filtered = useMemo(() => {
    if (!items) return null;
    const needle = normalize(query.trim());
    if (!needle) return items;
    return items.filter((p) =>
      normalize(
        [p.title, p.reference, p.city, p.neighborhood ?? ""].join(" ")
      ).includes(needle)
    );
  }, [items, query]);

  const search = (
    <View style={styles.searchWrap}>
      <View style={styles.searchBox}>
        <Ionicons name="search-outline" size={17} color={colors.inkSecondary} />
        <TextInput
          style={styles.searchInput}
          placeholder="Buscar por título, zona o referencia"
          placeholderTextColor={colors.inkSecondary}
          value={query}
          onChangeText={setQuery}
          autoCorrect={false}
          returnKeyType="search"
          accessibilityLabel="Buscar inmuebles"
        />
        {query.length > 0 ? (
          <Pressable
            hitSlop={8}
            onPress={() => setQuery("")}
            accessibilityRole="button"
            accessibilityLabel="Borrar la búsqueda"
          >
            <Ionicons name="close-circle" size={17} color={colors.inkSecondary} />
          </Pressable>
        ) : null}
      </View>
    </View>
  );

  if (error) {
    return (
      <View style={styles.screen}>
        {search}
        <ErrorView onRetry={() => void load()} />
      </View>
    );
  }

  if (filtered === null) {
    return (
      <View style={styles.screen}>
        {search}
        <SkeletonList rows={5} />
      </View>
    );
  }

  const searching = query.trim().length > 0;

  return (
    <View style={styles.screen}>
      {search}
      <FlatList
        data={filtered}
        keyExtractor={(p) => p.id}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={colors.primary}
            colors={[colors.primary]}
          />
        }
        contentContainerStyle={[
          styles.listContent,
          filtered.length === 0 ? { flexGrow: 1 } : null,
        ]}
        ListEmptyComponent={
          searching ? (
            <EmptyView
              icon="search-outline"
              title="Ningún inmueble coincide"
              subtitle={`No hay resultados para "${query.trim()}". Prueba con la referencia o con la zona.`}
            />
          ) : (
            <EmptyView
              icon="home-outline"
              title="Tu catálogo está vacío"
              subtitle="Sube tus inmuebles desde el panel y la IA podrá proponerlos a los clientes."
              ctaLabel="Ver puesta en marcha"
              onCta={() => router.push("/setup-checklist")}
            />
          )
        }
        renderItem={({ item }) => <PropertyCard item={item} />}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  searchWrap: {
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
    paddingHorizontal: spacing(4),
    paddingVertical: spacing(3),
  },
  searchBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing(2),
    backgroundColor: colors.bg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.input,
    paddingHorizontal: spacing(3),
    height: 44,
  },
  searchInput: { flex: 1, ...type.body, paddingVertical: 0 },
  listContent: { padding: spacing(4), gap: spacing(3) },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    padding: spacing(4),
    gap: 3,
    ...shadow.card,
  },
  cardHead: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: spacing(2),
  },
  cardTitle: { ...type.heading, flexShrink: 1 },
  cardPrice: { ...type.body, fontWeight: "700", fontSize: 17, marginTop: spacing(1) },
  cardMeta: { ...type.meta, fontWeight: "400" },
  cardRef: { ...type.meta, fontSize: 12, marginTop: 2 },
  shareBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing(2),
    backgroundColor: colors.whatsapp,
    borderRadius: radius.button,
    height: 44,
    marginTop: spacing(3),
  },
  shareBtnText: { color: colors.white, fontWeight: "600", fontSize: 15 },
});

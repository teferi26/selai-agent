import { useCallback, useEffect, useRef, useState } from "react";
import { Linking, Platform } from "react-native";
import Constants from "expo-constants";
import * as Notifications from "expo-notifications";
import { router } from "expo-router";
import { api, apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Push token registration.
//
// Fire-and-forget: asks for notification permissions, fetches the Expo push
// token and registers the device against the backend. Any failure is silent —
// since SDK 53 Expo Go does NOT support remote push notifications, so this
// must never break the app when running under Expo Go.
// ---------------------------------------------------------------------------

// Qué agencia tiene registrado este dispositivo ahora mismo, no "si ya se
// registró alguna vez".
//
// Con un booleano había fuga entre inquilinos: el registro solo ocurría una
// vez por proceso de JS, y cerrar sesión no reinicia el bundle. Un agente de
// la inmobiliaria A cerraba sesión, entraba uno de la B en el mismo teléfono,
// y como la bandera seguía puesta el token nunca se volvía a registrar: la
// fila de device_tokens seguía apuntando a la agencia A. Y send_push
// (backend/app/services/push.py) reparte por agency_id, así que ese teléfono
// seguía recibiendo los escalados de A — con el nombre del lead y un trozo de
// su mensaje en el cuerpo del aviso. El backend ya sabe repuntar la fila
// ("agency switches re-point the row", app/api/devices.py); lo que faltaba era
// que la app volviera a llamar.
let registeredFor: string | null = null;
let registeredToken: string | null = null;

export const LEAD_ACTIONS_CATEGORY = "lead_actions";
export const CALL_LEAD_ACTION = "call_lead";
export const VIEW_CONVERSATION_ACTION = "view_conversation";

let actionRegistration: Promise<void> | null = null;

/** Register the native buttons shown on actionable lead notifications. */
export function configureNotificationActions(): Promise<void> {
  if (actionRegistration) return actionRegistration;
  if (
    Constants.appOwnership === "expo" ||
    (Platform.OS !== "ios" && Platform.OS !== "android")
  ) {
    return Promise.resolve();
  }
  actionRegistration = Notifications.setNotificationCategoryAsync(
    LEAD_ACTIONS_CATEGORY,
    [
      {
        identifier: CALL_LEAD_ACTION,
        buttonTitle: "Llamar",
        options: { opensAppToForeground: true },
      },
      {
        identifier: VIEW_CONVERSATION_ACTION,
        buttonTitle: "Ver conversación",
        options: { opensAppToForeground: true },
      },
    ]
  )
    .then(() => undefined)
    .catch((error) => {
      actionRegistration = null;
      console.log(
        "[push] notification actions unavailable:",
        error instanceof Error ? error.message : error
      );
    });
  return actionRegistration;
}

async function currentExpoPushToken(): Promise<string | null> {
  if (Constants.appOwnership === "expo") return null;
  if (Platform.OS !== "ios" && Platform.OS !== "android") return null;
  const projectId: string | undefined = Constants.expoConfig?.extra?.eas?.projectId;
  const { data } = await Notifications.getExpoPushTokenAsync(
    projectId ? { projectId } : undefined
  );
  return data || null;
}

export async function registerPushToken(agencyId: string): Promise<void> {
  if (registeredFor === agencyId) return;
  try {
    // Remote push is unsupported in Expo Go (SDK 53+) — bail out early.
    if (Constants.appOwnership === "expo") {
      console.log("[push] Expo Go detected, skipping push registration");
      return;
    }
    if (Platform.OS !== "ios" && Platform.OS !== "android") return;

    const perms = await Notifications.getPermissionsAsync();
    let status = perms.status;
    if (status !== "granted") {
      const req = await Notifications.requestPermissionsAsync();
      status = req.status;
    }
    if (status !== "granted") {
      console.log("[push] notification permission not granted");
      return;
    }

    // EAS builds need the projectId; it lands in expoConfig.extra after
    // `eas init`. Without it (bare dev build) the no-arg call still works.
    const token = await currentExpoPushToken();
    if (!token) return;

    await api(`/api/v1/agencies/${agencyId}/devices`, {
      method: "POST",
      body: JSON.stringify({
        expo_push_token: token,
        platform: Platform.OS,
      }),
    });
    registeredFor = agencyId;
    registeredToken = token;
    console.log("[push] device registered");
  } catch (e) {
    // Silent by design: push is best-effort plumbing.
    console.log("[push] registration skipped:", e instanceof Error ? e.message : e);
  }
}

/** Remove this install from the signed-in agency before its auth is cleared. */
export async function unregisterPushToken(agencyId: string): Promise<void> {
  try {
    const token = registeredToken ?? (await currentExpoPushToken());
    if (!token) return;
    const response = await apiFetch(
      `/api/v1/agencies/${agencyId}/devices`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expo_push_token: token }),
      },
      5_000
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    console.log("[push] device unregistered");
  } catch (e) {
    // Logout must remain possible offline. A later login always re-registers
    // and the backend then re-points the token to the new agency.
    console.log("[push] unregistration skipped:", e instanceof Error ? e.message : e);
  } finally {
    registeredFor = null;
    registeredToken = null;
  }
}

// ---------------------------------------------------------------------------
// Tapping a notification has to land somewhere useful.
//
// Every alert the backend sends (escalado, lead caliente, captación, mensaje
// en horario de oficina, aviso de portal ilegible) carries `conversation_id`
// in `data` — see app/services/push.py callers. The app used to drop it and
// just open on whatever screen it had last, which for an escalation is the
// one thing the notification was asking you NOT to do.
// ---------------------------------------------------------------------------

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Cuánto puede haber pasado desde el aviso para que abrir la app cuente como
 * "ha tocado la notificación" y no como "ha abierto la app y punto". */
const COLD_START_MAX_AGE_MS = 5 * 60_000;

/** Frontera entre las dos unidades. Cualquier instante posterior a 1973 medido
 * en milisegundos la supera; cualquier instante anterior al año 5138 medido en
 * segundos se queda por debajo. */
const SECONDS_CEILING = 1e11;

/**
 * `Notification.date` normalizada a milisegundos.
 *
 * Las dos plataformas no la mandan en la misma unidad: Android serializa
 * `Date.getTime()` (milisegundos, NotificationSerializer.java) y iOS
 * `timeIntervalSince1970` (SEGUNDOS, EXNotificationSerializer.m — a diferencia
 * del planificador de la misma librería, que sí multiplica por 1000). Restando
 * a `Date.now()` sin corregirlo, en iOS toda notificación parecía tener 56 años
 * y el arranque en frío no navegaba nunca: tocar el aviso de un escalado abría
 * la bandeja, que es justo lo que la notificación pedía no hacer.
 *
 * Se decide por magnitud y no por `Platform.OS` para que siga siendo correcto
 * el día que expo unifique las unidades en cualquiera de los dos sentidos.
 */
export function notificationDateMs(date: unknown): number | null {
  if (typeof date !== "number" || !Number.isFinite(date) || date <= 0) return null;
  return date < SECONDS_CEILING ? date * 1000 : date;
}

/**
 * The conversation a notification points at, or null.
 *
 * The payload is validated even though it is ours: it arrives from outside
 * the app and ends up interpolated into a route, so anything that is not a
 * plain uuid is ignored instead of being navigated to.
 */
export function conversationTargetOf(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const value = (data as Record<string, unknown>).conversation_id;
  if (typeof value !== "string" || !UUID_RE.test(value)) return null;
  return value;
}

/** A public lead phone from our own push payload, restricted to safe E.164. */
export function phoneTargetOf(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const value = (data as Record<string, unknown>).phone;
  if (typeof value !== "string" || !/^\+[1-9]\d{6,14}$/.test(value)) return null;
  return value;
}

/**
 * Opens `/conversation/{id}` when the user taps an alert.
 *
 * `ready` gates the actual navigation: a tap can wake the app from cold, and
 * the target is parked until the agency session exists instead of being
 * lost. That gate doubles as the navigation one — the session is resolved by
 * the tabs layout, so by the time it says "ready" the navigator is mounted.
 */
export function useNotificationRouting(ready: boolean): void {
  const [target, setTarget] = useState<string | null>(null);
  const handled = useRef<Set<string>>(new Set());

  const accept = useCallback(
    (response: Notifications.NotificationResponse | null, coldStart = false) => {
      if (!response) return;
      // The cold-start lookup and the listener can report the same tap.
      const id = response.notification.request.identifier;
      if (handled.current.has(id)) return;
      handled.current.add(id);

      // Expo keeps the last response forever, so the cold-start lookup also
      // answers when the app is opened days later from its icon. Without this
      // check, opening the app on Friday reopened Tuesday's escalation.
      if (coldStart) {
        const sentAt = notificationDateMs(response.notification.date);
        // Sin fecha creíble no se descarta el aviso: preferimos abrir de más a
        // tragarnos el escalado que motivó el arranque.
        if (sentAt !== null && Date.now() - sentAt > COLD_START_MAX_AGE_MS) return;
      }

      const data = response.notification.request.content.data;
      if (response.actionIdentifier === CALL_LEAD_ACTION) {
        const phone = phoneTargetOf(data);
        if (phone) void Linking.openURL(`tel:${phone}`).catch(() => {});
        return;
      }
      if (
        response.actionIdentifier !== Notifications.DEFAULT_ACTION_IDENTIFIER &&
        response.actionIdentifier !== VIEW_CONVERSATION_ACTION
      ) {
        return;
      }

      const conversationId = conversationTargetOf(data);
      if (conversationId) setTarget(conversationId);
    },
    []
  );

  useEffect(() => {
    void configureNotificationActions();
    // App opened from scratch by tapping the notification.
    void Notifications.getLastNotificationResponseAsync()
      .then((response) => accept(response, true))
      .catch(() => {
        // Not supported under Expo Go — routing is best-effort, like the rest.
      });
    const sub = Notifications.addNotificationResponseReceivedListener((r) =>
      accept(r)
    );
    return () => sub.remove();
  }, [accept]);

  useEffect(() => {
    if (!target || !ready) return;
    setTarget(null);
    try {
      router.push({ pathname: "/conversation/[id]", params: { id: target } });
    } catch {
      // Si el router todavía no puede navegar, el usuario sigue en la bandeja
      // con el aviso en la lista: peor eso que tirar la app abajo.
    }
  }, [target, ready]);
}

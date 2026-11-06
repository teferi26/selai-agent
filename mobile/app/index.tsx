import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { router } from "expo-router";
import * as Application from "expo-application";
import Constants from "expo-constants";
import * as WebBrowser from "expo-web-browser";
import * as AuthSession from "expo-auth-session";
import * as Updates from "expo-updates";
import { Ionicons } from "@expo/vector-icons";
import { useAuth, useSSO } from "@clerk/expo";
import { useSignIn } from "@clerk/expo/legacy";
import { SupportEmail } from "@/components/ui";
import { getDevEmail, setDevEmail } from "@/lib/api";
import { hasClerk } from "@/lib/auth";
import { explainClerkError } from "@/lib/clerk-errors";
import { successHaptic, tapHaptic } from "@/lib/haptics";
import { button, colors, radius, spacing, type } from "@/lib/theme";
import { SIGN_UP_URL } from "@/lib/web";

// Required so the auth browser session closes correctly when the OAuth
// redirect brings the user back to the app.
WebBrowser.maybeCompleteAuthSession();

/** Contexto que viaja en el correo de soporte desde esta pantalla. */
const SUPPORT_CONTEXT = "Pantalla de acceso";

/** Mínimo que exige Clerk por defecto. Se avisa antes de enviar para no
 * gastar el código de un solo uso en un 422. */
const MIN_PASSWORD = 8;

// ---------------------------------------------------------------------------
// Production login: Google prominent, email+password discreet.
// ---------------------------------------------------------------------------

function ClerkLogin() {
  const { isLoaded: authLoaded, isSignedIn } = useAuth();
  const { signIn, setActive, isLoaded } = useSignIn();
  const { startSSOFlow } = useSSO();
  const [showEmail, setShowEmail] = useState(false);
  const [forgot, setForgot] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Clerk fetches its environment from clerk.selai.es on first load; if that
  // request hangs (no network, DNS not yet propagated) `isLoaded` never
  // flips and the spinner would be a dead end. Surface it after 8s.
  const [authTimedOut, setAuthTimedOut] = useState(false);

  useEffect(() => {
    if (authLoaded) return;
    const t = setTimeout(() => setAuthTimedOut(true), 8000);
    return () => clearTimeout(t);
  }, [authLoaded]);

  useEffect(() => {
    if (authLoaded && isSignedIn) router.replace("/(tabs)/inbox");
  }, [authLoaded, isSignedIn]);

  async function submitGoogle() {
    if (busy || googleBusy) return;
    tapHaptic();
    setGoogleBusy(true);
    setError(null);
    try {
      const { createdSessionId, setActive: ssoSetActive } = await startSSOFlow({
        strategy: "oauth_google",
        redirectUrl: AuthSession.makeRedirectUri(),
      });
      if (createdSessionId && ssoSetActive) {
        await ssoSetActive({ session: createdSessionId });
        successHaptic();
        router.replace("/(tabs)/inbox");
      } else {
        // signIn/signUp need extra steps (e.g. account not fully provisioned).
        setError(
          "No hemos podido completar el acceso con Google. Completa tu registro en selai.es y vuelve a intentarlo."
        );
      }
    } catch (e) {
      setError(explainClerkError(e, "google"));
    } finally {
      setGoogleBusy(false);
    }
  }

  async function submit() {
    if (!isLoaded || busy) return;
    if (!email.trim() || !password) {
      setError("Introduce tu email y contraseña.");
      return;
    }
    tapHaptic();
    setBusy(true);
    setError(null);
    try {
      const result = await signIn.create({
        identifier: email.trim(),
        password,
      });
      if (result.status === "complete") {
        await setActive({ session: result.createdSessionId });
        successHaptic();
        router.replace("/(tabs)/inbox");
      } else {
        setError("Tu cuenta necesita un paso extra. Inicia sesión desde selai.es.");
      }
    } catch (e) {
      // Clerk contesta en inglés ("Password is incorrect..."). Aquí se traduce
      // por código; lo que no conocemos sale como genérico, nunca en inglés.
      setError(explainClerkError(e, "signin"));
    } finally {
      setBusy(false);
    }
  }

  if (!authLoaded) {
    if (authTimedOut) {
      return (
        <LoginShell>
          <Text style={styles.error}>
            No hemos podido conectar con el servicio de acceso. Comprueba tu
            conexión (prueba a cambiar entre WiFi y datos) y reinténtalo.
          </Text>
          <Pressable
            style={({ pressed }) => [styles.primaryBtn, pressed ? styles.btnPressed : null]}
            onPress={() => {
              tapHaptic();
              void Updates.reloadAsync().catch(() => setAuthTimedOut(false));
            }}
            accessibilityRole="button"
          >
            <Text style={styles.primaryBtnText}>Reintentar</Text>
          </Pressable>
        </LoginShell>
      );
    }
    return (
      <View style={styles.container}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  if (forgot) {
    return (
      <ForgotPassword
        initialEmail={email}
        onCancel={() => {
          setError(null);
          setForgot(false);
        }}
      />
    );
  }

  return (
    <LoginShell>
      <Pressable
        style={({ pressed }) => [
          styles.googleBtn,
          googleBusy ? styles.btnDisabled : null,
          pressed && !googleBusy ? styles.btnPressed : null,
        ]}
        onPress={() => void submitGoogle()}
        disabled={googleBusy}
        accessibilityRole="button"
      >
        {googleBusy ? (
          <ActivityIndicator color={colors.ink} />
        ) : (
          <>
            <Ionicons name="logo-google" size={20} color={colors.ink} />
            <Text style={styles.googleBtnText}>Continuar con Google</Text>
          </>
        )}
      </Pressable>

      {!showEmail ? (
        <Pressable
          style={styles.emailToggle}
          onPress={() => {
            tapHaptic();
            setShowEmail(true);
          }}
          accessibilityRole="button"
        >
          <Text style={styles.emailToggleText}>Acceder con email y contraseña</Text>
        </Pressable>
      ) : (
        <View style={styles.emailForm}>
          <TextInput
            style={styles.input}
            placeholder="Email"
            accessibilityLabel="Email"
            placeholderTextColor={colors.inkSecondary}
            autoCapitalize="none"
            autoComplete="email"
            keyboardType="email-address"
            value={email}
            onChangeText={setEmail}
          />
          <TextInput
            style={styles.input}
            placeholder="Contraseña"
            accessibilityLabel="Contraseña"
            placeholderTextColor={colors.inkSecondary}
            secureTextEntry
            autoComplete="password"
            value={password}
            onChangeText={setPassword}
          />
          <Pressable
            style={({ pressed }) => [
              styles.primaryBtn,
              busy ? styles.btnDisabled : null,
              pressed && !busy ? styles.btnPressed : null,
            ]}
            onPress={() => void submit()}
            disabled={busy}
            accessibilityRole="button"
          >
            {busy ? (
              <ActivityIndicator color={colors.white} />
            ) : (
              <Text style={styles.primaryBtnText}>Iniciar sesión</Text>
            )}
          </Pressable>
          <Pressable
            style={styles.emailToggle}
            onPress={() => {
              tapHaptic();
              setError(null);
              setForgot(true);
            }}
            accessibilityRole="button"
          >
            <Text style={styles.emailToggleText}>He olvidado mi contraseña</Text>
          </Pressable>
        </View>
      )}

      {error ? (
        <View
          accessibilityRole="alert"
          accessibilityLiveRegion="assertive"
          style={styles.errorBlock}
        >
          <Text style={styles.error}>{error}</Text>
          {/* Aquí no hay sesión, así que el formulario de soporte de dentro de
              la app no puede enviar nada: el correo es la única vía honesta. */}
          <Text style={styles.supportLine}>
            ¿Sigues sin poder entrar? Escríbenos a{" "}
            <SupportEmail context={SUPPORT_CONTEXT} />.
          </Text>
        </View>
      ) : null}

      <View style={styles.signUpRow}>
        <Text style={styles.hint}>¿No tienes cuenta?</Text>
        <Pressable
          accessibilityRole="link"
          accessibilityHint="Abre selai.es en el navegador"
          hitSlop={8}
          onPress={() => {
            tapHaptic();
            void Linking.openURL(SIGN_UP_URL).catch(() => {
              setError(
                `No hemos podido abrir el navegador. Entra en ${SIGN_UP_URL} desde tu ordenador para crear la cuenta.`
              );
            });
          }}
        >
          <Text style={styles.signUpLink}>Crear cuenta en selai.es</Text>
        </Pressable>
      </View>
    </LoginShell>
  );
}

// ---------------------------------------------------------------------------
// Recuperar la contraseña, dentro de la app.
//
// Clerk lo soporta con la estrategia `reset_password_email_code`: `create()`
// manda el código al correo, `attemptFirstFactor()` lo canjea junto con la
// contraseña nueva. Antes esta pantalla no ofrecía ninguna salida a quien no
// recordaba su clave, y el panel web quedaba a un ordenador de distancia.
// ---------------------------------------------------------------------------

function ForgotPassword({
  initialEmail,
  onCancel,
}: {
  initialEmail: string;
  onCancel: () => void;
}) {
  const { signIn, setActive, isLoaded } = useSignIn();
  const [stage, setStage] = useState<"email" | "code">("email");
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function sendCode() {
    if (!isLoaded || busy) return;
    const identifier = email.trim();
    if (!identifier.includes("@")) {
      setError("Introduce el email con el que entras en selai.");
      return;
    }
    tapHaptic();
    setBusy(true);
    setError(null);
    try {
      await signIn.create({ strategy: "reset_password_email_code", identifier });
      setNotice(
        `Te hemos enviado un código a ${identifier}. Míralo en tu correo (revisa también la carpeta de spam).`
      );
      setStage("code");
    } catch (e) {
      setError(explainClerkError(e, "reset"));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!isLoaded || busy) return;
    if (!code.trim()) {
      setError("Escribe el código que te hemos enviado por correo.");
      return;
    }
    if (password.length < MIN_PASSWORD) {
      setError(`La contraseña nueva debe tener al menos ${MIN_PASSWORD} caracteres.`);
      return;
    }
    tapHaptic();
    setBusy(true);
    setError(null);
    try {
      const result = await signIn.attemptFirstFactor({
        strategy: "reset_password_email_code",
        code: code.trim(),
        password,
      });
      // Cada rama dice solo lo que ha pasado de verdad: afirmar "hemos
      // cambiado tu contraseña" cuando no sabemos si se cambió mandaría al
      // usuario a intentar entrar con una clave que no existe.
      if (result.status === "complete") {
        await setActive({ session: result.createdSessionId });
        successHaptic();
        router.replace("/(tabs)/inbox");
        return;
      }
      if (result.status === "needs_second_factor") {
        setError(
          "Tu cuenta tiene verificación en dos pasos. Termina el cambio desde selai.es en el navegador y vuelve aquí a entrar."
        );
        return;
      }
      if (result.status === "needs_new_password") {
        // Clerk aceptó el código pero pide la contraseña en una segunda
        // llamada: la tenemos, así que se termina aquí sin volver a pedirla.
        const done = await signIn.resetPassword({ password });
        if (done.status === "complete") {
          await setActive({ session: done.createdSessionId });
          successHaptic();
          router.replace("/(tabs)/inbox");
          return;
        }
        // La contraseña sí se ha cambiado; lo que no hemos podido es abrir
        // sesión aquí mismo.
        setError(
          "Hemos cambiado tu contraseña, pero no hemos podido abrirte la sesión. Vuelve atrás e inicia sesión con la nueva."
        );
        return;
      }
      setError(
        "No hemos podido completar el cambio desde la app. Termina de recuperar el acceso en selai.es desde el navegador."
      );
    } catch (e) {
      setError(explainClerkError(e, "reset"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <LoginShell>
      <Text style={styles.forgotTitle}>Recuperar el acceso</Text>

      {stage === "email" ? (
        <View style={styles.emailForm}>
          <Text style={styles.forgotLead}>
            Escribe tu email y te mandamos un código para poner una contraseña nueva.
          </Text>
          <TextInput
            style={styles.input}
            placeholder="Email"
            placeholderTextColor={colors.inkSecondary}
            autoCapitalize="none"
            autoComplete="email"
            keyboardType="email-address"
            value={email}
            onChangeText={setEmail}
            editable={!busy}
            accessibilityLabel="Email de tu cuenta"
          />
          <Pressable
            style={({ pressed }) => [
              styles.primaryBtn,
              busy ? styles.btnDisabled : null,
              pressed && !busy ? styles.btnPressed : null,
            ]}
            onPress={() => void sendCode()}
            disabled={busy}
            accessibilityRole="button"
          >
            {busy ? (
              <ActivityIndicator color={colors.white} />
            ) : (
              <Text style={styles.primaryBtnText}>Enviarme el código</Text>
            )}
          </Pressable>
        </View>
      ) : (
        <View style={styles.emailForm}>
          {notice ? <Text style={styles.notice}>{notice}</Text> : null}
          <TextInput
            style={styles.input}
            placeholder="Código del correo"
            placeholderTextColor={colors.inkSecondary}
            autoCapitalize="none"
            autoComplete="one-time-code"
            keyboardType="number-pad"
            value={code}
            onChangeText={setCode}
            editable={!busy}
            accessibilityLabel="Código recibido por correo"
          />
          <TextInput
            style={styles.input}
            placeholder={`Contraseña nueva (mínimo ${MIN_PASSWORD} caracteres)`}
            placeholderTextColor={colors.inkSecondary}
            secureTextEntry
            autoComplete="new-password"
            value={password}
            onChangeText={setPassword}
            editable={!busy}
            accessibilityLabel="Contraseña nueva"
          />
          <Pressable
            style={({ pressed }) => [
              styles.primaryBtn,
              busy ? styles.btnDisabled : null,
              pressed && !busy ? styles.btnPressed : null,
            ]}
            onPress={() => void confirm()}
            disabled={busy}
            accessibilityRole="button"
          >
            {busy ? (
              <ActivityIndicator color={colors.white} />
            ) : (
              <Text style={styles.primaryBtnText}>Cambiar la contraseña</Text>
            )}
          </Pressable>
          <Pressable
            style={styles.emailToggle}
            onPress={() => {
              tapHaptic();
              setCode("");
              setNotice(null);
              setStage("email");
            }}
            disabled={busy}
            accessibilityRole="button"
          >
            <Text style={styles.emailToggleText}>No me ha llegado, enviar otro</Text>
          </Pressable>
        </View>
      )}

      {error ? (
        <View
          accessibilityRole="alert"
          accessibilityLiveRegion="assertive"
          style={styles.errorBlock}
        >
          <Text style={styles.error}>{error}</Text>
          <Text style={styles.supportLine}>
            ¿Sigues sin poder entrar? Escríbenos a{" "}
            <SupportEmail context={SUPPORT_CONTEXT} />.
          </Text>
        </View>
      ) : null}

      <Pressable
        style={styles.emailToggle}
        onPress={() => {
          tapHaptic();
          onCancel();
        }}
        disabled={busy}
        accessibilityRole="button"
      >
        <Text style={styles.emailToggleText}>Volver al inicio de sesión</Text>
      </Pressable>
    </LoginShell>
  );
}

// ---------------------------------------------------------------------------
// Dev fallback: no Clerk key — the backend accepts X-Dev-Email.
// ---------------------------------------------------------------------------

function DevLogin() {
  const [email, setEmail] = useState("");
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDevEmail().then((saved) => {
      if (saved) {
        router.replace("/(tabs)/inbox");
      } else {
        setChecking(false);
      }
    });
  }, []);

  async function submit() {
    const value = email.trim();
    if (!value || !value.includes("@")) {
      setError("Introduce un email válido.");
      return;
    }
    tapHaptic();
    setError(null);
    await setDevEmail(value);
    successHaptic();
    router.replace("/(tabs)/inbox");
  }

  if (checking) {
    return (
      <View style={styles.container}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  return (
    <LoginShell devBadge>
      <View style={styles.emailForm}>
        <TextInput
          style={styles.input}
          placeholder="tu@inmobiliaria.es"
          accessibilityLabel="Email"
          placeholderTextColor={colors.inkSecondary}
          autoCapitalize="none"
          autoComplete="email"
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
        />
        {error ? (
          <Text accessibilityRole="alert" accessibilityLiveRegion="assertive" style={styles.error}>
            {error}
          </Text>
        ) : null}
        <Pressable
          style={({ pressed }) => [styles.primaryBtn, pressed ? styles.btnPressed : null]}
          onPress={() => void submit()}
          accessibilityRole="button"
        >
          <Text style={styles.primaryBtnText}>Entrar</Text>
        </Pressable>
      </View>
      <Text style={styles.hint}>
        Modo desarrollo. Configura EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY para usar el login real.
      </Text>
    </LoginShell>
  );
}

// ---------------------------------------------------------------------------
// Brand shell: porcelain background, big wordmark, one-line claim.
// ---------------------------------------------------------------------------

function LoginShell({
  children,
  devBadge,
}: {
  children: React.ReactNode;
  devBadge?: boolean;
}) {
  const version = Constants.expoConfig?.version ?? "?";
  const build = Application.nativeBuildVersion ?? "dev";
  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      {/* Scroll: con el teclado abierto, el formulario de recuperación (código
          + contraseña nueva + avisos) no cabe en pantallas pequeñas. */}
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
      >
        <View style={styles.brand}>
          <View style={styles.logoBadge}>
            <Text style={styles.logoText}>s</Text>
          </View>
          <Text style={styles.wordmark}>selai</Text>
          <Text style={styles.claim}>Tu agencia, atendida por IA las 24 horas.</Text>
          {devBadge ? <Text style={styles.devBadge}>Entorno de desarrollo</Text> : null}
        </View>
        <View style={styles.actions}>{children}</View>
        <Text style={styles.versionText}>{`v${version} (${build})`}</Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

export default function Index() {
  return hasClerk ? <ClerkLogin /> : <DevLogin />;
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  scrollContent: {
    flexGrow: 1,
    justifyContent: "center",
    padding: spacing(6),
  },
  brand: { alignItems: "center", marginBottom: spacing(10) },
  logoBadge: {
    width: 72,
    height: 72,
    borderRadius: 20,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing(4),
  },
  logoText: { color: colors.white, fontSize: 40, fontWeight: "700" },
  wordmark: {
    fontSize: 40,
    fontWeight: "700",
    letterSpacing: -1,
    color: colors.ink,
  },
  claim: {
    ...type.bodySecondary,
    marginTop: spacing(2),
    textAlign: "center",
  },
  devBadge: {
    ...type.meta,
    marginTop: spacing(3),
    paddingHorizontal: spacing(3),
    paddingVertical: spacing(1),
    borderRadius: radius.pill,
    backgroundColor: colors.neutralSoft,
    overflow: "hidden",
  },
  actions: { gap: spacing(3) },
  googleBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing(3),
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    height: button.primaryHeight,
    borderRadius: button.radius,
  },
  googleBtnText: { color: colors.ink, fontWeight: "600", fontSize: 16 },
  emailToggle: { alignItems: "center", paddingVertical: spacing(2) },
  emailToggleText: { ...type.meta, color: colors.primary },
  emailForm: { gap: spacing(3) },
  input: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    borderRadius: radius.input,
    paddingHorizontal: spacing(4),
    height: button.primaryHeight,
    backgroundColor: colors.surface,
    color: colors.ink,
    fontSize: 15,
  },
  primaryBtn: {
    backgroundColor: colors.primary,
    height: button.primaryHeight,
    borderRadius: button.radius,
    alignItems: "center",
    justifyContent: "center",
  },
  primaryBtnText: { color: colors.white, fontWeight: "600", fontSize: 16 },
  btnDisabled: { opacity: 0.5 },
  btnPressed: { opacity: 0.85 },
  errorBlock: { gap: spacing(2) },
  error: { color: colors.dangerInk, textAlign: "center", fontSize: 13, lineHeight: 19 },
  supportLine: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 12,
    lineHeight: 18,
    textAlign: "center",
  },
  notice: {
    ...type.bodySecondary,
    fontSize: 13,
    lineHeight: 19,
    textAlign: "center",
  },
  forgotTitle: { ...type.heading, fontSize: 18, textAlign: "center" },
  forgotLead: {
    ...type.bodySecondary,
    fontSize: 13,
    lineHeight: 19,
    textAlign: "center",
  },
  hint: {
    ...type.meta,
    fontWeight: "400",
    textAlign: "center",
  },
  signUpRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    alignItems: "center",
    gap: spacing(2),
    marginTop: spacing(2),
  },
  signUpLink: { ...type.meta, color: colors.primary, fontWeight: "600" },
  versionText: {
    ...type.meta,
    fontWeight: "400",
    fontSize: 11,
    textAlign: "center",
    marginTop: spacing(6),
    opacity: 0.7,
  },
});

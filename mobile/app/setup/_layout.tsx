// Contenedor del asistente de puesta en marcha. El proveedor vive aquí para
// que los 9 pasos compartan una sola carga de ajustes, canales y catálogo:
// moverse entre pasos no vuelve a pedir nada al servidor.
import { Stack } from "expo-router";
import { SetupWizardProvider } from "@/context/SetupWizardContext";
import { colors } from "@/lib/theme";

export default function SetupLayout() {
  return (
    <SetupWizardProvider>
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.bg },
          animation: "fade",
          // La salida se controla desde la pantalla para poder avisar si hay
          // credenciales o textos sin guardar. Evita perderlos con un gesto.
          gestureEnabled: false,
        }}
      />
    </SetupWizardProvider>
  );
}

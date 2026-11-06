import { useCallback } from "react";
import { Alert } from "react-native";
import {
  type LeaveReason,
  useStepSkip,
} from "@/context/SetupWizardContext";
import { warningHaptic } from "@/lib/haptics";

const ACTION_LABEL: Record<LeaveReason, string> = {
  skip: "Saltar sin guardar",
  back: "Volver sin guardar",
  close: "Salir sin guardar",
};

const DESTINATION_TEXT: Record<LeaveReason, string> = {
  skip: "saltas este paso",
  back: "vuelves al paso anterior",
  close: "sales de la puesta en marcha",
};

/** Registra una barrera común para campos locales que aún no se han enviado. */
export function useUnsavedStepGuard(
  isDirty: boolean,
  detail = "Lo que has escrito todavía no se ha guardado."
): void {
  const guard = useCallback(
    (reason: LeaveReason): Promise<boolean> => {
      if (!isDirty) return Promise.resolve(true);
      warningHaptic();
      return new Promise((resolve) => {
        Alert.alert(
          "Tienes cambios sin guardar",
          `${detail} Si ${DESTINATION_TEXT[reason]}, se perderá.`,
          [
            { text: "Seguir aquí", style: "cancel", onPress: () => resolve(false) },
            {
              text: ACTION_LABEL[reason],
              style: "destructive",
              onPress: () => resolve(true),
            },
          ],
          { cancelable: false, onDismiss: () => resolve(false) }
        );
      });
    },
    [detail, isDirty]
  );

  useStepSkip(guard);
}

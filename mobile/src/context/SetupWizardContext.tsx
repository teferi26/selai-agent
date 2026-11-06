// ---------------------------------------------------------------------------
// Shared state for the native "Puesta en marcha" wizard (app/setup/*).
//
// Lives in app/setup/_layout.tsx so the 9 steps share one load of the agency
// settings, the channel status and the catalog check: moving between steps
// never refetches, and any step that connects something refreshes the piece
// it changed for everybody else.
//
// Progress is persisted exactly like the web panel: PATCH
// settings.setup_checklist with the step's key (merged server-side).
// ---------------------------------------------------------------------------
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AgencySettings, fetchAgencySettings, fetchPropertiesSample } from "@/lib/api";
import { ChannelsStatus, SettingsPatch, fetchChannels, patchAgencySettings } from "@/lib/setup-api";
import {
  SetupStep,
  SetupStepKey,
  computeSetupSteps,
  invalidateSetupCache,
} from "@/lib/setup";
import { useAgency } from "@/context/AgencyContext";

/** A step's "Siguiente" handler. Returning false keeps the user on the step
 * (e.g. the profile PATCH failed) so nothing is silently lost. */
export type NextHandler = () => Promise<boolean>;

/**
 * A step's "Saltar por ahora" handler. Same contract as NextHandler: false
 * keeps the user where they are.
 *
 * It exists because skipping is NOT the same as advancing. "Siguiente" saves;
 * "Saltar" doesn't, and a step that holds typed-but-unsaved data (the agency
 * profile, the freshly generated webhook secret) has to be able to say so
 * before that data disappears. Steps with nothing to lose don't register one.
 */
export type LeaveReason = "skip" | "back" | "close";
export type SkipHandler = (reason: LeaveReason) => Promise<boolean>;

type SetupWizardValue = {
  agencyId: string | null;
  agencyName: string;
  slug: string;

  /** First load of settings + catalog. The wizard cannot open without it. */
  loading: boolean;
  error: boolean;
  reload: () => Promise<void>;

  settings: AgencySettings | null;
  hasProperties: boolean;
  checklist: Record<string, boolean>;
  steps: SetupStep[];
  doneMap: Record<SetupStepKey, boolean>;
  doneCount: number;

  /** Channel status loads on its own: only some steps need it, and a failure
   * there must not black out the whole wizard. */
  channels: ChannelsStatus | null;
  channelsLoading: boolean;
  channelsError: boolean;
  refreshChannels: () => Promise<void>;

  refreshSettings: () => Promise<void>;
  refreshProperties: () => Promise<void>;
  /** Ticks one or more checklist keys (merged by the backend). */
  markChecklist: (keys: Record<string, boolean>) => Promise<void>;
  saveSettings: (patch: SettingsPatch) => Promise<void>;

  registerNext: (handler: NextHandler | null) => void;
  runNext: () => Promise<boolean>;

  registerSkip: (handler: SkipHandler | null) => void;
  /** True when the wizard may move on. A step with no handler always can. */
  runSkip: (reason?: LeaveReason) => Promise<boolean>;
};

const Ctx = createContext<SetupWizardValue | null>(null);

const EMPTY_DONE: Record<SetupStepKey, boolean> = {
  agency: false,
  voice: false,
  phone: false,
  whatsapp: false,
  portals: false,
  properties: false,
  webchat: false,
  crm: false,
  test_call: false,
};

export function SetupWizardProvider({ children }: { children: ReactNode }) {
  const { me } = useAgency();
  const agencyId = me?.agency_id ?? null;

  const [settings, setSettings] = useState<AgencySettings | null>(null);
  const [hasProperties, setHasProperties] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const [channels, setChannels] = useState<ChannelsStatus | null>(null);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelsError, setChannelsError] = useState(false);

  const nextRef = useRef<NextHandler | null>(null);
  const skipRef = useRef<SkipHandler | null>(null);

  const reload = useCallback(async () => {
    if (!agencyId) return;
    setLoading(true);
    setError(false);
    try {
      const [s, props] = await Promise.all([
        fetchAgencySettings(agencyId),
        fetchPropertiesSample(agencyId),
      ]);
      setSettings(s);
      setHasProperties(props.length > 0);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [agencyId]);

  const refreshChannels = useCallback(async () => {
    if (!agencyId) return;
    setChannelsLoading(true);
    setChannelsError(false);
    try {
      setChannels(await fetchChannels(agencyId));
    } catch {
      setChannelsError(true);
    } finally {
      setChannelsLoading(false);
    }
  }, [agencyId]);

  const refreshSettings = useCallback(async () => {
    if (!agencyId) return;
    try {
      setSettings(await fetchAgencySettings(agencyId));
      invalidateSetupCache();
    } catch {
      // Keep whatever is on screen: the action that triggered this already
      // reported its own outcome, and a stale read must not blank the step.
    }
  }, [agencyId]);

  const refreshProperties = useCallback(async () => {
    if (!agencyId) return;
    try {
      const props = await fetchPropertiesSample(agencyId);
      setHasProperties(props.length > 0);
      invalidateSetupCache();
    } catch {
      // Same reasoning as refreshSettings.
    }
  }, [agencyId]);

  const saveSettings = useCallback(
    async (patch: SettingsPatch) => {
      if (!agencyId) throw new Error("no_agency");
      const updated = await patchAgencySettings(agencyId, patch);
      setSettings(updated);
      invalidateSetupCache();
    },
    [agencyId]
  );

  const markChecklist = useCallback(
    async (keys: Record<string, boolean>) => {
      await saveSettings({ setup_checklist: keys });
    },
    [saveSettings]
  );

  const registerNext = useCallback((handler: NextHandler | null) => {
    nextRef.current = handler;
  }, []);

  const runNext = useCallback(async () => {
    const handler = nextRef.current;
    if (!handler) return true;
    return handler();
  }, []);

  const registerSkip = useCallback((handler: SkipHandler | null) => {
    skipRef.current = handler;
  }, []);

  const runSkip = useCallback(async (reason: LeaveReason = "skip") => {
    const handler = skipRef.current;
    if (!handler) return true;
    return handler(reason);
  }, []);

  useEffect(() => {
    void reload();
    void refreshChannels();
  }, [reload, refreshChannels]);

  const steps = useMemo(
    () => (settings ? computeSetupSteps(settings.settings, hasProperties) : []),
    [settings, hasProperties]
  );

  const doneMap = useMemo(() => {
    if (steps.length === 0) return EMPTY_DONE;
    const map = { ...EMPTY_DONE };
    for (const s of steps) map[s.key] = s.done;
    return map;
  }, [steps]);

  const value = useMemo<SetupWizardValue>(
    () => ({
      agencyId,
      agencyName: me?.agency_name ?? "",
      slug: me?.agency_slug ?? "",
      loading,
      error,
      reload,
      settings,
      hasProperties,
      checklist: settings?.settings.setup_checklist ?? {},
      steps,
      doneMap,
      doneCount: steps.filter((s) => s.done).length,
      channels,
      channelsLoading,
      channelsError,
      refreshChannels,
      refreshSettings,
      refreshProperties,
      markChecklist,
      saveSettings,
      registerNext,
      runNext,
      registerSkip,
      runSkip,
    }),
    [
      agencyId,
      me?.agency_name,
      me?.agency_slug,
      loading,
      error,
      reload,
      settings,
      hasProperties,
      steps,
      doneMap,
      channels,
      channelsLoading,
      channelsError,
      refreshChannels,
      refreshSettings,
      refreshProperties,
      markChecklist,
      saveSettings,
      registerNext,
      runNext,
      registerSkip,
      runSkip,
    ]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSetupWizard(): SetupWizardValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useSetupWizard must be used inside SetupWizardProvider");
  return ctx;
}

/**
 * Registers this step's "Siguiente" work (saving a form, ticking a key).
 * Steps that just need navigation don't call it.
 */
export function useStepNext(handler: NextHandler | null): void {
  const { registerNext } = useSetupWizard();
  useEffect(() => {
    registerNext(handler);
    return () => registerNext(null);
  }, [registerNext, handler]);
}

/**
 * Registers what this step does when the user presses "Saltar por ahora".
 * Steps that lose nothing by skipping don't call it.
 */
export function useStepSkip(handler: SkipHandler | null): void {
  const { registerSkip } = useSetupWizard();
  useEffect(() => {
    registerSkip(handler);
    return () => registerSkip(null);
  }, [registerSkip, handler]);
}

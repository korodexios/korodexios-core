import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { catalog, emptyPrefs, messages as loadMessages, prefs as loadPrefs, savePrefs } from "./kdx";
import type { CommandSpec, Prefs } from "./kdx";

interface Ctx {
  ready: boolean;
  prefs: Prefs;
  save: (changes: Record<string, unknown>) => Promise<void>;
  commands: CommandSpec[];
  messages: Record<string, string>;
}

const AppState = createContext<Ctx>({
  ready: true,
  prefs: emptyPrefs(),
  save: async () => undefined,
  commands: [],
  messages: {},
});

export const useApp = () => useContext(AppState);

// A text the daemon's catalogue knows is translated; anything else is shown as written.
export function useT(): (text: string) => string {
  const { messages } = useApp();
  return (text) => messages[text] ?? text;
}

export function AppProvider({ children, locale }: { children: ReactNode; locale: string }) {
  const [prefs, setPrefs] = useState<Prefs>(emptyPrefs());
  const [commands, setCommands] = useState<CommandSpec[]>([]);
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [ready, setReady] = useState(false);

  // Forms read the remembered values once, when they mount: wait until both answers are in.
  useEffect(() => {
    const prefsLoaded = loadPrefs().then(setPrefs).catch(() => undefined);
    const catalogLoaded = catalog().then(setCommands).catch(() => undefined);
    void Promise.all([prefsLoaded, catalogLoaded]).then(() => setReady(true));
  }, []);

  useEffect(() => {
    loadMessages(locale).then(setMessages).catch(() => undefined);
  }, [locale]);

  const save = useCallback(async (changes: Record<string, unknown>) => {
    setPrefs(await savePrefs(changes));
  }, []);

  const value = useMemo(
    () => ({ ready, prefs, save, commands, messages }),
    [ready, prefs, save, commands, messages],
  );
  return <AppState.Provider value={value}>{children}</AppState.Provider>;
}

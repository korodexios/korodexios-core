import { reactive } from "vue";

import { catalog, emptyPrefs, messages, prefs, savePrefs } from "./kdx";
import type { CommandSpec, Prefs } from "./kdx";

// One store for the whole app: the person's saved choices, the command catalogue and the
// translated texts. Forms read the remembered values once, so 'ready' waits for both.
export const store = reactive({
  ready: false,
  prefs: emptyPrefs() as Prefs,
  commands: [] as CommandSpec[],
  messages: {} as Record<string, string>,
});

export async function loadState(): Promise<void> {
  store.ready = false;
  await Promise.all([
    prefs().then((loaded) => (store.prefs = loaded)).catch(() => undefined),
    catalog().then((loaded) => (store.commands = loaded)).catch(() => undefined),
  ]);
  store.ready = true;
}

export async function loadMessages(locale: string): Promise<void> {
  store.messages = await messages(locale).catch(() => ({}));
}

export async function save(changes: Record<string, unknown>): Promise<void> {
  store.prefs = await savePrefs(changes);
}

// A text the daemon's catalogue knows is translated; anything else is shown as written.
export const t = (text: string): string => store.messages[text] ?? text;

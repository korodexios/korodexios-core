import { ref, watchEffect } from "vue";

import { api, dig, onTopics } from "./kdx";
import type { PanelSpec } from "./kdx";

// Load a panel's source once, again every 'refresh' seconds, and again whenever a bus event
// named in 'refresh_on' arrives (one shared event stream for all panels).
export function useSource(panel: () => PanelSpec) {
  const data = ref<unknown>(undefined);
  const error = ref("");
  watchEffect((onCleanup) => {
    const spec = panel();
    let alive = true;
    const load = async () => {
      try {
        const payload = await api<unknown>(spec.source ?? "");
        if (alive) {
          data.value = dig(payload, spec.path ?? "");
          error.value = "";
        }
      } catch (reason) {
        if (alive) error.value = (reason as Error).message;
      }
    };
    void load();
    const timer = spec.refresh > 0 ? window.setInterval(() => void load(), spec.refresh * 1000) : undefined;
    const unsubscribe = onTopics(spec.refresh_on ?? [], () => void load());
    // runs before the next load and when the component goes away
    onCleanup(() => {
      alive = false;
      if (timer !== undefined) window.clearInterval(timer);
      unsubscribe();
    });
  });
  return { data, error };
}

export const show = (value: unknown): string =>
  value === null || value === undefined
    ? "-"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

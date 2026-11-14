import type { ComponentType } from "react";

import type { PanelSpec } from "./kdx";

export interface PanelProps {
  module: string;
  panel: PanelSpec;
}

const registry = new Map<string, ComponentType<PanelProps>>();

// Replace the generic drawing of one panel with your own component. The key is
// "<module>.<panel id>", exactly as in 'kdx ui manifest'.
export function overridePanel(key: string, component: ComponentType<PanelProps>): void {
  registry.set(key, component);
}

export function overrideFor(module: string, panel: string): ComponentType<PanelProps> | undefined {
  return registry.get(`${module}.${panel}`);
}

// Example (delete or replace):
//
//   (add this file's own call here, e.g. with a component imported from ./my-panels)
//   overridePanel("job_queue.tasks", ({ panel }) => <h3>{panel.title}: my own view</h3>);

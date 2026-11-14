import type { Component } from "vue";

// Replace the generic drawing of one panel with your own component. The key is
// "<module>.<panel id>", exactly as in 'kdx ui manifest'. The component gets the props
// { module: string, panel: PanelSpec }.
const registry = new Map<string, Component>();

export function overridePanel(key: string, component: Component): void {
  registry.set(key, component);
}

export function overrideFor(module: string, panel: string): Component | undefined {
  return registry.get(`${module}.${panel}`);
}

// Example (delete or replace):
//
//   import QueueTasks from "./my-panels/QueueTasks.vue";
//   overridePanel("job_queue.tasks", QueueTasks);

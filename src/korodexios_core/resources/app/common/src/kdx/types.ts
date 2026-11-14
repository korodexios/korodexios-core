// The shapes of the UI contract (GET /api/v1/ui/manifest, /api/v1/ui/prefs, /api/v1/ui/catalog).
// 'kdx ui types' writes the route list of the live daemon to schema.ts beside this file.

export type PanelType =
  | "table"
  | "keyvalue"
  | "log"
  | "text"
  | "form"
  | "actions"
  | "settings"
  | "embed";
export type CellKind = "text" | "chip" | "bytes" | "duration" | "time" | "link" | "code" | "media";
export type Tone = "ok" | "warn" | "bad" | "busy" | "muted";
export type Scalar = string | number | boolean;

export interface ButtonSpec {
  id: string;
  label: string;
  command: string;
  args: Record<string, Scalar>;
  confirm: boolean;
  danger: boolean;
}

export interface ColumnSpec {
  key: string;
  label: string;
  kind?: CellKind;
  tones?: Record<string, Tone>;
}

export interface PanelSpec {
  id: string;
  type: PanelType;
  title: string;
  help: string;
  refresh: number;
  /** Bus event name prefixes that make the panel load again ("queue.", "player.track"). */
  refresh_on?: string[];
  dock?: "bottom";
  locked?: boolean;
  source?: string;
  path?: string;
  columns?: ColumnSpec[];
  actions?: ButtonSpec[];
  text?: string;
  command?: string;
  args?: Record<string, Scalar>;
  buttons?: ButtonSpec[];
  /** embed: a path of a static mount on this daemon, and the frame height in pixels. */
  src?: string;
  height?: number;
  /** settings: the module or config group the panel edits. */
  module?: string;
  group?: string;
}

export interface ModuleSpec {
  module: string;
  title: string;
  icon: string;
  order: number;
  panels: PanelSpec[];
}

export interface Manifest {
  version: number;
  locale: string;
  modules: ModuleSpec[];
  issues: { module: string; panel?: string; message: string }[];
}

export interface Overlay {
  label?: string;
  hidden?: boolean;
  order?: number;
}

export interface Prefs {
  nav: Record<string, Overlay>;
  panels: Record<string, Overlay>;
  values: Record<string, Record<string, Scalar>>;
  defaults: Record<string, Record<string, Scalar>>;
  settings: { last_tab?: string; locale?: string };
}

export interface CliArgSpec {
  dest: string;
  flags: string[];
  positional: boolean;
  kind: string;
  action: string | null;
  nargs: string | number | null;
  choices: string[];
  default: unknown;
  required: boolean;
  help: string;
}

export interface CommandSpec {
  path: string;
  help: string;
  module: string;
  remote: boolean;
  danger: boolean;
  role: string;
  args: CliArgSpec[];
}

export interface Status {
  app: { name?: string; state: string; version?: string };
  modules: { total: number };
}

export const emptyPrefs = (): Prefs => ({
  nav: {},
  panels: {},
  values: {},
  defaults: {},
  settings: {},
});

import { useEffect, useRef, useState } from "react";
import type { ComponentType, FormEvent } from "react";

import { api, argvFrom, dig, mediaKind, mediaPath, mediaSource, onTopics, runCommand, serveEmbed } from "./kdx";
import type { ButtonSpec, ColumnSpec, PanelType, Scalar } from "./kdx";
import { overrideFor } from "./overrides";
import type { PanelProps } from "./overrides";
import { useApp, useT } from "./state";

// Load a panel's source once, again every 'refresh' seconds, and again whenever a bus event
// named in 'refresh_on' arrives (one shared event stream for all panels).
function useSource(source: string, path: string, refresh: number, refreshOn: readonly string[] = []) {
  const [state, setState] = useState<{ data?: unknown; error?: string }>({});
  const topics = refreshOn.join(" ");
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const payload = await api<unknown>(source);
        if (alive) setState({ data: dig(payload, path) });
      } catch (error) {
        if (alive) setState({ error: (error as Error).message });
      }
    };
    void load();
    const timer = refresh > 0 ? window.setInterval(() => void load(), refresh * 1000) : undefined;
    const unsubscribe = onTopics(topics ? topics.split(" ") : [], () => void load());
    return () => {
      alive = false;
      if (timer !== undefined) window.clearInterval(timer);
      unsubscribe();
    };
  }, [source, path, refresh, topics]);
  return state;
}

const show = (value: unknown): string =>
  value === null || value === undefined
    ? "-"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

// A media cell: plays or shows a file of a static mount of this daemon (src/kdx/media.ts).
function Media({ value }: { value: unknown }) {
  const path = mediaPath(value);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let release: () => void = () => undefined;
    mediaSource(path, controller.signal)
      .then((source) => {
        release = source.release;
        setUrl(source.url);
      })
      .catch((reason: Error) => setError(reason.message));
    return () => {
      controller.abort();
      release();
    };
  }, [path]);
  if (!path) return <>{show(value)}</>;
  if (error) return <span className="error">{error}</span>;
  if (!url) return <span className="muted">…</span>;
  switch (mediaKind(path)) {
    case "audio":
      return <audio controls preload="none" src={url} />;
    case "video":
      return <video controls preload="none" src={url} className="media" />;
    case "image":
      return <img src={url} alt={path} className="media" />;
    default:
      return (
        <a href={url} target="_blank" rel="noopener noreferrer">
          {path.split("/").pop()}
        </a>
      );
  }
}

function Cell({ column, value }: { column: ColumnSpec; value: unknown }) {
  return column.kind === "media" ? <Media value={value} /> : <>{show(value)}</>;
}

function TablePanel({ panel }: PanelProps) {
  const t = useT();
  const { data, error } = useSource(panel.source ?? "", panel.path ?? "", panel.refresh, panel.refresh_on);
  if (error) return <p className="error">{error}</p>;
  const rows = Array.isArray(data) ? (data as unknown[]) : [];
  const first = rows[0];
  const columns: ColumnSpec[] = panel.columns?.length
    ? panel.columns
    : first && typeof first === "object"
      ? Object.keys(first as object).map((key) => ({ key, label: key }))
      : [{ key: "", label: "value" }];
  return (
    <table>
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.key}>{column.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length ? (
          rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key}>
                  <Cell column={column} value={column.key ? dig(row, column.key) : row} />
                </td>
              ))}
            </tr>
          ))
        ) : (
          <tr>
            <td colSpan={columns.length} className="muted">
              {t("Nothing to show.")}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function KeyValuePanel({ panel }: PanelProps) {
  const { data, error } = useSource(panel.source ?? "", panel.path ?? "", panel.refresh, panel.refresh_on);
  if (error) return <p className="error">{error}</p>;
  const pairs: [string, unknown][] =
    data && typeof data === "object" ? Object.entries(data) : [["value", data]];
  return (
    <table>
      <tbody>
        {pairs.map(([key, value]) => (
          <tr key={key}>
            <th>{key}</th>
            <td>{show(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LogPanel({ panel }: PanelProps) {
  const { data, error } = useSource(panel.source ?? "", panel.path ?? "", panel.refresh, panel.refresh_on);
  if (error) return <p className="error">{error}</p>;
  const text = Array.isArray(data) ? data.join("\n") : show(data === undefined ? "" : data);
  return <pre className="terminal">{text}</pre>;
}

function Output({ text }: { text: string }) {
  return text ? <pre className="terminal">{text}</pre> : null;
}

function FormPanel(props: PanelProps) {
  const { ready } = useApp();
  return ready ? <FormFields {...props} /> : null;
}

function FormFields({ module, panel }: PanelProps) {
  const t = useT();
  const { commands, prefs, save } = useApp();
  const command = commands.find((item) => item.path === panel.command);
  const key = `${module}.${panel.id}`;
  const [values, setValues] = useState<Record<string, Scalar>>({
    ...(panel.args ?? {}),
    ...(prefs.defaults[key] ?? {}),
    ...(prefs.values[key] ?? {}),
  });
  const [output, setOutput] = useState("");
  if (!command) return <p className="muted">{panel.command}</p>;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await save({ values: { [key]: values } });
    const result = await runCommand(command.path, argvFrom(command, values), setOutput);
    setOutput(result.output);
  };

  return (
    <form onSubmit={(event) => void submit(event)}>
      {command.args.map((arg) => {
        const label = arg.positional ? arg.dest : arg.flags[arg.flags.length - 1];
        const current = values[arg.dest];
        const set = (value: Scalar) => setValues({ ...values, [arg.dest]: value });
        return (
          <label key={arg.dest} className="field">
            <span>{label}</span>
            {arg.kind === "flag" ? (
              <input type="checkbox" checked={current === true} onChange={(e) => set(e.target.checked)} />
            ) : arg.choices.length ? (
              <select value={String(current ?? "")} onChange={(e) => set(e.target.value)}>
                <option value="">(default)</option>
                {arg.choices.map((choice) => (
                  <option key={choice}>{choice}</option>
                ))}
              </select>
            ) : (
              <input
                type={arg.kind === "int" || arg.kind === "float" ? "number" : "text"}
                value={String(current ?? "")}
                placeholder={arg.default != null ? `default: ${String(arg.default)}` : ""}
                onChange={(e) => set(e.target.value)}
              />
            )}
            <small className="muted">{arg.help}</small>
          </label>
        );
      })}
      <div className="row">
        <button type="submit" className="primary">
          {t("Run")}
        </button>
        <button type="button" onClick={() => void save({ defaults: { [key]: values } })}>
          {t("Save as default")}
        </button>
        <button
          type="button"
          onClick={() => setValues({ ...(panel.args ?? {}), ...(prefs.defaults[key] ?? {}) })}
        >
          {t("Restore defaults")}
        </button>
      </div>
      <Output text={output} />
    </form>
  );
}

function ActionButton({ button }: { button: ButtonSpec }) {
  const t = useT();
  const { commands } = useApp();
  const [armed, setArmed] = useState(false);
  const [output, setOutput] = useState("");
  const command = commands.find((item) => item.path === button.command);
  const click = async () => {
    if ((button.confirm || button.danger) && !armed) {
      setArmed(true);
      window.setTimeout(() => setArmed(false), 5000);
      return;
    }
    setArmed(false);
    if (!command) return;
    const result = await runCommand(command.path, argvFrom(command, button.args), setOutput);
    setOutput(result.output);
  };
  return (
    <div>
      <button
        type="button"
        className={button.danger ? "danger" : ""}
        disabled={!command}
        onClick={() => void click()}
      >
        {armed ? t("Click again to run") : button.label}
      </button>
      <Output text={output} />
    </div>
  );
}

function ActionsPanel({ panel }: PanelProps) {
  return (
    <div className="row">
      {(panel.buttons ?? []).map((button) => (
        <ActionButton key={button.id} button={button} />
      ))}
    </div>
  );
}

// A page of a static mount, framed; it gets this app's session by the embed handshake (v1).
function EmbedPanel({ panel }: PanelProps) {
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(() => (frame.current ? serveEmbed(frame.current) : undefined), []);
  return (
    <iframe
      ref={frame}
      className="embed"
      src={panel.src}
      title={panel.title || panel.id}
      sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
      referrerPolicy="no-referrer"
      style={{ height: `${panel.height ?? 360}px` }}
    />
  );
}

function SettingsPanel({ panel }: PanelProps) {
  const t = useT();
  const target = panel.module ? `--module ${panel.module}` : panel.group ? `--group ${panel.group}` : "";
  return (
    <p className="muted">
      {t("Settings are edited in the built-in console or in the terminal:")}{" "}
      <code>kdx config schema {target}</code>
    </p>
  );
}

// Every panel type has a drawing: a type added to the contract fails the type check here.
const GENERIC: Record<PanelType, ComponentType<PanelProps>> = {
  table: TablePanel,
  keyvalue: KeyValuePanel,
  log: LogPanel,
  text: ({ panel }: PanelProps) => <p>{panel.text}</p>,
  form: FormPanel,
  actions: ActionsPanel,
  embed: EmbedPanel,
  settings: SettingsPanel,
};

export function Panel({ module, panel }: PanelProps) {
  const Custom = overrideFor(module, panel.id);
  const Component = Custom ?? GENERIC[panel.type];
  return (
    <section className="card panel" data-panel={`${module}.${panel.id}`}>
      <h2>{panel.title}</h2>
      {panel.help ? <p className="muted">{panel.help}</p> : null}
      <Component module={module} panel={panel} />
    </section>
  );
}

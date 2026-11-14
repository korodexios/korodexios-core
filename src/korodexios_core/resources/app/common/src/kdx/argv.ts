import type { CommandSpec } from "./types";

// Turn { dest: value } into the argv a terminal user would type; the daemon parses it with
// the command's own declaration, so validation is identical to the CLI.
export function argvFrom(command: CommandSpec, values: Record<string, unknown>): string[] {
  const argv: string[] = [];
  for (const arg of command.args) {
    const value = values[arg.dest];
    if (value === undefined || value === null || value === "") continue;
    const flag = arg.flags[arg.flags.length - 1];
    if (arg.kind === "flag") {
      const on = value === true || value === "true";
      if (on !== (arg.default === true)) argv.push(flag);
      continue;
    }
    const many = Array.isArray(value)
      ? value.map(String)
      : arg.nargs === "*" || arg.nargs === "+"
        ? String(value).split(/\s+/)
        : [String(value)];
    if (arg.positional) {
      argv.push(...many);
    } else if (arg.action === "append") {
      for (const part of many) argv.push(flag, part);
    } else {
      argv.push(flag, ...many);
    }
  }
  return argv;
}

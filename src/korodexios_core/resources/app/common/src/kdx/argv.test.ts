import { expect, test } from "vitest";

import { argvFrom } from "./argv";
import type { CommandSpec } from "./types";

const command: CommandSpec = {
  path: "queue submit",
  help: "",
  module: "job_queue",
  remote: true,
  danger: false,
  role: "operator",
  args: [
    { dest: "kind", flags: ["kind"], positional: true, kind: "str", action: null, nargs: null, choices: [], default: null, required: true, help: "" },
    { dest: "delay", flags: ["--delay"], positional: false, kind: "float", action: null, nargs: null, choices: [], default: 0, required: false, help: "" },
    { dest: "as_json", flags: ["--json"], positional: false, kind: "flag", action: "store_true", nargs: null, choices: [], default: false, required: false, help: "" },
  ],
};

test("builds the argv a terminal user would type", () => {
  expect(argvFrom(command, { kind: "demo", delay: 2, as_json: true })).toEqual([
    "demo",
    "--delay",
    "2",
    "--json",
  ]);
  expect(argvFrom(command, { kind: "demo", delay: "", as_json: false })).toEqual(["demo"]);
});

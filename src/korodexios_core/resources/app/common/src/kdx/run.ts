import { api } from "./api";

export interface RunResult {
  ok: boolean;
  output: string;
}

interface Job {
  state: string;
  output?: string;
  error?: string;
  exit_code?: number | null;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Every command runs as a background job: the reply is immediate and the output is polled,
// so a long command never holds an HTTP request open.
export async function runCommand(
  command: string,
  argv: string[],
  onOutput?: (text: string) => void,
): Promise<RunResult> {
  const started = await api<{ ok?: boolean; error?: string; job?: { id: string } }>(
    "/api/v1/rpc",
    { method: "POST", body: { command, args: { argv }, background: true } },
  );
  if (!started.ok || !started.job) return { ok: false, output: started.error ?? "refused" };
  for (;;) {
    const job = await api<Job>(`/api/v1/jobs?id=${encodeURIComponent(started.job.id)}`);
    if (job.state === "running" || job.state === "cancelling") {
      onOutput?.(job.output ?? "");
      await sleep(500);
      continue;
    }
    const text = `${job.output ?? ""}${job.error ?? ""}` || "(no output)";
    return { ok: job.state === "done", output: text };
  }
}

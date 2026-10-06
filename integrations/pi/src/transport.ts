import { spawn } from "node:child_process";

export type Outcome = {
  action?: "allow" | "inject_context" | "block" | "halt";
  reason?: string;
  system_message?: string;
};
export type Envelope = {
  session_id: string;
  cwd: string;
  work_id?: string;
  event_id?: string;
  continuation_active?: boolean;
  event: Record<string, unknown>;
};
export type BridgeOptions = {
  /** Executable and literal arguments; never passed through a shell. */
  command?: readonly [string, ...string[]];
  dataDir?: string;
  timeoutMs?: number;
};

export function text(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part?.type === "text")
    .map((part) => part.text)
    .join("\n");
}

export function reason(result: Outcome): string {
  return (
    result.system_message ?? result.reason ?? "Foreman blocked this operation"
  );
}

export function createTransport(
  client: "pi" | "pi-durable",
  options: BridgeOptions = {},
) {
  const command = options.command ?? ["foreman"];
  const timeoutMs = options.timeoutMs ?? 120_000;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0)
    throw new Error("Invalid Foreman timeout");
  return (payload: Envelope, signal?: AbortSignal): Promise<Outcome> =>
    new Promise((resolve, reject) => {
      const args = [...command.slice(1), "hook", "--client", client];
      if (options.dataDir) args.push("--data-dir", options.dataDir);
      // Full arguments go over stdin; process limits must never truncate evidence.
      const input = JSON.stringify(payload);
      const child = spawn(command[0], args, {
        cwd: payload.cwd,
        stdio: ["pipe", "pipe", "pipe"],
      });
      let output = "";
      let bytes = 0;
      let settled = false;
      const finish = (error?: Error, value?: Outcome) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        if (error) {
          child.kill("SIGKILL");
          reject(error);
        } else resolve(value!);
      };
      const abort = () => finish(new Error("Foreman hook cancelled"));
      const timer = setTimeout(
        () => finish(new Error("Foreman hook timed out")),
        timeoutMs,
      );
      child.on("error", () =>
        finish(new Error("Unable to start Foreman hook executable")),
      );
      child.stdin.on("error", () =>
        finish(new Error("Unable to send Foreman hook input")),
      );
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk: string) => {
        bytes += Buffer.byteLength(chunk);
        if (bytes > 1_048_576)
          finish(new Error("Foreman response exceeded 1 MiB"));
        else output += chunk;
      });
      // Drain diagnostics without retaining or exposing potentially sensitive evidence.
      child.stderr.resume();
      child.on("close", (code) => {
        if (settled) return;
        if (code !== 0)
          return finish(new Error(`Foreman hook exited with status ${code}`));
        try {
          const value = JSON.parse(output);
          if (
            !value ||
            Array.isArray(value) ||
            typeof value !== "object" ||
            Object.keys(value).some(
              (key) => !["action", "reason", "system_message"].includes(key),
            ) ||
            (value.action !== undefined &&
              !["allow", "inject_context", "block", "halt"].includes(
                value.action,
              )) ||
            [value.reason, value.system_message].some(
              (item) => item !== undefined && typeof item !== "string",
            ) ||
            (value.action === undefined && Object.keys(value).length !== 0)
          ) {
            throw new Error("Invalid outcome");
          }
          finish(undefined, value);
        } catch {
          finish(new Error("Invalid Foreman hook response"));
        }
      });
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) abort();
      else child.stdin.end(input);
    });
}

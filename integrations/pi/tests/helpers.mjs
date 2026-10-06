import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
const fixture = fileURLToPath(new URL("./fixture.mjs", import.meta.url));
export function setup(t, mode = "allow") {
  const dir = mkdtempSync(join(tmpdir(), "foreman-pi-"));
  const log = join(dir, "events.jsonl");
  writeFileSync(log, "");
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return {
    dir,
    log,
    command: [process.execPath, fixture, log, mode],
    events: () =>
      readFileSync(log, "utf8")
        .trim()
        .split("\n")
        .filter(Boolean)
        .map(JSON.parse),
  };
}

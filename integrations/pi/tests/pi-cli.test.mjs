import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { delimiter } from "node:path";
import { test } from "node:test";
import { setup } from "./helpers.mjs";
const cli = fileURLToPath(
  new URL(
    "../node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js",
    import.meta.url,
  ),
);
const bridge = fileURLToPath(new URL("../dist/pi.js", import.meta.url));
const provider = fileURLToPath(new URL("./cli-provider.mjs", import.meta.url));
const fixture = new URL("./fixture.mjs", import.meta.url).href;
test("actual Pi CLI loads the built extension and forwards its lifecycle", (t) => {
  const h = setup(t);
  mkdirSync(h.dir + "/bin");
  const shim = h.dir + "/bin/foreman";
  writeFileSync(
    shim,
    "#!/usr/bin/env node\nprocess.argv.splice(2, 0, " +
      JSON.stringify(h.log) +
      ', "allow");\nawait import(' +
      JSON.stringify(fixture) +
      ");\n",
  );
  chmodSync(shim, 0o755);
  const result = spawnSync(
    process.execPath,
    [
      cli,
      "--print",
      "--mode",
      "json",
      "--no-session",
      "--no-tools",
      "--no-extensions",
      "--no-skills",
      "--no-prompt-templates",
      "-e",
      provider,
      "-e",
      bridge,
      "--provider",
      "faux",
      "--model",
      "faux-1",
      "Fix the project",
    ],
    {
      cwd: h.dir,
      encoding: "utf8",
      timeout: 20_000,
      env: {
        ...process.env,
        PI_CODING_AGENT_DIR: h.dir + "/agent",
        PI_OFFLINE: "1",
        PI_TELEMETRY: "0",
        PATH: h.dir + "/bin" + delimiter + process.env.PATH,
      },
    },
  );
  assert.equal(result.status, 0, result.stderr + result.stdout);
  assert.ok(result.stdout.includes("CLI smoke complete"));
  const types = h.events().map((e) => e.event.type);
  assert.ok(types.includes("session_start"), JSON.stringify(types));
  assert.ok(types.includes("input"), JSON.stringify(types));
  assert.ok(types.includes("agent_before_settle"), JSON.stringify(types));
});

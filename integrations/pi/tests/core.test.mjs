import assert from "node:assert/strict";
import { writeFileSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { createTransport } from "../dist/transport.js";
import { setup } from "./helpers.mjs";
const fixture = fileURLToPath(new URL("./core-fixture.py", import.meta.url));
// CI installs Foreman into Python's environment. Local development may set
// FOREMAN_TEST_PYTHON to the repository virtualenv's absolute executable path.
const python = process.env.FOREMAN_TEST_PYTHON ?? "python3";
for (const client of ["pi", "pi-durable"]) {
  test(`bridge talks to the actual Foreman CLI (${client})`, async (t) => {
    const state = setup(t);
    const data = state.dir + "/foreman";
    const config = state.dir + "/config.toml";
    writeFileSync(config, '[hooks]\nrepositories = ["' + state.dir + '"]\n');
    const old = process.env.FOREMAN_CONFIG;
    process.env.FOREMAN_CONFIG = config;
    t.after(() =>
      old === undefined
        ? delete process.env.FOREMAN_CONFIG
        : (process.env.FOREMAN_CONFIG = old),
    );
    const send = createTransport(client, {
      command: [python, fixture],
      dataDir: data,
    });
    const base = {
      session_id: "native-session",
      cwd: state.dir,
      work_id: "original-work",
    };
    const start =
      client === "pi"
        ? { type: "input", text: "Fix the tests" }
        : { type: "beforeRequest", prompt: "Fix the tests" };
    const outcome = await send({ ...base, event: start });
    assert.equal(outcome.action, "inject_context");
    const tool =
      client === "pi"
        ? {
            type: "tool_call",
            toolName: "bash",
            toolCallId: "c1",
            input: { command: "pytest" },
          }
        : {
            type: "beforeTool",
            call: { name: "bash", id: "c1", arguments: { command: "pytest" } },
          };
    const toolOutcome = await send({ ...base, event: tool });
    assert.ok(
      [undefined, "inject_context", "block"].includes(toolOutcome.action),
    );
    const file = resolve(
      data,
      "sessions",
      readdirSync(data + "/sessions").find((f) => f.endsWith(".json")),
    );
    const session = JSON.parse(readFileSync(file, "utf8"));
    assert.equal(session.client, client);
    assert.equal(session.state.job, "Fix the tests");
    assert.equal(session.state.workers[0].client, client);
  });
}

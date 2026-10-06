import assert from "node:assert/strict";
import { tmpdir } from "node:os";
import { test } from "node:test";
import { createTransport } from "../dist/transport.js";
import { setup } from "./helpers.mjs";
const payload = {
  session_id: "s",
  cwd: tmpdir(),
  event: {
    type: "tool_call",
    toolName: "bash",
    input: { command: "echo `$(literal)`; x".repeat(5000) },
  },
};
test("transport preserves full arguments and shell metacharacters", async (t) => {
  const state = setup(t);
  assert.deepEqual(await createTransport("pi", state)(payload), {});
  assert.deepEqual(state.events()[0], payload);
});
for (const [mode, message] of [
  ["error", /status 2/],
  ["invalid", /Invalid Foreman/],
  ["flood", /exceeded/],
  ["timeout", /timed out/],
]) {
  test(`transport fails closed on ${mode}`, async (t) => {
    const state = setup(t, mode);
    await assert.rejects(
      createTransport("pi", {
        ...state,
        timeoutMs: mode === "timeout" ? 100 : 2000,
      })(payload),
      message,
    );
  });
}
test("transport fails closed when executable is missing", async () => {
  await assert.rejects(
    createTransport("pi", { command: ["/does/not/exist"] })(payload),
    /Unable to start/,
  );
});
test("transport cancels a child process", async (t) => {
  const state = setup(t, "timeout");
  const controller = new AbortController();
  const pending = createTransport("pi", state)(payload, controller.signal);
  controller.abort();
  await assert.rejects(pending, /cancelled/);
});

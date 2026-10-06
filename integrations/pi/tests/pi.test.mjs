import assert from "node:assert/strict";
import { test } from "node:test";
import { createForemanPi } from "../dist/pi.js";
import { setup } from "./helpers.mjs";
function host(t, mode = "allow") {
  const state = setup(t, mode),
    handlers = new Map(),
    messages = [],
    notices = [];
  let aborted = 0;
  const pi = {
    on: (name, fn) => handlers.set(name, fn),
    sendMessage: (message) => messages.push(message),
  };
  const ctx = {
    cwd: state.dir,
    sessionManager: { getSessionId: () => "pi-session" },
    hasUI: true,
    ui: { notify: (message) => notices.push(message) },
    abort: () => aborted++,
  };
  createForemanPi(state)(pi);
  return {
    ...state,
    handlers,
    messages,
    notices,
    ctx,
    aborted: () => aborted,
    emit: (type, fields = {}) => handlers.get(type)({ type, ...fields }, ctx),
  };
}
const tool = {
  toolName: "bash",
  toolCallId: "c1",
  input: { command: "dangerous" },
};
const settle = {
  outcome: "completed",
  context: {
    contextMessages: [
      { role: "assistant", content: [{ type: "text", text: "Not done" }] },
    ],
  },
};
test("Pi routing context, denial and session cleanup use native event APIs", async (t) => {
  const h = host(t);
  assert.equal(h.events().length, 0);
  await h.emit("session_start");
  assert.equal(await h.emit("input", { text: "Fix the tests" }), undefined);
  assert.equal(
    (await h.emit("before_agent_start")).message.content,
    "Follow the project checkpoint",
  );
  assert.deepEqual(await h.emit("tool_call", tool), {
    block: true,
    reason: "Denied operation",
    terminate: false,
  });
  await h.emit("session_shutdown");
  assert.deepEqual(
    h.events().map((e) => e.event.type),
    ["session_start", "input", "tool_call", "session_shutdown"],
  );
  assert.equal(h.events()[1].work_id, h.events()[2].work_id);
});
test("Pi consumes refused input before agent startup", async (t) => {
  const h = host(t, "block-input");
  assert.deepEqual(await h.emit("input", { text: "Fix this" }), {
    action: "handled",
  });
  assert.equal((await h.emit("tool_call", tool)).block, true);
});
test("Pi hook process errors prevent tools and consume input", async (t) => {
  const h = host(t, "error");
  assert.deepEqual(await h.emit("input", { text: "Fix this" }), {
    action: "handled",
  });
  assert.equal((await h.emit("tool_call", tool)).block, true);
  assert.equal(h.notices.length, 1);
});
test("Pi sends steering and retains structured results", async (t) => {
  const h = host(t, "steer-tools");
  await h.emit("input", { text: "Fix this" });
  await h.emit("tool_call", { ...tool, input: { command: "safe" } });
  assert.equal(h.messages[0].content, "Check the tests first");
  const content = [{ type: "text", text: "Actual output" }];
  const result = await h.emit("tool_result", {
    ...tool,
    content,
    isError: false,
    structuredContent: { actual: true },
  });
  assert.deepEqual(result.content, [
    ...content,
    { type: "text", text: "Check the tests first" },
  ]);
  assert.deepEqual(result.structuredContent, { actual: true });
});
test("Pi completion continuation and halt are distinct", async (t) => {
  const h = host(t, "continue");
  await h.emit("input", { text: "Fix this" });
  assert.equal((await h.emit("agent_before_settle", settle)).continue, true);
  assert.equal(
    (await h.emit("agent_before_settle", settle)).continue,
    undefined,
  );
  assert.equal(h.notices[0], "Finish the tests");
});
test("Pi post-tool halt aborts the operation and retains actual output", async (t) => {
  const h = host(t, "halt");
  const result = await h.emit("tool_result", {
    ...tool,
    content: [{ type: "text", text: "Actual output" }],
  });
  assert.equal(h.aborted(), 1);
  assert.equal(result.isError, true);
  assert.equal(result.content[0].text, "Actual output");
});
test("Pi aborted settlement does not request a continuation", async (t) => {
  const h = host(t);
  await h.emit("agent_before_settle", { ...settle, outcome: "aborted" });
  assert.equal(h.events().length, 0);
});

import assert from "node:assert/strict";
import { test } from "node:test";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { Type } from "@earendil-works/pi-ai";
import { createModels } from "@earendil-works/pi-ai/models";
import {
  fauxAssistantMessage,
  fauxProvider,
  fauxToolCall,
} from "@earendil-works/pi-ai/providers/faux";
import {
  createRegistry,
  defineExtension,
  defineTool,
  Harness,
  MemoryStorage,
} from "@earendil-works/pi-durable";
import { openNodeJsonlStorage } from "@earendil-works/pi-durable/storage/jsonl/node";
import { createForemanDurable } from "../dist/durable.js";
import { setup } from "./helpers.mjs";
const context = BACKGROUND_CONTEXT;
async function harness(t, mode = "allow", persistent = false) {
  const state = setup(t, mode),
    faux = fauxProvider(),
    models = createModels(),
    registry = createRegistry();
  models.setProvider(faux.provider);
  let current,
    executed = 0;
  registry.install(
    defineExtension({
      name: "test-tools",
      tools: [
        defineTool({
          name: "probe",
          description: "A test tool",
          parameters: Type.Object({ value: Type.String() }),
          execute: async (args) => {
            executed++;
            return { content: [{ type: "text", text: args.value }] };
          },
        }),
      ],
    }),
  );
  registry.install(
    createForemanDurable({
      ...state,
      sessionId: "persistent-storage-1",
      harness: () => current,
      cwd: state.dir,
    }),
  );
  const open = async () => {
    const storage = persistent
      ? await openNodeJsonlStorage(state.dir + "/storage", context)
      : new MemoryStorage();
    current = await Harness.open(
      storage,
      {
        models,
        registry,
        settings: { retry: { enabled: false }, compaction: { enabled: false } },
      },
      context,
    );
    return current;
  };
  await open();
  t.after(async () => current.close(context));
  return {
    ...state,
    faux,
    models,
    registry,
    open,
    current: () => current,
    executed: () => executed,
    root: () =>
      current.root(context, {
        agent: { model: { provider: "faux", modelId: "faux-1" } },
      }),
  };
}
test("actual durable task hooks block tool execution and keep original work across rounds", async (t) => {
  const h = await harness(t);
  const root = await h.root();
  h.faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("probe", { value: "dangerous" }, { id: "c1" }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage("Finished"),
  ]);
  const status = await (
    await root.submit(
      { type: "input", content: "Fix the original task", requestId: "work-1" },
      context,
    )
  ).wait(context);
  assert.equal(status.status, "done");
  assert.equal(h.executed(), 0);
  const requests = h.events().filter((e) => e.event.type === "beforeRequest");
  assert.equal(requests.length, 2);
  assert.equal(requests[0].work_id, requests[1].work_id);
  assert.ok(requests.every((e) => e.event.prompt === "Fix the original task"));
  const transcript = await root.context(context);
  assert.ok(JSON.stringify(transcript.messages).includes("Denied operation"));
});
test("actual durable afterTool preserves results and emits steering", async (t) => {
  const h = await harness(t, "steer-tools");
  const root = await h.root();
  // A beforeTool steering response is a denial in Durable's limited API.
  h.faux.setResponses([
    fauxAssistantMessage(fauxToolCall("probe", { value: "safe" }), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage("Finished"),
  ]);
  await (
    await root.submit({ type: "input", content: "Fix it" }, context)
  ).wait(context);
  assert.equal(h.executed(), 0);
  assert.ok(
    JSON.stringify((await root.context(context)).messages).includes(
      "Check the tests first",
    ),
  );
});
test("actual durable allowed tool records original result through afterTool", async (t) => {
  const h = await harness(t);
  const root = await h.root();
  h.faux.setResponses([
    fauxAssistantMessage(fauxToolCall("probe", { value: "actual output" }), {
      stopReason: "toolUse",
    }),
    fauxAssistantMessage("Finished"),
  ]);
  await (
    await root.submit({ type: "input", content: "Fix it" }, context)
  ).wait(context);
  assert.equal(h.executed(), 1);
  const event = h.events().find((e) => e.event.type === "afterTool");
  assert.equal(event.event.result.content[0].text, "actual output");
  assert.equal(event.event.call.arguments.value, "actual output");
});
test("actual durable completion continuation retains the job and halts on refusal", async (t) => {
  const h = await harness(t, "continue");
  const root = await h.root();
  h.faux.setResponses([
    fauxAssistantMessage("Incomplete"),
    fauxAssistantMessage("Still incomplete"),
  ]);
  const status = await (
    await root.submit({ type: "input", content: "Original job" }, context)
  ).wait(context);
  assert.equal(h.faux.state.callCount, 2);
  assert.equal(status.status, "unanswered");
  const inputs = h.events().filter((e) => e.event.type === "beforeRequest");
  assert.ok(inputs.every((e) => e.event.prompt === "Original job"));
  assert.equal(inputs[0].work_id, inputs[1].work_id);
});
test("durable routing process failure prevents the first provider request", async (t) => {
  const h = await harness(t, "error");
  const root = await h.root();
  const status = await (
    await root.submit({ type: "input", content: "Fix it" }, context)
  ).wait(context);
  assert.equal(status.status, "unanswered");
  assert.equal(h.faux.state.callCount, 0);
});
test("durable storage reopen and new input maintain namespace and distinct work identity", async (t) => {
  const h = await harness(t, "allow", true);
  let root = await h.root();
  h.faux.setResponses([
    fauxAssistantMessage("Done"),
    fauxAssistantMessage("Done again"),
  ]);
  await (
    await root.submit(
      { type: "input", content: "First job", requestId: "first" },
      context,
    )
  ).wait(context);
  const id = root.id;
  await h.current().close(context);
  await h.open();
  root = await h.root();
  assert.equal(root.id, id);
  await (
    await root.submit(
      { type: "input", content: "Second job", requestId: "second" },
      context,
    )
  ).wait(context);
  const inputs = h.events().filter((e) => e.event.type === "beforeRequest");
  assert.equal(inputs[0].session_id, inputs[1].session_id);
  assert.notEqual(inputs[0].work_id, inputs[1].work_id);
  assert.equal(inputs[1].event.prompt, "Second job");
});

test("durable post-tool halt aborts mixed parallel rounds before another model request", async (t) => {
  const h = await harness(t, "halt");
  const root = await h.root();
  h.faux.setResponses([
    fauxAssistantMessage(
      [
        fauxToolCall("probe", { value: "actual result" }, { id: "c1" }),
        fauxToolCall("probe", { value: "dangerous" }, { id: "c2" }),
      ],
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage("Must not run"),
  ]);
  const status = await (
    await root.submit({ type: "input", content: "Fix it" }, context)
  ).wait(context);
  assert.equal(status.status, "unanswered");
  assert.equal(h.faux.state.callCount, 1);
  assert.equal(h.executed(), 1);
  assert.ok(
    JSON.stringify((await root.context(context)).messages).includes(
      "actual result",
    ),
  );
});

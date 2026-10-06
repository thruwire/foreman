import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import {
  fauxAssistantMessage,
  fauxProvider,
} from "@earendil-works/pi-ai/providers/faux";
import { createForemanPi } from "../dist/pi.js";
import { setup } from "./helpers.mjs";
async function create(t, mode) {
  const state = setup(t, mode),
    faux = fauxProvider();
  const modelRuntime = await ModelRuntime.create({
    authPath: state.dir + "/auth.json",
    modelsPath: null,
    modelsStorePath: state.dir + "/models.json",
    refreshOnCreate: false,
  });
  modelRuntime.registerNativeProvider(faux.provider);
  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: false },
    retry: { enabled: false },
  });
  const resourceLoader = new DefaultResourceLoader({
    cwd: state.dir,
    agentDir: state.dir,
    settingsManager,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    extensionFactories: [createForemanPi(state)],
  });
  await resourceLoader.reload();
  const { session } = await createAgentSession({
    cwd: state.dir,
    agentDir: state.dir,
    modelRuntime,
    model: faux.getModel(),
    settingsManager,
    resourceLoader,
    sessionManager: SessionManager.inMemory(state.dir),
    noTools: "all",
  });
  await session.bindExtensions({});
  t.after(() => session.dispose());
  return { ...state, session, faux };
}
test("actual Pi SDK consumes denied input without calling the provider", async (t) => {
  const h = await create(t, "block-input");
  await h.session.prompt("Fix the project");
  assert.equal(h.faux.state.callCount, 0);
  assert.ok(h.events().some((e) => e.event.type === "input"));
});
test("actual Pi SDK injects context and uses the final actionable completion event", async (t) => {
  const h = await create(t, "allow");
  h.faux.setResponses([fauxAssistantMessage("Finished")]);
  await h.session.prompt("Fix the project");
  assert.equal(h.faux.state.callCount, 1);
  assert.ok(h.events().some((e) => e.event.type === "agent_before_settle"));
  assert.ok(
    h.session.state.messages.some(
      (m) =>
        m.role === "custom" && m.content === "Follow the project checkpoint",
    ),
  );
});

test("actual Pi SDK requests one continuation then reports the halt", async (t) => {
  const h = await create(t, "continue");
  h.faux.setResponses([
    fauxAssistantMessage("Not finished"),
    fauxAssistantMessage("Still unfinished"),
  ]);
  await h.session.prompt("Fix the project");
  assert.equal(h.faux.state.callCount, 2);
  assert.equal(h.events().filter((e) => e.event.type === "input").length, 1);
  assert.equal(
    h.events().filter((e) => e.event.type === "agent_before_settle").length,
    2,
  );
});

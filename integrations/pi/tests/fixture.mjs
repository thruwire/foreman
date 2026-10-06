import { appendFileSync, readFileSync } from "node:fs";
const [log, mode] = process.argv.slice(2);
let input = "";
for await (const chunk of process.stdin) input += chunk;
const payload = JSON.parse(input);
const previous = readFileSync(log, "utf8")
  .trim()
  .split("\n")
  .filter(Boolean)
  .map(JSON.parse);
appendFileSync(log, JSON.stringify(payload) + "\n");
const type = payload.event.type;
if (mode === "error") process.exit(2);
if (mode === "invalid") {
  process.stdout.write('{"action":"unexpected"}');
} else if (mode === "timeout") {
  setInterval(() => {}, 10_000);
  await new Promise(() => {});
} else if (mode === "flood") {
  process.stdout.write("x".repeat(1_048_577));
} else {
  let result = {};
  if (mode === "block-input" && ["input", "beforeRequest"].includes(type))
    result = { action: "block", reason: "Cannot route" };
  else if (
    mode === "halt" &&
    ["onYield", "agent_before_settle", "afterTool", "tool_result"].includes(
      type,
    )
  )
    result = { action: "halt", reason: "Needs review" };
  else if (["input", "beforeRequest"].includes(type))
    result = {
      action: "inject_context",
      reason: "Follow the project checkpoint",
    };
  else if (
    mode === "steer-tools" &&
    ["beforeTool", "afterTool", "tool_call", "tool_result"].includes(type)
  )
    result = { action: "inject_context", reason: "Check the tests first" };
  else if (
    ["tool_call", "beforeTool"].includes(type) &&
    JSON.stringify(payload.event).includes("dangerous")
  )
    result = { action: "block", reason: "Denied operation" };
  else if (
    mode === "continue" &&
    ["onYield", "agent_before_settle"].includes(type)
  )
    result = {
      action: previous.some((p) => p.event.type === type) ? "halt" : "block",
      reason: "Finish the tests",
    };
  process.stdout.write(JSON.stringify(result));
}

import { randomUUID } from "node:crypto";
import type {
  ExtensionAPI,
  ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import {
  createTransport,
  reason,
  text,
  type BridgeOptions,
  type Envelope,
} from "./transport.js";

/** Register lifecycle handlers only; loading the factory starts no processes. */
export function createForemanPi(options: BridgeOptions = {}) {
  return (pi: ExtensionAPI) => {
    const send = createTransport("pi", options);
    let workId: string | undefined;
    let context: string | undefined;
    let suspended: string | undefined;
    const envelope = (
      ctx: ExtensionContext,
      event: Envelope["event"],
    ): Envelope => ({
      session_id: ctx.sessionManager.getSessionId(),
      cwd: ctx.cwd,
      work_id: workId,
      event,
    });
    const notify = (ctx: ExtensionContext, message: string) => {
      if (ctx.hasUI) ctx.ui.notify(message, "error");
      else console.error(message);
    };
    pi.on("session_start", async (event, ctx) => {
      workId = undefined;
      context = undefined;
      suspended = undefined;
      try {
        await send(envelope(ctx, { type: event.type }));
      } catch (error) {
        suspended = String(error);
        notify(ctx, suspended);
      }
    });
    // `input` can consume a refused prompt. before_agent_start cannot cancel,
    // and Pi reports exceptions from that handler but still starts the agent.
    pi.on("input", async (event, ctx) => {
      workId = randomUUID();
      context = undefined;
      try {
        const outcome = await send(
          envelope(ctx, { type: event.type, text: event.text }),
          ctx.signal,
        );
        if (outcome.action === "block" || outcome.action === "halt") {
          suspended = reason(outcome);
          notify(ctx, suspended);
          return { action: "handled" };
        }
        suspended = undefined;
        if (outcome.action === "inject_context") context = reason(outcome);
      } catch (error) {
        suspended = String(error);
        notify(ctx, suspended);
        return { action: "handled" };
      }
    });
    pi.on("before_agent_start", async () =>
      context
        ? {
            message: {
              customType: "foreman",
              content: context,
              display: false,
            },
          }
        : undefined,
    );
    pi.on("tool_call", async (event, ctx) => {
      if (suspended) return { block: true, reason: suspended };
      try {
        const outcome = await send(envelope(ctx, { ...event }), ctx.signal);
        if (outcome.action === "block" || outcome.action === "halt") {
          return {
            block: true,
            reason: reason(outcome),
            terminate: outcome.action === "halt",
          };
        }
        if (outcome.action === "inject_context") {
          pi.sendMessage(
            { customType: "foreman", content: reason(outcome), display: false },
            { deliverAs: "steer" },
          );
        }
      } catch (error) {
        return { block: true, reason: String(error) };
      }
    });
    pi.on("tool_result", async (event, ctx) => {
      try {
        const outcome = await send(envelope(ctx, { ...event }), ctx.signal);
        if (outcome.action === "inject_context") {
          return {
            content: [
              ...event.content,
              { type: "text", text: reason(outcome) },
            ],
            structuredContent: event.structuredContent,
          };
        }
        if (outcome.action !== "block" && outcome.action !== "halt") return;
        suspended = reason(outcome);
      } catch (error) {
        suspended = String(error);
      }
      ctx.abort();
      notify(ctx, suspended);
      return {
        content: [...event.content, { type: "text", text: suspended }],
        isError: true,
      };
    });
    pi.on("agent_before_settle", async (event, ctx) => {
      if (suspended || event.outcome !== "completed") return;
      const messages = event.context.contextMessages;
      const last = [...messages]
        .reverse()
        .find((message) => message.role === "assistant");
      try {
        const outcome = await send(
          envelope(ctx, {
            type: event.type,
            last_assistant_message: text(last?.content),
          }),
          ctx.signal,
        );
        if (outcome.action === "block" || outcome.action === "inject_context") {
          return {
            entries: [
              {
                type: "custom_message",
                customType: "foreman",
                content: reason(outcome),
                display: false,
              },
            ],
            continue: true,
          };
        }
        if (outcome.action === "halt") {
          suspended = reason(outcome);
          notify(ctx, suspended);
          return {
            entries: [
              {
                type: "custom_message",
                customType: "foreman",
                content: suspended,
                display: true,
              },
            ],
          };
        }
      } catch (error) {
        suspended = String(error);
        notify(ctx, suspended);
        ctx.abort();
      }
    });
    pi.on("session_shutdown", async (event, ctx) => {
      try {
        await send(envelope(ctx, { type: event.type }));
      } catch (error) {
        notify(ctx, String(error));
      }
    });
  };
}

export default createForemanPi();

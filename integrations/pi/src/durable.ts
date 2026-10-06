import {
  AgentDoc,
  defineDoc,
  defineExtension,
  GenerationTask,
  hook,
  LiveDoc,
  ToolTask,
  type Harness,
  type HookApi,
} from "@earendil-works/pi-durable";
import type { Context } from "@earendil-works/chord";
import {
  awaitWithContext,
  withoutAbortSignal,
} from "@earendil-works/chord/context";
import {
  createTransport,
  reason,
  text,
  type BridgeOptions,
  type Envelope,
} from "./transport.js";

export type DurableOptions = BridgeOptions & {
  /** Stable, unique identity of the storage, reused after reopening it. */
  sessionId: string;
  /** Lazy accessor: registry installation happens before Harness.open(). */
  harness: () => Harness;
  /** Fallback when the conversation has no configured cwd. */
  cwd: string;
};

const Control = defineDoc<{ workId: string; halt: string }>({
  kind: "foreman.control",
  version: 1,
  scope: "conversation",
  history: "latest",
  fork: "initial",
  initial: () => ({ workId: "", halt: "" }),
});

/** Native task hooks, selected with the extension name `foreman`. */
export function createForemanDurable(options: DurableOptions) {
  if (!options.sessionId.trim())
    throw new Error("A stable durable sessionId is required");
  const send = createTransport("pi-durable", options);
  const halt = async (
    api: HookApi,
    context: Context,
    error: unknown,
  ): Promise<never> => {
    // Ordinary hook throws are reported and swallowed by Pi. abortTask() commits
    // the mark and then joins this invocation. Wait only until this invocation's
    // context is cancelled; awaiting the join itself here would wait on ourselves.
    const abort = options
      .harness()
      .abortTask(api.taskId, withoutAbortSignal(context));
    try {
      await awaitWithContext(abort, context);
    } catch (abortError) {
      if (!context.abortSignal?.aborted) throw abortError;
    }
    console.error(`Foreman halted durable work: ${String(error)}`);
    throw error;
  };
  const envelope = async (
    api: HookApi,
    context: Context,
    event: Envelope["event"],
  ): Promise<Envelope> => {
    const agent = await api.snapshot(AgentDoc, api.conversationId, context);
    return {
      session_id: JSON.stringify([options.sessionId, api.conversationId]),
      cwd: agent?.cwd ?? options.cwd,
      ...(event.type === "onYield"
        ? { event_id: `onYield:${api.taskId}` }
        : {}),
      event,
    };
  };
  return defineExtension({
    name: "foreman",
    hooks: [
      hook(GenerationTask, {
        beforeRequest: async (request, api, context) => {
          try {
            // Run input IDs survive retries, tool rounds, and onYield handovers.
            // Real queued user input changes them. Continuation messages have no
            // submission IDs and must never replace the original supervised job.
            const live = await api.snapshot(
              LiveDoc,
              api.conversationId,
              context,
            );
            if (!live?.run?.inputs.length)
              throw new Error("Foreman cannot identify durable run input");
            const harness = options.harness();
            const workId = JSON.stringify(live.run.inputs);
            const control = await api.snapshot(
              Control,
              api.conversationId,
              context,
            );
            if (control?.workId === workId && control.halt)
              throw new Error(control.halt);
            if (control?.workId !== workId)
              await harness.commit(async (tx) => {
                const record = await tx.doc(Control, api.conversationId);
                record.workId = workId;
                record.halt = "";
              }, context);
            const conversation = await harness.conversation(
              api.conversationId,
              context,
            );
            if (!conversation)
              throw new Error("Foreman cannot read durable conversation");
            const prompts: string[] = [];
            for (const id of live.run.inputs) {
              const submission = await harness.submission(id, context);
              const status = await submission?.status(context);
              if (!status?.entry)
                throw new Error("Foreman cannot read durable submitted work");
              const page = await conversation.entries(
                { minEntryId: status.entry, maxEntryId: status.entry },
                1,
                undefined,
                context,
              );
              const entry = page.items[0];
              const prompt = entry?.model
                ?.filter((message) => message.role === "user")
                .map((message) => text(message.content))
                .join("\n");
              if (!prompt?.trim())
                throw new Error("Foreman requires a text work prompt");
              prompts.push(prompt);
            }
            const input = await envelope(api, context, {
              type: "beforeRequest",
              prompt: prompts.join("\n\n"),
            });
            input.work_id = workId;
            const outcome = await send(input, context.abortSignal);
            if (outcome.action === "block" || outcome.action === "halt")
              throw new Error(reason(outcome));
            if (outcome.action === "inject_context")
              return {
                messages: [
                  ...request.messages,
                  {
                    role: "user",
                    content: `[Foreman]\n${reason(outcome)}`,
                    timestamp: Date.now(),
                  },
                ],
              };
          } catch (error) {
            return halt(api, context, error);
          }
        },
        onYield: async (answer, api, context) => {
          try {
            // Memo the effect before returning it; recovery reuses this task's
            // decision instead of consuming the continuation allowance twice.
            let outcome = await api.memo("foreman.yield", context);
            if (outcome === undefined) {
              outcome = await api.memo(
                "foreman.yield",
                (await send(
                  await envelope(api, context, {
                    type: "onYield",
                    last_assistant_message: text(answer.content),
                  }),
                  context.abortSignal,
                )) as Record<string, string>,
                context,
              );
            }
            const result = outcome as Awaited<ReturnType<typeof send>>;
            if (result.action === "halt")
              return halt(api, context, new Error(reason(result)));
            if (result.action === "block" || result.action === "inject_context")
              return { continue: reason(result) };
          } catch (error) {
            return halt(api, context, error);
          }
        },
        afterTools: async (_assistant, _results, api, context) => {
          try {
            const control = await api.snapshot(
              Control,
              api.conversationId,
              context,
            );
            if (control?.halt)
              return halt(api, context, new Error(control.halt));
          } catch (error) {
            return halt(api, context, error);
          }
        },
      }),
      hook(ToolTask, {
        beforeTool: async (call, api, context) => {
          const control = await api.snapshot(
            Control,
            api.conversationId,
            context,
          );
          if (control?.halt) return { block: control.halt };
          const outcome = await send(
            await envelope(api, context, { type: "beforeTool", call }),
            context.abortSignal,
          );
          // beforeTool only supports blocking or argument replacement. Steering
          // therefore blocks this call with feedback instead of being discarded.
          if (outcome.action && outcome.action !== "allow")
            return { block: reason(outcome) };
        },
        afterTool: async (call, result, api, context) => {
          let outcome;
          try {
            outcome = await send(
              await envelope(api, context, { type: "afterTool", call, result }),
              context.abortSignal,
            );
          } catch (error) {
            outcome = { action: "halt" as const, reason: String(error) };
          }
          if (!outcome.action || outcome.action === "allow") return;
          if (outcome.action !== "inject_context")
            await options.harness().commit(async (tx) => {
              (await tx.doc(Control, api.conversationId)).halt =
                reason(outcome);
            }, context);
          return {
            ...result,
            content: [
              ...(result.content ?? []),
              { type: "text", text: reason(outcome) },
            ],
            ...(outcome.action === "inject_context"
              ? {}
              : {
                  isError: true,
                  control: { ...result.control, terminate: true },
                }),
          };
        },
      }),
    ],
  });
}

# Foreman for Pi and Pi Durable

These bridges forward Pi lifecycle events and Pi Durable task hooks to the same
`foreman hook` supervisor used by Codex. Foreman owns routing, evidence, decisions,
request budgets, and attached-session storage. The bridge never makes a model call.

Tested against `@earendil-works/pi-coding-agent` and
`@earendil-works/pi-durable` **1.0.4**, with Node **22.19+**. Pi Durable's API is
experimental; the peer range is limited to 1.0.x. This integration is a local
package in the repository, not a published npm package. The former
`@mariozechner` packages are not part of this tested contract.

## Build and configure

Until this PR is released, install the Foreman core from this checkout:

```bash
python3 -m pip install -e /absolute/path/to/foreman
cd /absolute/path/to/foreman/integrations/pi
npm ci
npm run build
```

Put `TYPESAFE_API_KEY` in the environment or `~/.foreman/.env`. Existing Foreman
responsibilities, repository scope, extensions, and request budgets apply to both
clients; see [the hook guide](../../docs/hooks.md). The default bridge command is
`foreman hook --client pi` or `foreman hook --client pi-durable`, found on `PATH`.

## Pi coding agent

Load the built extension:

```bash
pi -e /absolute/path/to/foreman/integrations/pi/dist/pi.js
```

For persistent loading, add that absolute path to `extensions` in Pi's
`~/.pi/agent/settings.json`. Keep the adjacent `transport.js` in place.
For a custom executable, create a Pi extension that exports the factory result:

```js
import { createForemanPi } from '/absolute/path/to/foreman/integrations/pi/dist/pi.js';
export default createForemanPi({
  command: ['/absolute/path/to/foreman'],
  // dataDir: '/absolute/path/to/isolated-foreman-data',
  // timeoutMs: 120000,
});
```

The extension handles session start/shutdown, prompt input, tool calls/results,
and `agent_before_settle`. Routing happens at `input`, which can consume a refused
prompt before the agent starts; `before_agent_start` supplies the returned
responsibility context. Pi's ordinary extension exceptions are notification-only,
so a routing failure consumes the input, and a tool failure returns a denial.

A completion block appends model-visible Foreman feedback and requests one
continuation. Foreman persists that allowance; another incomplete finish halts.
Post-tool blocking or halt preserves the result, adds feedback, and aborts the
agent. Aborted/error runs are not automatically continued. Manual user commands
such as `!bash` are outside agent tool supervision. Extensions that transform
input after this handler may change the eventual prompt; register Foreman after
input transformers when using them.

## Pi Durable

Install the extension in the host's registry before opening the harness:

```js
import { createRegistry, Harness } from '@earendil-works/pi-durable';
import { createForemanDurable } from '/absolute/path/to/foreman/integrations/pi/dist/durable.js';

let harness;
const registry = createRegistry();
// Install your tools and other extensions here.
registry.install(createForemanDurable({
  sessionId: '/absolute/path/to/my-pi-storage',
  cwd: '/absolute/path/to/my-repository',
  harness: () => harness,
}));
harness = await Harness.open(storage, { models, registry }, context);
```

`storage`, `models`, and `context` are the host's normal durable setup. The lazy
`harness` accessor resolves after opening, before any work starts. `sessionId`
must uniquely identify the storage and stay the same when reopening it. A
conversation ID is only unique within one storage, so the bridge combines both.
A conversation's configured `cwd` takes precedence over the fallback above.
The extension is selected by default; when explicitly selecting extensions,
include `foreman` in the conversation's selection.

The native hooks are `GenerationTask.beforeRequest`, `GenerationTask.onYield`,
`GenerationTask.afterTools`, `ToolTask.beforeTool`, and `ToolTask.afterTool`. `beforeRequest` reads the run's
actual submitted entries through the harness, rather than routing the transcript
or a synthetic continuation prompt. Stable submission IDs keep routing and the
one-continuation allowance intact through retries and generation handovers. New
user submissions route new work. Text prompts are required; image-only submissions
are refused because Foreman has no text job to assess.

Pi Durable's `beforeTool` supports blocking and argument replacement, but no
context injection. Foreman steering at this boundary therefore blocks the
proposed tool with feedback for the model to act on. After a tool, steering is
appended to its result; blocking or halt also marks an error and requests
termination through the native result control. A saved conversation control
record also halts the generation at `afterTools`, including mixed parallel
batches where one result's terminate flag would otherwise be ignored. As with Pi, a tool already run
cannot be undone, and other calls in an existing parallel batch may already be
running. For strict sequential boundaries set `toolExecution: 'sequential'` in
harness settings.

Pi Durable reports and swallows ordinary generation-hook exceptions. The bridge
uses the public `abortTask` API and waits for invocation cancellation, rather
than joining its own invocation, before propagating a refused request or halt.
Completion decisions use both a task memo and Foreman's replay record, covering
the gap between the external decision and Pi's memo commit. This does not make
external model calls or tool executions exactly once. Foreman records expire
under its normal session TTL; reopening after expiry starts fresh supervision.
There is no shutdown task hook, so durable Foreman sessions expire naturally.

## Process contract and tests

Bridge input is one JSON envelope on stdin:

```json
{"session_id":"native identity","cwd":"/workspace","work_id":"stable work identity","event":{"type":"input","text":"Fix the tests"}}
```

The Pi Durable adapter instead accepts `beforeRequest` with `prompt`, tool hooks
with a native `call` (`id`, `name`, `arguments`), `afterTool` with `result`, and
`onYield` with `last_assistant_message`. `work_id` deduplicates routing;
`event_id` identifies replayable completion calls. Reusing either identity with
changed input fails. Successful output is `{}` or a semantic action object:
`{"action":"inject_context|block|halt","reason":"..."}`. The bridge applies it
through the client's native API; this JSON is not a native Pi hook response.

Commands are executed without a shell. Full arguments and results go over stdin.
Missing executables, nonzero status, invalid/oversized JSON, cancellation, and
timeouts prevent supervised operations. Diagnostics are drained rather than
retained in the bridge; use Foreman's saved state for assessment failure details.

```bash
npm test
# From the Foreman repository root:
PYTHONPATH=src .venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Tests use published Pi packages, their offline faux provider, isolated temporary
storage, and a fixture hook process. They do not need provider credentials or
alter the user's installed integrations.

Upstream contracts: [Pi extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md),
[Pi Durable hooks](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md#hooks).
The source inspected for implementation is
[`ae92585`](https://github.com/earendil-works/pi/tree/ae92585d3b3e5f1e4b123d14a34314d826d8d9f5);
type checks and runtime tests use the published 1.0.4 packages in the lockfile.

# Deep Agents Code integration

Foreman core supports both headless workers and native hooks for LangChain's
[Deep Agents Code](https://github.com/langchain-ai/deepagents/tree/main/libs/code)
(`dcode`). The CLI supplies the agent harness; Foreman owns responsibility routing,
evidence collection, interventions, verification, and completion checks. Foreman core
has no LangChain SDK dependency. Native hooks can be installed through the
`foreman-deepagents` marketplace plugin or the core setup command.

## Requirements and installation

- `foreman-core >= 0.5.0` needs Python 3.11+ and includes
  `foreman deepagents setup` and the `deepagents` worker backend.
- `deepagents-code >= 0.1.83` needs Python 3.12+. The CLI flags and Hooks v2 contract
  were checked against the published 0.1.83 package; older versions are unsupported.
  Foreman's automated tests use offline subprocesses and protocol fixtures. Both
  integration paths were also exercised with live model calls against a sample
  repository; see the [live test results](live-test-results.md).
- A TypeSafe API key for Foreman, plus credentials for the worker's selected model provider.

Install the commands in separate tool environments, for example with `uv`:

```bash
uv tool install 'foreman-core>=0.5.0'
uv tool install --python 3.12 'deepagents-code>=0.1.83'
foreman --version
foreman deepagents setup --help
dcode --version
```

For development or before 0.5.0 is published, run `uv tool install --force .`
from this Foreman checkout instead of installing the published core package.

Configure the model provider following the
[Deep Agents documentation](https://docs.langchain.com/oss/python/deepagents/overview).
For example, export `OPENAI_API_KEY` for an OpenAI model or `ANTHROPIC_API_KEY` for
an Anthropic model. Foreman inherits these variables into the worker, while removing
its own `TYPESAFE_*` variables. Do not put supervisor credentials in the repository.

Export `TYPESAFE_API_KEY` in the terminal running `foreman run`. For native hooks,
use Foreman's protected central `${FOREMAN_DATA_DIR:-~/.foreman}/.env`.
Native hook environments may remove credential variables, so do not rely on
`TYPESAFE_API_KEY` being inherited from dcode:

```bash
mkdir -p ~/.foreman
chmod 700 ~/.foreman
touch ~/.foreman/.env
chmod 600 ~/.foreman/.env
```

Edit that file to add `TYPESAFE_API_KEY=your-key`. Existing process variables take
precedence when available. The `foreman run` command requires the exported key.

## Foreman-launched jobs

Select the backend and optionally the model, then run a job:

```bash
export FOREMAN_WORKER_BACKEND=deepagents
export FOREMAN_DEEPAGENTS_MODEL=openai:your-model
foreman run --repo /absolute/path/to/project --job "Add request retries and verify them"
```

Foreman runs `dcode --non-interactive <mission>` in the repository with a turn limit,
a wall-clock timeout, and a shell allow-list. Both output pipes are streamed in bounded
chunks, including output without newlines. No hook setup is necessary for this path.
Native user and enabled plugin hooks already installed in `dcode` still run; project
hooks are not automatically trusted. Use a profile without Foreman hooks, or disable
`foreman-deepagents@thruwire`, for Foreman-owned jobs to avoid duplicate assessments.

| Variable | Default | Meaning |
| --- | --- | --- |
| `FOREMAN_DEEPAGENTS_EXECUTABLE` | `dcode` | Executable name or absolute path |
| `FOREMAN_DEEPAGENTS_MODEL` | unset | Provider/model identifier; otherwise use dcode configuration |
| `FOREMAN_DEEPAGENTS_MAX_TURNS` | `200` | Headless agentic turn limit |
| `FOREMAN_DEEPAGENTS_SHELL_ALLOW_LIST` | `recommended` | Native shell allow-list: preset, comma-separated commands, or `all` |
| `FOREMAN_WORKER_TIMEOUT_SECONDS` | `3600` | Foreman's deadline, also passed to dcode rounded up to whole seconds |

The recommended shell preset may exclude commands needed by your project. Set an
explicit list (for example `git,python,pytest,uv`) when appropriate. `all` allows arbitrary
shell commands on the host; this worker does not configure a sandbox. The CLI's native
managed configuration may further restrict execution.

Headless mode has no live input channel: Foreman uses stop/retry for steering. Native
exit code 124 means a timeout or turn budget was exhausted. A zero exit is a worker
result; Foreman still decides whether the job meets its completion and verification
requirements. On POSIX, shutdown collects descendant process groups so the separate
LangGraph server is stopped as well. This collection requires the system `ps` command.

## Interactive sessions with native hooks

First configure Foreman's central credentials and responsibilities. Packaged responsibilities
are enabled by default. To restrict supervision to selected repositories, merge a `[hooks]`
section into `${FOREMAN_CONFIG:-~/.foreman/config.toml}`:

```toml
[hooks]
repositories = ["/absolute/path/to/project"]
```

Repository identities include subdirectories and linked Git worktrees. Omit `repositories`
for global supervision or use `[]` to disable it. Central responsibility overrides,
custom criteria, and extensions work just as with Codex and Pi; see
[responsibility configuration](../../docs/hooks.md#responsibility-configuration).

Choose one of the following hook installation methods. Both use the same core
adapter and responsibilities. Registering both makes every matching handler execute,
causing duplicate assessments.

### Marketplace plugin

The [Foreman Deep Agents plugin](https://github.com/thruwire/marketplace/tree/main/plugins/foreman-deepagents)
packages the seven lifecycle handlers and a setup/diagnostic skill. The existing
`foreman@thruwire` plugin targets Codex; use `foreman-deepagents@thruwire` with dcode.
The plugin requires the separately installed runtime versions listed above and
`foreman` on the PATH used to launch dcode.

After configuring central credentials, repository scope, and responsibilities,
review the plugin's hooks and launcher, then install:

```bash
dcode plugin marketplace add thruwire/marketplace
dcode plugin install foreman-deepagents@thruwire
dcode plugin list --json
```

If the marketplace is already registered, re-run `dcode plugin marketplace add thruwire/marketplace`
to refresh its catalog before installing; this works with CLI 0.1.83. Start a fresh
dcode session in your repository and submit your task.
Installing and enabling the plugin authorizes its hooks; workspace trust governs
project hooks separately. `/reload` refreshes plugin discovery, but a fresh session
ensures Foreman receives startup and the original prompt.

If you previously ran `foreman deepagents setup`, back up the hook files, then remove
only Foreman's handlers invoking `hook --client deepagents` from `~/.deepagents/hooks.json`
and any project `.deepagents/hooks.json`. Preserve other handlers and settings. Plugin
installation does not modify these files or remove duplicate registrations for you.

To disable or remove the plugin:

```bash
dcode plugin disable foreman-deepagents@thruwire
# Or:
dcode plugin uninstall foreman-deepagents@thruwire
```

Start a fresh session afterward. This does not remove handlers installed separately
by setup.

### Direct hook setup

Use this method when you prefer native hook configuration without a plugin:

```bash
foreman deepagents setup
```

This merges seven wildcard event handlers into `~/.deepagents/hooks.json`, preserving
existing handlers and other settings. Repeating the same setup does not duplicate entries.
Writes are atomic. The command refuses malformed or legacy list-shaped configurations;
convert those to Hooks v2 using the native hook documentation before retrying.
It resolves the installed Foreman executable
to an absolute path when available, which avoids dependence on the coding agent's PATH.

To select another hook file or executable:

```bash
foreman deepagents setup \
  --hooks-file /absolute/path/to/hooks.json \
  --foreman-executable /absolute/path/to/foreman
```

For project-scoped configuration, select `/project/.deepagents/hooks.json`. Interactive
`dcode` requires workspace trust before loading project hooks; headless runs require
the native `--trust-project-hooks` opt-in. Foreman's worker deliberately does not add it.
User hooks load without project trust. Setup configures hooks only; it does not install
the CLI, change credentials, or rewrite Foreman's responsibilities.

Start a fresh `dcode` session in the configured repository and submit your task. Handlers
invoke `foreman hook --client deepagents` directly through native command hooks.

| Native event | Foreman boundary |
| --- | --- |
| `SessionStart` | Create or refresh attached-session state |
| `UserPromptSubmit` | Route work; native `prompt_id` identifies repeat submissions |
| `PreToolUse` | Assess proposed tool call; deny or inject guidance |
| `PostToolUse` | Record result and assess progress |
| `PostToolUseFailure` | Record error/interruption and assess progress |
| `Stop` | Check completion; request one continuation or finish/halt |
| `SessionEnd` | Remove attached-session state |

Native `stop_hook_active` and Foreman's saved continuation allowance prevent repeated
completion requests. Tool events emitted inside subagents contribute to the parent
session's evidence; `SubagentStop` is not treated as parent completion. Native failures
are normalized with `isError` and the error text.

The native response contract has limits: pre-tool denial blocks the proposed operation,
while post-tool block/stop requests become feedback in the model's next context, rather
than immediately aborting the whole run. Prompt hooks can prevent work submission;
`Stop` controls continuation and settling. Hook errors with exit code 2 block pre-tool
operations or prompt submission; other nonzero errors and hook timeouts are diagnostics
in dcode and do not guarantee blocking. Each installed handler has a 120-second deadline.
See the [native hook contract](https://github.com/langchain-ai/deepagents/blob/main/libs/code/HOOKS.md).

## Verify setup and troubleshoot

- Check `dcode --help` includes `--non-interactive`, `--max-turns`, `--timeout`, and
  `--shell-allow-list`. Upgrade `deepagents-code` if these are absent.
- For the plugin, inspect `dcode plugin list --json` for an enabled
  `foreman-deepagents@thruwire`, and check that `foreman` is on dcode's PATH. Start a
  fresh session after installing. A missing user hook file is expected with this method.
- For direct setup, inspect `~/.deepagents/hooks.json`: the seven handlers should
  invoke your Foreman executable with `hook --client deepagents`. Repeat setup with
  the same executable to verify it reports `Already installed`.
- During a task in a fresh interactive session, look for a `client: "deepagents"`
  record under `${FOREMAN_DATA_DIR:-~/.foreman}/sessions/`. Session-end removes it.
  An excluded repository or disabled responsibility registry produces no session state.
  `jev-usage.jsonl` in the same central data directory records real supervision requests.
- A missing executable needs PATH repair or `FOREMAN_DEEPAGENTS_EXECUTABLE`; provider
  errors need worker credentials/model configuration. Missing supervisor credentials
  need the protected central `.env` or the launching process environment.
- `arrived before work_submitted` means tools ran without the initial prompt hook.
  Install all seven events and start a fresh session. Unsupported events fail explicitly.
- Repeated shell denials need a suitable allow-list. Exit 124 needs a larger turn/time
  budget or a narrower task. Neither is reported as successful worker completion.
- Existing project/user hook decisions may take precedence over Foreman's responses.
  Native matching handlers all execute; avoid duplicate Foreman registration in multiple
  scopes. Remove only Foreman's handler entries to uninstall, preserving other hooks.

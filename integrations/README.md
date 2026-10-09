# Agent integrations

Foreman's assistant-neutral hook protocol lives in the core project. Installable assistant plugin
packages and publisher catalogs live in
[`thruwire/marketplace`](https://github.com/thruwire/marketplace), where their manifests and assets
are available to marketplace discovery before installation.

- The [Foreman Codex plugin](https://github.com/thruwire/marketplace/tree/main/plugins/foreman)
  invokes the core `foreman hook --client codex` protocol.
- [`deepagents/`](deepagents/) documents the core dcode worker and native hook adapter.
  The [Deep Agents plugin](https://github.com/thruwire/marketplace/tree/main/plugins/foreman-deepagents)
  installs native hooks through dcode's marketplace. Alternatively, `foreman deepagents setup`
  merges native hook configuration directly; no plugin is required for that path or the worker.
- [`pi/`](pi/) supplies buildable Pi extension and Pi Durable task-hook bridges, invoking
  `foreman hook --client pi` and `foreman hook --client pi-durable` respectively. This is a local
  integration package with installation documentation and offline tests. Pi supervision requires
  the TypeScript bridge in addition to `foreman-core`; the guide covers source installation and
  installation from the published `@thruwire/foreman-pi` npm package.
- [`claude/`](claude/) reserves the future Claude Code integration location. It is not currently an
  installable plugin.

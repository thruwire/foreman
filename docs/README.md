# Foreman documentation

These notes explain Foreman's design and implementation:

- [Theory: semantic supervision](theory.md) describes the two-loop architecture.
- [Why Jev fits](why-jev.md) maps Jev's actual primitives to Foreman's needs.
- [What Foreman is proving](what-foreman-proves.md) states the hypotheses and evidence boundaries.
- [Runtime and event flow](runtime.md) is the implementation guide.
- [Evidence providers](evidence.md) defines per-check evidence selection and CLI providers.
- [Responsibility configuration and routing](routing.md) explains central definitions and matching.
- [Extensions](extensions.md) defines registration, lifecycle, snapshots, and hierarchical routing.
- [Coding-assistant hooks](hooks.md) describes the adapter-neutral attached-worker protocol.
- [Worker backends](workers.md) describes owned coding-agent transports.
- [Deep Agents setup](../integrations/deepagents/README.md) covers dcode workers and native hooks.
- [Live steering](steering.md) explains how Jev guidance reaches an active Codex turn.
- [Releasing](releasing.md) documents the build and trusted-publishing process.

The root [README](../README.md) remains the installation and command reference.

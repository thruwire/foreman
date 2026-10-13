# Claude Code integration placeholder

This directory reserves the product-owned location for a future Foreman integration with Claude
Code. It intentionally contains no plugin manifest, hooks, or executable files and is not
installable.

When implemented, keep Claude-specific protocol handling here and add its catalog entry to
`thruwire/marketplace/.claude-plugin/marketplace.json`.

## Decision-model provider

These setup examples use Jev, the default decision model. OpenAI supervision uses the same
hooks and responsibilities. Install `foreman-core[openai]` and set
`FOREMAN_DECISION_PROVIDER=openai`, `FOREMAN_DECISION_MODEL=gpt-6-luna`, and
`FOREMAN_OPENAI_API_KEY` in the protected central `.env`. See
[decision-model configuration](../../docs/decision-models.md).

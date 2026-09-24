# Extensions

Foreman extensions add installed responsibility implementations and hierarchical routing without
putting service-specific code in Foreman. An extension is trusted local code installed separately
from Foreman. Synchronized snapshots are declarative data; they cannot import Python or introduce
new executable implementations.

## Registration and enablement

An installed Python distribution registers an extension entry point whose name exactly matches its
manifest ID:

```toml
[project.entry-points."foreman.extensions"]
example = "example_foreman:extension"
```

Foreman only imports extensions present in its central configuration. Presence enables the
extension; there is no separate enable command or boolean:

```toml
[extensions."example"]
```

The default configuration path is:

```text
${FOREMAN_CONFIG:-${FOREMAN_DATA_DIR:-~/.foreman}/config.toml}
```

An installed but unconfigured extension is ignored and is not imported. A configured extension
that is not installed fails closed with an actionable error. Extension and responsibility IDs are
namespaced, and collisions or attempts to route another extension's responsibilities are rejected.

## Extension contract

Every extension supplies a manifest and a local activation method:

```python
class ForemanExtension(Protocol):
    manifest: ExtensionManifest

    def activate(
        self,
        context: ExtensionActivationContext,
        snapshot: ExtensionSnapshot | None,
    ) -> ExtensionContribution: ...

    async def close(self) -> None: ...
```

`ExtensionContribution` contains registered responsibility implementations, their existing
`ResponsibilityFileConfig` definitions, and an optional `RouteGroup` root. Installed code is still
required for every responsibility; snapshot content only configures that code.

Authentication and synchronization are optional methods discovered on the extension:

```python
async def login(context: ExtensionLoginContext) -> ExtensionIdentity: ...
async def logout(context: ExtensionLoginContext) -> None: ...
async def sync(context: ExtensionSyncContext) -> ExtensionSnapshot: ...
```

The contexts expose extension-owned settings and a credential-store interface backed by the
operating system keyring. Tokens do not enter TOML, snapshots, routing bindings, repository state,
or hook payloads.

## Lifecycle

| Stage | Trigger | Network permitted |
|---|---|---:|
| Discover | Foreman process starts | No |
| Enable | Extension appears in global config | No |
| Activate | Every run or hook process | No |
| Login | `foreman extension login ID` | Yes |
| Sync | Login or `foreman extension sync ID` | Yes |
| Route and assess | Incoming work and coding-assistant hooks | No |
| Close | Process exits | No |

A successful login immediately synchronizes. Other lifecycle commands are:

```bash
foreman extension status [ID]
foreman extension sync ID
foreman extension logout ID
```

Logout removes the cached snapshot after the extension removes its credentials.

## Snapshots

Snapshots are versioned, bounded JSON records stored at:

```text
${FOREMAN_DATA_DIR:-~/.foreman}/extensions/<extension-id>/snapshot.json
```

They are atomically replaced and owner-readable where supported. Identity, schema version, size,
and expiration are validated before activation. Synchronization failure leaves the previous valid
snapshot intact. Missing, malformed, incompatible, or expired required snapshots fail closed and
tell the user to run the explicit synchronization command.

Most importantly, neither `foreman run` nor `foreman hook` calls `sync()`. They only read the
current local snapshot and call the non-networked `activate()` method. Refresh scheduling can be
added at a host or plugin startup boundary later without adding network latency to each hook.

## Hierarchical routing

An extension can materialize route groups from its cached snapshot:

```text
extension root
└── cached project candidates
    ├── project A
    │   └── responsibilities
    └── project B
        └── responsibilities
```

Sibling groups use `all_matches` by default, preserving Foreman's multiple-responsibility routing.
A parent can choose `best_match` for mutually exclusive children such as projects. Matching groups
contribute bounded, JSON-safe bindings such as a project ID. Bindings, the route trace, active
extension IDs, and snapshot revisions are persisted in `FactoryState` and included in Foreman's
observation. Credentials must never be included in bindings.

Managed runs emit `EXTENSIONS_ACTIVATED` before routing or starting a worker. Attached sessions pin
their active extension IDs and snapshot revisions when a prompt is routed. If those change between
hook invocations, Foreman requires the work prompt to be submitted again rather than assessing
against a different responsibility set.

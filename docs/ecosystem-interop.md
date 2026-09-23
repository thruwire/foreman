# System 1.5 Ecosystem & Complementary Tools

In recent developments around TypeSafe AI's Jev System One paradigm, clear, specialized roles have emerged across the broader **System 1.5 architecture** (the deterministic executive decision layer sitting between System 1 fast perception and System 2 generative deliberation).

While **Foreman** is the definitive supervisor for coding worker runtimes (`Codex` / `OpenCode`), other non-competing, complementary open-source tools have matured to solve adjacent bottlenecks in the autonomous agentic workflow:

| Role in the Agent Loop | Primary Tool | Specialty |
| :--- | :--- | :--- |
| **Worker Runtime Supervision** | **Foreman** (this repository) | End-to-end execution, worker steering (`steer`/`stop`/`retry`/`finish`), turn supervision. |
| **Code Quality & Test Gate** | [**jev-harness**](https://github.com/ismaelsoilet/jev-harness) | Deterministic test-failure triage (< 500µs local / 70-300ms Jev), shell recovery (`pip`/`npm`/`cargo`), doom-loop abort circuit breaker, zero-dependency offline fallback. |
| **Tool-Call Guardrail** | [**jev-guard**](https://github.com/leepokai/jev-guard) | Policy-based tool interceptor (`deny`/`ask`/`allow`). |
| **Capability Routing** | [**JevRouter**](https://github.com/BillionsBobby/JevRouter) | Routing model and capability tiers. |

## How Foreman and Jev Harness Compose Naturally

1. **Foreman Directs, Jev Harness Triages:**
   When a worker executes tests, `jev-harness test-gate` can classify raw failure output locally (< 500µs) or via Jev (70-300ms). If `skip_llm = true` (e.g. missing dependency or transient port bind), the factory can execute an automated recovery step without burning supervisor or worker LLM iterations.
2. **Double-Veto Safety:**
   Foreman's worker-health assessment (`worker_stuck`, `work_off_track`) can be informed by `jev-harness abort-check` receipts without relinquishing Foreman's authority over the execution loop.
3. **Zero-Dependency Tri-Runtime Parity:**
   `jev-harness` provides native typed libraries across Python, TypeScript, and Rust, making it straightforward to invoke within Python-based Foreman or TypeScript-based pipelines.

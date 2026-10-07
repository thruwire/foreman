# Use Jev through OpenRouter

Foreman can use an OpenRouter API key through its existing TypeSafe SDK integration. No separate
TypeSafe account, proxy, or Foreman backend is required. Both responsibility routing and assessment
use the SDK's `TYPESAFE_API_KEY` and `TYPESAFE_BASE_URL` environment variables.

[OpenRouter's TypeSafe SDK guide](https://openrouter.ai/docs/guides/community/typesafe-sdk) documents
the compatible System One endpoint. Set the base URL to `https://openrouter.ai/api`; the SDK appends
`/v1/systemone`. Do not include `/v1` or the full endpoint in the base URL, and do not use the chat
completions endpoint for Jev.

Foreman sends `jev-latest` by default. OpenRouter maps that name to its `~typesafe/jev-latest` alias
and bills the request to the OpenRouter account. The returned model identifies the version that
served the request.

## Managed runs

If `OPENROUTER_API_KEY` is already present in your environment, provide the SDK configuration for
one invocation:

```bash
TYPESAFE_BASE_URL=https://openrouter.ai/api \
TYPESAFE_API_KEY="$OPENROUTER_API_KEY" \
foreman run --repo ./my-project --job "Implement the requested change and run its tests."
```

The SDK does not read `OPENROUTER_API_KEY` directly. `TYPESAFE_API_KEY` is the SDK's variable name;
the key belongs to the provider selected by `TYPESAFE_BASE_URL`. Always set the matching key and URL
together.

Alternatively, edit the `.env` loaded by `foreman run` with a text editor:

```dotenv
TYPESAFE_API_KEY=your-openrouter-key
TYPESAFE_BASE_URL=https://openrouter.ai/api
```

Keep credentials out of Git, shell history, logs, and command output. Existing environment values
take precedence over `.env` values, so remove stale TypeSafe settings from the launching process
when switching providers.

## Attached hooks

Foreman 0.4.2 and newer also load `${FOREMAN_DATA_DIR:-~/.foreman}/.env` for attached hooks. Save
the same two variables there in a file readable only by its owner. No plugin launcher changes or
duplicate hook registrations are needed.

For Foreman 0.4.1, supply both variables in the process environment that launches the coding
assistant. A desktop app may need restarting to inherit changed environment variables.

Continue with the normal [hook setup](hooks.md) and your assistant's plugin instructions. Provider
configuration does not install, trust, or enable lifecycle hooks. Start a fresh assistant session
after activating the hooks so Foreman receives the initial work prompt.

## Verify the connection

With `OPENROUTER_API_KEY` already set, run this small SDK probe in the Python environment where
Foreman is installed. It makes one billable Jev request with synthetic evidence, without starting
a coding worker or changing a repository:

```bash
TYPESAFE_BASE_URL=https://openrouter.ai/api \
TYPESAFE_API_KEY="$OPENROUTER_API_KEY" \
python - <<'PY'
import asyncio

from typesafe_sdk import AsyncTypeSafeClient, Noul, RetryPolicy


async def main():
    async with AsyncTypeSafeClient(
        model="jev-latest", retry=RetryPolicy(max_retries=0), timeout=15,
    ) as client:
        result = await client.system_one(
            state={"test_exit_code": 1, "test_summary": "FAILED (failures=1)"},
            questions={
                "tests_passed": Noul(instructions="Does this evidence show that the tests passed?"),
            },
        )
        print("model:", result.model)
        print("tests_passed:", result.nouls["tests_passed"].noul)
        print("input_tokens:", result.usage.input_tokens)
        print("output_tokens:", result.usage.output_tokens)


asyncio.run(main())
PY
```

A successful response verifies authentication, endpoint selection, and SDK response parsing. The
failed-test example should produce a low probability, but model scores are not deterministic test
assertions. This probe does not verify native hook activation or establish supervision accuracy.

`foreman extension status` only validates local configuration and extensions; it does not check
provider authentication. Do not use the SDK's model-listing call as an authentication probe here:
OpenRouter's Models API has a different response schema from TypeSafe's.

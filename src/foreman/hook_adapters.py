from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from foreman.hooks import (
    HookAction,
    HookError,
    HookEvent,
    HookEventKind,
    HookOutcome,
)


class HookAdapter(Protocol):
    """Translate one coding assistant's hook protocol at Foreman's boundary."""

    client: str

    def parse(self, payload: dict[str, Any]) -> HookEvent: ...

    def render(self, event: HookEvent, outcome: HookOutcome) -> dict[str, Any]: ...


class CodexHookEventName(StrEnum):
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"
    SESSION_END = "SessionEnd"


class CodexHookInput(BaseModel):
    """The stable subset of Codex lifecycle input consumed by its adapter."""

    model_config = ConfigDict(extra="allow")

    session_id: str = Field(min_length=1, max_length=1_000)
    cwd: str = Field(min_length=1)
    hook_event_name: CodexHookEventName
    turn_id: str | None = None
    prompt: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    tool_input: Any = None
    tool_response: Any = None
    stop_hook_active: bool = False
    last_assistant_message: str | None = None

    @model_validator(mode="after")
    def required_event_fields(self) -> CodexHookInput:
        if self.hook_event_name is CodexHookEventName.USER_PROMPT_SUBMIT and not (
            self.prompt and self.prompt.strip()
        ):
            raise ValueError("UserPromptSubmit requires a non-empty prompt")
        if self.hook_event_name in {
            CodexHookEventName.PRE_TOOL_USE,
            CodexHookEventName.POST_TOOL_USE,
        } and not self.tool_name:
            raise ValueError(f"{self.hook_event_name.value} requires tool_name")
        return self


_CODEX_EVENT_KINDS = {
    CodexHookEventName.SESSION_START: HookEventKind.SESSION_STARTED,
    CodexHookEventName.USER_PROMPT_SUBMIT: HookEventKind.WORK_SUBMITTED,
    CodexHookEventName.PRE_TOOL_USE: HookEventKind.BEFORE_TOOL,
    CodexHookEventName.POST_TOOL_USE: HookEventKind.AFTER_TOOL,
    CodexHookEventName.STOP: HookEventKind.WORKER_STOPPING,
    CodexHookEventName.SESSION_END: HookEventKind.SESSION_ENDED,
}


class CodexHookAdapter:
    client = "codex"

    def parse(self, payload: dict[str, Any]) -> HookEvent:
        try:
            source = CodexHookInput.model_validate(payload)
        except ValidationError as error:
            raise HookError(f"invalid Codex hook input: {error}") from error
        return HookEvent(
            client=self.client,
            session_id=source.session_id,
            cwd=source.cwd,
            kind=_CODEX_EVENT_KINDS[source.hook_event_name],
            source_event_name=source.hook_event_name.value,
            turn_id=source.turn_id,
            prompt=source.prompt,
            tool_name=source.tool_name,
            tool_use_id=source.tool_use_id,
            tool_input=source.tool_input,
            tool_response=source.tool_response,
            continuation_active=source.stop_hook_active,
            last_assistant_message=source.last_assistant_message,
        )

    def render(self, event: HookEvent, outcome: HookOutcome) -> dict[str, Any]:
        reason = outcome.reason or "Foreman blocked this operation"
        if outcome.action is HookAction.ALLOW:
            return {}
        if outcome.action is HookAction.INJECT_CONTEXT:
            return {
                "hookSpecificOutput": {
                    "hookEventName": event.source_event_name,
                    "additionalContext": reason,
                }
            }
        if outcome.action is HookAction.BLOCK:
            if event.kind is HookEventKind.BEFORE_TOOL:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": event.source_event_name,
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            return {"decision": "block", "reason": reason}
        return {
            "continue": False,
            "stopReason": reason,
            "systemMessage": outcome.system_message or reason,
        }


class PiHookInput(BaseModel):
    """Bridge envelope around Pi's native events; identities come from its host."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=1_000)
    cwd: str = Field(min_length=1)
    event: dict[str, Any]
    work_id: str | None = Field(default=None, min_length=1, max_length=1_000)
    event_id: str | None = Field(default=None, min_length=1, max_length=1_000)
    continuation_active: bool = False


_PI_EVENT_KINDS = {
    "session_start": HookEventKind.SESSION_STARTED,
    "input": HookEventKind.WORK_SUBMITTED,
    "tool_call": HookEventKind.BEFORE_TOOL,
    "tool_result": HookEventKind.AFTER_TOOL,
    "agent_before_settle": HookEventKind.WORKER_STOPPING,
    "session_shutdown": HookEventKind.SESSION_ENDED,
}
_DURABLE_EVENT_KINDS = {
    "beforeRequest": HookEventKind.WORK_SUBMITTED,
    "beforeTool": HookEventKind.BEFORE_TOOL,
    "afterTool": HookEventKind.AFTER_TOOL,
    "onYield": HookEventKind.WORKER_STOPPING,
}


class PiHookAdapter:
    client = "pi"
    event_kinds = _PI_EVENT_KINDS

    def parse(self, payload: dict[str, Any]) -> HookEvent:
        try:
            source = PiHookInput.model_validate(payload)
            native = source.event
            name = native.get("type")
            if name not in self.event_kinds:
                raise ValueError(f"unsupported event {name!r}")
            durable = self.client == "pi-durable"
            call = native.get("call", {}) if durable else native
            if not isinstance(call, dict):
                raise ValueError("call must be an object")
            return HookEvent(
                client=self.client,
                session_id=source.session_id,
                cwd=source.cwd,
                kind=self.event_kinds[name],
                source_event_name=name,
                work_id=source.work_id,
                event_id=source.event_id,
                turn_id=source.work_id,
                prompt=native.get("prompt" if durable else "text"),
                tool_name=call.get("name" if durable else "toolName"),
                tool_use_id=call.get("id" if durable else "toolCallId"),
                tool_input=call.get("arguments" if durable else "input"),
                tool_response=native.get("result") if durable else (
                    {key: native[key]
                     for key in ("content", "details", "structuredContent", "isError")
                     if key in native} if name == "tool_result" else None
                ),
                continuation_active=source.continuation_active,
                last_assistant_message=native.get("last_assistant_message"),
            )
        except (ValidationError, ValueError, TypeError) as error:
            raise HookError(f"invalid {self.client} hook input: {error}") from error

    def render(self, event: HookEvent, outcome: HookOutcome) -> dict[str, Any]:
        # The TypeScript bridges apply these effects through native APIs. A HALT
        # remains distinct from BLOCK (which requests a completion continuation).
        if outcome.action is HookAction.ALLOW:
            return {}
        return outcome.model_dump(mode="json", exclude_none=True)


class PiDurableHookAdapter(PiHookAdapter):
    client = "pi-durable"
    event_kinds = _DURABLE_EVENT_KINDS


_ADAPTERS: dict[str, HookAdapter] = {
    CodexHookAdapter.client: CodexHookAdapter(),
    PiHookAdapter.client: PiHookAdapter(),
    PiDurableHookAdapter.client: PiDurableHookAdapter(),
}


def hook_adapter(client: str) -> HookAdapter:
    normalized = client.strip().lower()
    try:
        return _ADAPTERS[normalized]
    except KeyError:
        available = ", ".join(sorted(_ADAPTERS))
        raise HookError(
            f"unsupported hook client {client!r}; available clients: {available}"
        ) from None


def available_hook_clients() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))

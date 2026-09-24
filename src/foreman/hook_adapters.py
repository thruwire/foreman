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


_ADAPTERS: dict[str, HookAdapter] = {
    CodexHookAdapter.client: CodexHookAdapter(),
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

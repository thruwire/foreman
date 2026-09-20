from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from foreman.config import FactoryConfig
from foreman.decision_policy import apply_policy, effective_policy
from foreman.foreman.base import ForemanModel, ForemanModelError
from foreman.models import (
    AbstainCategory,
    Decision,
    DecisionRequest,
    EventType,
    FactoryEvent,
)
from foreman.persistence import PersistenceError, RunStore
from foreman.version import __version__

MCP_PROTOCOL_VERSION = "2024-11-05"

ASK_FOREMAN_TOOL: dict[str, Any] = {
    "name": "ask_foreman",
    "description": (
        "Ask the foreman to autonomously answer a multiple-choice question on "
        "your behalf. The foreman classifies the question, picks one of the "
        "options, and reports its confidence; a configured confidence threshold "
        "and abstain denylist decide whether the answer is returned. When the "
        "foreman abstains, hand the question to the human instead. Abstaining "
        "never terminates or escalates anything."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to decide, as asked to the human.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "The offered answers; the foreman picks one or abstains.",
            },
            "context": {
                "type": "string",
                "description": "What the agent is doing and why the question arose.",
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Decision log session; defaults to one id per server process."
                ),
            },
            "risk_hint": {
                "type": "string",
                "description": "Advisory hint about what the question involves.",
            },
            "extra_abstain_categories": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [category.value for category in AbstainCategory],
                },
                "description": (
                    "Extra abstain categories for this call only. These can only "
                    "tighten the server's denylist, never loosen it."
                ),
            },
            "min_confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "Minimum confidence for this call only. The effective "
                    "threshold is the maximum of this and the server's."
                ),
            },
        },
        "required": ["question", "options"],
    },
}


def _error_response(
    request_id: Any, code: int, message: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class MCPServer:
    """Minimal stdio JSON-RPC 2.0 MCP server exposing the foreman decider."""

    def __init__(
        self,
        *,
        repository: Path | str,
        model: ForemanModel,
        config: FactoryConfig | None = None,
        session_id: str | None = None,
    ) -> None:
        self.repository = Path(repository).resolve()
        self.model = model
        self.config = config or FactoryConfig.from_environment()
        self.session_id = session_id or uuid4().hex[:12]
        self.store = RunStore(self.repository)

    def _resolve_session_id(self, requested: Any) -> str:
        if isinstance(requested, str) and requested:
            try:
                self.store.run_dir(requested)
                return requested
            except PersistenceError:
                pass
        return self.session_id

    async def handle(self, message: Any) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC message; notifications get no response."""

        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error_response(None, -32600, "invalid request")
        method = message.get("method")
        if not isinstance(method, str):
            return _error_response(message.get("id"), -32600, "invalid request")
        request_id = message.get("id")
        if request_id is None:
            # Notification: nothing in this server needs them.
            return None
        if method == "initialize":
            return _result_response(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "foreman", "version": __version__},
                },
            )
        if method == "tools/list":
            return _result_response(request_id, {"tools": [ASK_FOREMAN_TOOL]})
        if method == "tools/call":
            return await self._handle_tool_call(request_id, message.get("params"))
        return _error_response(request_id, -32601, f"unknown method {method!r}")

    async def _handle_tool_call(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        if not isinstance(params, dict) or params.get("name") != "ask_foreman":
            return _error_response(request_id, -32602, "unknown tool")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            return _error_response(request_id, -32602, "tool arguments must be an object")
        try:
            decision_request = DecisionRequest(
                question=arguments["question"],
                options=arguments["options"],
                context=arguments.get("context", ""),
                risk_hint=arguments.get("risk_hint", ""),
                extra_abstain_categories=arguments.get("extra_abstain_categories", []),
                min_confidence=arguments.get("min_confidence"),
            )
        except (KeyError, ValidationError) as error:
            return _error_response(request_id, -32602, f"invalid params: {error}")
        session_id = self._resolve_session_id(arguments.get("session_id"))

        policy = effective_policy(self.config, decision_request)
        try:
            decision = await self.model.decide(decision_request)
        except ForemanModelError as error:
            # A dead decider is not a crash: the question goes back to the human.
            decision = Decision(
                choice=None,
                confidence=0.0,
                rationale=f"decision model unavailable: {error}",
                classification=[],
                abstained=True,
            )
        outcome = apply_policy(
            decision,
            threshold=policy.threshold,
            denylist=policy.denylist,
            options=decision_request.options,
        )
        self.store.append_event(
            FactoryEvent(
                run_id=session_id,
                event_type=EventType.FOREMAN_DECIDED,
                payload={
                    "question": decision_request.question,
                    "options": decision_request.options,
                    "classification": [
                        category.value for category in decision.classification
                    ],
                    "choice": decision.choice,
                    "confidence": decision.confidence,
                    "answered": outcome.answered,
                    "rationale": outcome.rationale,
                    "effective_threshold": policy.threshold,
                    "effective_denylist": sorted(
                        category.value for category in policy.denylist
                    ),
                },
            )
        )
        if outcome.answered:
            result = {
                "status": "answered",
                "choice": outcome.choice,
                "rationale": outcome.rationale,
            }
        else:
            result = {"status": "abstained", "rationale": outcome.rationale}
        return _result_response(
            request_id, {"content": [{"type": "text", "text": json.dumps(result)}]}
        )

    async def serve_stdio(self) -> None:
        """Serve JSON-RPC over stdio with LSP-style Content-Length framing."""

        loop = asyncio.get_running_loop()
        stdin = sys.stdin.buffer
        stdout = sys.stdout.buffer
        while True:
            headers: dict[str, str] = {}
            while True:
                line = await loop.run_in_executor(None, stdin.readline)
                if not line:
                    return
                stripped = line.strip()
                if not stripped:
                    break
                name, _, value = stripped.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                continue
            body = await loop.run_in_executor(None, stdin.read, length)
            if not body:
                return
            try:
                message = json.loads(body)
            except json.JSONDecodeError:
                response: dict[str, Any] | None = _error_response(
                    None, -32700, "parse error"
                )
            else:
                response = await self.handle(message)
            if response is None:
                continue
            payload = json.dumps(response).encode("utf-8")
            stdout.write(
                f"Content-Length: {len(payload)}\r\n\r\n".encode("latin-1") + payload
            )
            stdout.flush()

from __future__ import annotations

import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

DEEPAGENTS_HOOK_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "Stop", "SessionEnd",
)


def install_deepagents_hooks(path: Path, executable: str) -> bool:
    """Merge Foreman's handlers into a Hooks v2 document atomically.

    Existing settings and handlers are retained. Refuse unsupported legacy or
    malformed configurations before writing; repeat setup is a no-op. Returns
    whether the file changed. The caller chooses the configuration destination.
    """
    document: dict[str, Any] = {}
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("hooks configuration must be a Hooks v2 JSON object")
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object; convert legacy hooks to Hooks v2 first")
    argv = [executable, "hook", "--client", "deepagents"]
    command = shlex.join(argv)
    changed = False
    for event in DEEPAGENTS_HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list) or any(
            not isinstance(group, dict) or not isinstance(group.get("hooks"), list)
            or any(not isinstance(handler, dict) for handler in group["hooks"])
            for group in groups
        ):
            raise ValueError(f"{event} must contain matcher groups with command handler lists")
        installed = any(
            group.get("matcher", "*") == "*" and any(
                handler.get("type") == "command" and (
                    handler.get("argv") == argv
                    or ("argv" not in handler and handler.get("command") == command)
                )
                for handler in group["hooks"]
            )
            for group in groups
        )
        if not installed:
            groups.append({"hooks": [{
                "type": "command", "command": command, "argv": argv, "timeout": 120,
            }]})
            changed = True
    if not changed:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
        ) as stream:
            temporary = stream.name
            json.dump(document, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    return True

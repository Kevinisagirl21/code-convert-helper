"""The plugin subprocess protocol described in ``PLUGIN_API.md``.

A plugin is any executable that reads one JSON request object from stdin
and writes one JSON response object to stdout before exiting. This module
is the host side of that contract: it never trusts a plugin to behave --
a crash, a timeout, or malformed JSON just means "no suggestion" for that
call, and the overall conversion continues unaffected.

Python is the primary plugin-authoring path (see
:mod:`plugins.python_sdk`), but this protocol module itself has no
opinion about what language wrote the plugin -- a compiled executable
implementing the same stdin/stdout contract is just as valid.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

PROTOCOL_VERSION = "1"

#: How long to wait for a plugin subprocess before giving up on it. A slow
#: or hung plugin should never be able to stall an entire conversion run.
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass
class PluginRequest:
    hook: str
    context: dict[str, Any] = field(default_factory=dict)
    protocol_version: str = PROTOCOL_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class PluginSuggestion:
    summary: str
    detail: str = ""
    confidence: str = "heuristic"  # "curated" | "heuristic"


def run_external_plugin(
    executable: str, request: PluginRequest, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> PluginSuggestion | None:
    """Invoke an external plugin executable and return its suggestion, if any.

    Never raises on plugin misbehavior -- a failing plugin simply
    contributes nothing to this call, logged for visibility rather than
    surfaced as a hard error.
    """

    try:
        result = subprocess.run(
            [executable, request.hook],
            input=request.to_json(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[code-convert-helper] plugin '{executable}' failed to run: {exc}")
        return None

    if result.returncode != 0:
        print(f"[code-convert-helper] plugin '{executable}' exited with code {result.returncode}; skipping")
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"[code-convert-helper] plugin '{executable}' returned malformed JSON; skipping")
        return None

    suggestion = payload.get("suggestion")
    if suggestion is None:
        return None
    try:
        return PluginSuggestion(**suggestion)
    except TypeError:
        print(f"[code-convert-helper] plugin '{executable}' returned an unrecognized suggestion shape; skipping")
        return None

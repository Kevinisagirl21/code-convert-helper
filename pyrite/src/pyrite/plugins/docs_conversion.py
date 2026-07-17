"""Built-in plugin: docstring -> rustdoc conversion. **Not yet implemented.**

This is deliberately left as a documented stub rather than a half-working
guess. ``PLUGIN_API.md`` specifies the target behavior: recognize Sphinx,
Google-style, and NumPy-style docstrings attached to IR nodes and emit an
idiomatic rustdoc (``///``) comment block with ``# Arguments`` /
``# Returns`` / ``# Errors`` sections.

What's needed before this can be implemented for real:

1. The IR schema needs a dedicated docstring node (today a docstring is
   just the first statement of a function body, an ordinary
   ``ExprStmt`` wrapping a string constant -- it isn't structurally
   distinguished from any other expression statement).
2. A small parser per docstring style (Sphinx's ``:param:``/``:returns:``,
   Google's ``Args:``/``Returns:``, NumPy's underlined section headers).

Left unimplemented here rather than shipping a partial parser that would
silently mishandle two of the three styles.
"""

from __future__ import annotations


def convert_docstring(_docstring_text: str, _style: str = "auto") -> str:
    raise NotImplementedError(
        "docstring-to-rustdoc conversion is planned (see PLUGIN_API.md) "
        "but not implemented in this prototype"
    )

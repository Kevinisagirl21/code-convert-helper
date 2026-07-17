"""Built-in plugin: suggest a Rust crate in place of a Python import.

Runs in-process (unlike a third-party plugin, which goes through the
subprocess protocol in :mod:`plugins.protocol`) since it ships with
the tool itself. The curated table below is ordinary, editable data --
not a conversion rule -- and every suggestion is surfaced as a marked
comment, never silently substituted, per ``PLUGIN_API.md``.
"""

from __future__ import annotations

from ir import schema
from plugins.protocol import PluginSuggestion

#: module.attribute -> curated suggestion. Deliberately small; growing
#: this table is safe (it only ever produces a marked suggestion), unlike
#: growing the core conversion logic.
_CURATED_TABLE: dict[str, PluginSuggestion] = {
    "requests.get": PluginSuggestion(
        summary="consider `reqwest` (blocking client)",
        detail='add `reqwest = { version = "*", features = ["blocking"] }`',
        confidence="curated",
    ),
    "requests.post": PluginSuggestion(
        summary="consider `reqwest` (blocking client)",
        detail='add `reqwest = { version = "*", features = ["blocking"] }`',
        confidence="curated",
    ),
    "json.dumps": PluginSuggestion(
        summary="consider `serde_json::to_string`",
        detail='add `serde_json = "*"` and a `#[derive(Serialize)]` type',
        confidence="curated",
    ),
    "json.loads": PluginSuggestion(
        summary="consider `serde_json::from_str`",
        detail='add `serde_json = "*"` and a `#[derive(Deserialize)]` type',
        confidence="curated",
    ),
}


def suggest_crate(module: str, attr: str) -> PluginSuggestion | None:
    """Look up a curated suggestion for ``module.attr``, if one exists."""

    return _CURATED_TABLE.get(f"{module}.{attr}")


def annotate_crate_suggestions(module: schema.ModuleNode) -> None:
    """Walk a built module IR and attach crate-substitution comments.

    Looks for calls shaped like ``imported_module.attr(...)`` at the top
    level of an expression statement or assignment, and -- if the curated
    table has a match -- appends a marked ``SUGGESTED CRATE`` comment. This
    never rewrites the call itself; it only ever adds a visible marker,
    consistent with every other ambiguity-adjacent decision in the tool.
    """

    imported_modules = {
        top.alias or top.module: top.module for top in module.body if isinstance(top, schema.ImportNode)
    }
    if not imported_modules:
        return

    def _check_expr(expr: schema.Expr) -> PluginSuggestion | None:
        if (
            isinstance(expr, schema.CallExpr)
            and isinstance(expr.func, schema.AttributeExpr)
            and isinstance(expr.func.value, schema.NameExpr)
        ):
            local_name = expr.func.value.name
            real_module = imported_modules.get(local_name)
            if real_module is not None:
                return suggest_crate(real_module, expr.func.attr)
        return None

    def _annotate(stmt: schema.Stmt) -> None:
        suggestion = None
        if isinstance(stmt, schema.ExprStmt):
            suggestion = _check_expr(stmt.value)
        elif isinstance(stmt, schema.AssignStmt):
            suggestion = _check_expr(stmt.value)
        if suggestion is not None:
            stmt.comments.leading.append(
                schema.Comment(
                    text=f"SUGGESTED CRATE: {suggestion.summary} ({suggestion.detail})"
                )
            )
        if isinstance(stmt, schema.IfStmt):
            for s in stmt.body + stmt.orelse:
                _annotate(s)
        elif isinstance(stmt, (schema.WhileStmt, schema.ForStmt)):
            for s in stmt.body:
                _annotate(s)

    for top in module.body:
        if isinstance(top, schema.FunctionDefNode):
            for s in top.body:
                _annotate(s)
        elif isinstance(top, schema.ClassDefNode):
            for m in top.methods:
                for s in m.body:
                    _annotate(s)

"""Serializing IR to disk, and loading it back.

The IR file is a real artifact, not a throwaway cache: :func:`save_module`
writes it as formatted JSON and then marks it read-only, so nothing --
including this tool's own later stages -- can hand-edit it and break the
invariants the schema depends on. See ``ARCHITECTURE.md`` for the full
rationale and the planned upgrade path for future schema versions.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
from pathlib import Path
from typing import Any

from pyrite.ir import schema

# Maps a node's "kind" tag to the dataclass that should be reconstructed
# from it. Every dataclass in schema.py that can appear inside a Stmt/Expr
# union or as a top-level body entry must be registered here.
_EXPR_REGISTRY: dict[str, type] = {
    "constant": schema.ConstantExpr,
    "name": schema.NameExpr,
    "binop": schema.BinOpExpr,
    "compare": schema.CompareExpr,
    "boolop": schema.BoolOpExpr,
    "unaryop": schema.UnaryOpExpr,
    "call": schema.CallExpr,
    "attribute": schema.AttributeExpr,
    "subscript": schema.SubscriptExpr,
    "list": schema.ListExpr,
    "dict": schema.DictExpr,
}

_STMT_REGISTRY: dict[str, type] = {
    "assign": schema.AssignStmt,
    "return": schema.ReturnStmt,
    "expr_stmt": schema.ExprStmt,
    "pass": schema.PassStmt,
    "if": schema.IfStmt,
    "while": schema.WhileStmt,
    "for": schema.ForStmt,
    "raise": schema.RaiseStmt,
    "unsupported": schema.UnsupportedStmt,
}

_TOP_LEVEL_REGISTRY: dict[str, type] = {
    **_STMT_REGISTRY,
    "function_def": schema.FunctionDefNode,
    "class_def": schema.ClassDefNode,
    "import": schema.ImportNode,
}

# Field names whose values are themselves expressions (single or list) or
# statement lists, and therefore need tagged-union reconstruction rather
# than being treated as plain data.
_EXPR_FIELDS = {"value", "left", "right", "operand", "func", "test", "index", "iter", "message"}
_EXPR_LIST_FIELDS = {"values", "args", "elements", "keys"}
_STMT_LIST_FIELDS = {"body", "orelse"}


def module_to_dict(module: schema.ModuleNode) -> dict[str, Any]:
    """Convert a :class:`~pyrite.ir.schema.ModuleNode` to a plain JSON-safe dict.

    ``dataclasses.asdict`` recurses into nested dataclasses based on their
    *actual* runtime type, so this works uniformly across the tagged
    unions defined in :mod:`pyrite.ir.schema` without needing a per-class
    ``to_dict`` method.
    """

    return dataclasses.asdict(module)


def _reconstruct_expr(data: dict[str, Any] | None) -> Any:
    if data is None:
        return None
    kind = data["kind"]
    cls = _EXPR_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"unknown expression kind: {kind!r}")
    return _reconstruct_node(cls, data)


def _reconstruct_stmt(data: dict[str, Any]) -> Any:
    kind = data["kind"]
    cls = _STMT_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"unknown statement kind: {kind!r}")
    return _reconstruct_node(cls, data)


def _reconstruct_type_slot(data: dict[str, Any]) -> schema.TypeSlot:
    if data["kind"] == "concrete":
        return schema.ConcreteType(value=data["value"])
    return schema.TypeHole(id=data["id"], known_info=list(data.get("known_info", [])))


def _reconstruct_node(cls: type, data: dict[str, Any]) -> Any:
    """Rebuild one dataclass instance, recursing into known nested shapes."""

    kwargs: dict[str, Any] = {}
    field_names = {f.name for f in dataclasses.fields(cls)}
    for key, value in data.items():
        if key not in field_names:
            continue
        if key == "type" and isinstance(value, dict):
            kwargs[key] = _reconstruct_type_slot(value)
        elif key == "return_type" and isinstance(value, dict):
            kwargs[key] = _reconstruct_type_slot(value)
        elif key in _EXPR_FIELDS and isinstance(value, dict):
            kwargs[key] = _reconstruct_expr(value)
        elif key in _EXPR_LIST_FIELDS and isinstance(value, list):
            kwargs[key] = [_reconstruct_expr(v) for v in value]
        elif key in _STMT_LIST_FIELDS and isinstance(value, list):
            kwargs[key] = [_reconstruct_stmt(v) for v in value]
        elif key == "body" and cls in (schema.FunctionDefNode,) and isinstance(value, list):
            kwargs[key] = [_reconstruct_stmt(v) for v in value]
        elif key == "comments" and isinstance(value, dict):
            kwargs[key] = schema.Comments(
                leading=[schema.Comment(**c) for c in value.get("leading", [])],
                trailing=[schema.Comment(**c) for c in value.get("trailing", [])],
            )
        elif key == "source_span" and isinstance(value, dict):
            kwargs[key] = schema.SourceSpan(**value)
        elif key == "ambiguity" and isinstance(value, dict):
            kwargs[key] = schema.Ambiguity(**value)
        elif key == "params" and isinstance(value, list):
            kwargs[key] = [
                schema.Param(name=p["name"], type=_reconstruct_type_slot(p["type"]))
                for p in value
            ]
        elif key == "fields" and isinstance(value, list) and cls is schema.ClassDefNode:
            kwargs[key] = [
                schema.ClassFieldNode(name=f["name"], type=_reconstruct_type_slot(f["type"]))
                for f in value
            ]
        elif key == "methods" and isinstance(value, list):
            kwargs[key] = [_reconstruct_node(schema.FunctionDefNode, m) for m in value]
        else:
            kwargs[key] = value
    return cls(**kwargs)


def module_from_dict(data: dict[str, Any]) -> schema.ModuleNode:
    """Reconstruct a :class:`~pyrite.ir.schema.ModuleNode` from its dict form."""

    body = []
    for entry in data.get("body", []):
        cls = _TOP_LEVEL_REGISTRY.get(entry["kind"])
        if cls is None:
            raise ValueError(f"unknown top-level kind: {entry['kind']!r}")
        body.append(_reconstruct_node(cls, entry))
    return schema.ModuleNode(
        schema_version=data["schema_version"],
        source_file=data["source_file"],
        body=body,
    )


def save_module(module: schema.ModuleNode, path: Path, *, read_only: bool = True) -> None:
    """Write ``module`` to ``path`` as formatted JSON, then lock it read-only.

    Parameters
    ----------
    read_only:
        Set to ``False`` only for the tool's own internal upgrade passes
        (see ``ARCHITECTURE.md``). User-facing runs should always leave
        this ``True``.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # A prior locked file: unlock briefly so we can overwrite it, this
        # run's output is authoritative for this invocation.
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
    path.write_text(json.dumps(module_to_dict(module), indent=2), encoding="utf-8")
    if read_only:
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def load_module(path: Path) -> schema.ModuleNode:
    """Read an IR file back into a :class:`~pyrite.ir.schema.ModuleNode`.

    Read-only permissions on the file are not touched by this function --
    loading an IR file for inspection should never require unlocking it.
    """

    data = json.loads(path.read_text(encoding="utf-8"))
    return module_from_dict(data)

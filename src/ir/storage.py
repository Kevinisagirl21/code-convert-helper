"""Serializing IR to disk, and loading it back."""

from __future__ import annotations

import dataclasses
import json
import os
import stat
from pathlib import Path
from typing import Any

from ir import schema

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

_EXPR_FIELDS = {"value", "left", "right", "operand", "func", "test", "index", "iter", "message"}
_EXPR_LIST_FIELDS = {"values", "args", "elements", "keys"}
_STMT_LIST_FIELDS = {"body", "orelse"}

_OWNERSHIP_FIELDS = {"ownership", "return_ownership"}


def module_to_dict(module: schema.ModuleNode) -> dict[str, Any]:
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


def _reconstruct_directive(data: dict[str, Any] | None) -> schema.Directive | None:
    if data is None:
        return None
    return schema.Directive(
        directive_key=data["directive_key"], value=data["value"], raw_text=data["raw_text"]
    )


def _reconstruct_ownership(data: dict[str, Any] | None) -> schema.OwnershipDecision | None:
    if data is None:
        return None
    return schema.OwnershipDecision(
        value=data["value"],
        source=data["source"],
        directive=_reconstruct_directive(data.get("directive")),
        evidence=list(data.get("evidence", [])),
        conflict=data.get("conflict"),
    )


def _reconstruct_node(cls: type, data: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    field_names = {f.name for f in dataclasses.fields(cls)}
    for key, value in data.items():
        if key not in field_names:
            continue
        if key == "type" and isinstance(value, dict):
            kwargs[key] = _reconstruct_type_slot(value)
        elif key == "return_type" and isinstance(value, dict):
            kwargs[key] = _reconstruct_type_slot(value)
        elif key in _OWNERSHIP_FIELDS:
            kwargs[key] = _reconstruct_ownership(value)
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
                schema.Param(
                    name=p["name"],
                    type=_reconstruct_type_slot(p["type"]),
                    ownership=_reconstruct_ownership(p.get("ownership")),
                )
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
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
    path.write_text(json.dumps(module_to_dict(module), indent=2), encoding="utf-8")
    if read_only:
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def load_module(path: Path) -> schema.ModuleNode:
    data = json.loads(path.read_text(encoding="utf-8"))
    return module_from_dict(data)

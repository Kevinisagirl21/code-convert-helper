"""Type-hint resolution for py2rust v2.

v1 called this module "inference" because it guessed types it wasn't
told and fell back to a :class:`~ir.schema.TypeHole` when it couldn't.
v2 requires an explicit, mandatory type hint everywhere preflight
enforces one (see :mod:`preflight.checks`), so there is nothing left to
*infer* -- this module now only resolves an already-present hint's text
into a :class:`~ir.schema.ConcreteType`, or raises if it can't. Preflight
is expected to have already hard-rejected any missing/unhinted case
before this module is ever called, so encountering ``None`` here
indicates a preflight/builder inconsistency, not a normal "no hint"
case.

The module name and location are kept as-is ("edit in place" per the
Milestone 1 decision) even though "inference" no longer describes what
it does, to avoid an unrelated rename/move touching every importer in
the same change that already rewrites the internals.
"""

from __future__ import annotations

import libcst as cst

from ir import schema


class UnsupportedTypeHintError(ValueError):
    """Raised when an annotation exists but isn't one v2's MVP subset
    understands (e.g. a generic or a user-defined class name).

    Preflight's hint-presence check only verifies *some* annotation is
    present -- it doesn't validate every hint is one of the concrete
    types the codegen subset supports. That validation happens here,
    at the point the annotation is actually resolved to a Rust type,
    and is deliberately a hard error (not a hole) since v2 has no
    unresolved-type representation anymore.
    """


#: Maps a Python annotation's source text to a concrete Rust type name.
#: Deliberately small and explicit -- growing this table is safe, silently
#: mis-mapping an annotation is not.
_ANNOTATION_MAP = {
    "int": "i64",
    "float": "f64",
    "str": "String",
    "bool": "bool",
    "None": "()",
}


def type_from_annotation(annotation: cst.Annotation | None) -> schema.ConcreteType:
    """Resolve an explicit, mandatory type hint to a concrete Rust type.

    Handles the v2 MVP subset's core scalar types directly (``int``,
    ``float``, ``str``, ``bool``, ``None``) plus one level of
    ``list[T]`` / ``dict[K, V]`` generic subscript, recursing into the
    element type(s) the same way v1's literal-based inference did for
    ``Vec<T>`` / ``HashMap<K, V>`` -- just from an explicit hint instead
    of a guess now.

    Parameters
    ----------
    annotation:
        The hint's CST node. Must not be ``None`` -- preflight is
        responsible for hard-rejecting any missing hint before the
        builder ever calls this. Passing ``None`` here is a programming
        error, not a "no hint" case to handle gracefully.

    Raises
    ------
    ValueError
        If ``annotation`` is ``None`` (preflight/builder inconsistency).
    UnsupportedTypeHintError
        If the annotation is present but isn't a type v2's MVP codegen
        subset understands.
    """

    if annotation is None:
        raise ValueError(
            "type_from_annotation() called with no annotation; preflight "
            "should have hard-rejected this before the builder ran"
        )
    return _resolve_annotation_node(annotation.annotation)


def _resolve_annotation_node(node: cst.BaseExpression) -> schema.ConcreteType:
    if isinstance(node, cst.Subscript) and isinstance(node.value, cst.Name):
        base = node.value.value
        slice_exprs = [s.slice.value for s in node.slice if isinstance(s.slice, cst.Index)]
        if base == "list" and len(slice_exprs) == 1:
            elem = _resolve_annotation_node(slice_exprs[0])
            return schema.ConcreteType(value=f"Vec<{elem.value}>")
        if base == "dict" and len(slice_exprs) == 2:
            key = _resolve_annotation_node(slice_exprs[0])
            val = _resolve_annotation_node(slice_exprs[1])
            return schema.ConcreteType(value=f"HashMap<{key.value}, {val.value}>")
        raise UnsupportedTypeHintError(
            f"generic hint '{base}[...]' isn't part of the v2 MVP subset "
            f"(only 'list[T]' and 'dict[K, V]' are supported)"
        )
    text = cst.Module([]).code_for_node(node).strip()
    mapped = _ANNOTATION_MAP.get(text)
    if mapped is None:
        raise UnsupportedTypeHintError(
            f"type hint {text!r} isn't part of the v2 MVP subset "
            f"({', '.join(sorted(_ANNOTATION_MAP))}, 'list[T]', 'dict[K, V]')"
        )
    return schema.ConcreteType(value=mapped)




"""Intermediate representation (IR) schema.

This module defines the versioned, serializable data structures that make
up pyrite's intermediate representation. The IR sits between the Python
front end (:mod:`pyrite.ir.builder`) and the Rust back end
(:mod:`pyrite.codegen.rust_writer`): it is the artifact that gets written
to disk, locked read-only, and inspected for debugging.

Design notes
------------
* Every node kind is a plain :func:`dataclasses.dataclass` with no
  inheritance between node kinds. This keeps serialization simple
  (:func:`dataclasses.asdict` handles any nested dataclass regardless of
  static type) and keeps each class easy to read in isolation.
* A ``kind`` field on every node acts as a tag so a dict loaded back from
  JSON can be dispatched to the right dataclass constructor
  (see :mod:`pyrite.ir.storage`).
* Type information is never a bare guess. A type slot is either a
  :class:`ConcreteType` (resolved) or a :class:`TypeHole` (explicitly
  unresolved, carrying whatever partial evidence inference collected).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

#: Schema version for this IR shape. A future revision that adds support
#: for e.g. decorators or generators bumps this and writes IR under a new
#: version rather than mutating files written under this one.
SCHEMA_VERSION = "v1_core"


@dataclass
class SourceSpan:
    """A location in the original Python source file."""

    file: str
    start_line: int
    end_line: int


@dataclass
class ConcreteType:
    """A fully resolved type, e.g. produced from a type hint or inference."""

    value: str
    kind: str = "concrete"


@dataclass
class TypeHole:
    """An explicitly unresolved type slot.

    Attributes
    ----------
    id:
        Stable identifier so the same hole can be referenced from more
        than one place (e.g. a parameter and a later usage).
    known_info:
        Human-readable fragments of evidence gathered during inference,
        e.g. ``"compared with '>' against param 'value' (int)"``. These
        are carried all the way to codegen and rendered as a reference
        comment above the hole, instead of being discarded.
    """

    id: str
    known_info: list[str] = field(default_factory=list)
    kind: str = "hole"


#: A type slot is always one or the other -- never a silent default.
TypeSlot = Union[ConcreteType, TypeHole]


@dataclass
class Comment:
    """A single comment, with a confidence score for its attachment."""

    text: str
    confidence: float = 1.0


@dataclass
class Comments:
    """Comments attached to a node: those above it, and same-line trailing."""

    leading: list[Comment] = field(default_factory=list)
    trailing: list[Comment] = field(default_factory=list)


@dataclass
class Ambiguity:
    """Records a judgment call the tool made, so it can be marked visibly.

    Attributes
    ----------
    category:
        A short machine-readable label, e.g. ``"collection-type"``.
    chosen:
        What the tool actually emitted.
    alternatives:
        Other reasonable choices that were not picked -- this list is
        allowed to have a single entry today ("no other option
        implemented yet") without changing the shape of the field, so a
        future revision can add real alternatives without touching the
        schema.
    rationale:
        Short human-readable explanation shown in the generated comment.
    """

    category: str
    chosen: str
    alternatives: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class Param:
    """A function or method parameter."""

    name: str
    type: TypeSlot


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass
class ConstantExpr:
    """A literal: int, float, str, bool, or None."""

    value: object
    py_type: str  # "int" | "float" | "str" | "bool" | "None"
    kind: str = "constant"


@dataclass
class NameExpr:
    """A bare name reference, e.g. ``x``."""

    name: str
    kind: str = "name"


@dataclass
class BinOpExpr:
    """A binary operator expression, e.g. ``a + b``."""

    op: str
    left: "Expr"
    right: "Expr"
    kind: str = "binop"


@dataclass
class CompareExpr:
    """A comparison, e.g. ``a < b``. Only single comparisons in v1."""

    op: str
    left: "Expr"
    right: "Expr"
    kind: str = "compare"


@dataclass
class BoolOpExpr:
    """A boolean combination, e.g. ``a and b``."""

    op: str  # "and" | "or"
    values: list["Expr"]
    kind: str = "boolop"


@dataclass
class UnaryOpExpr:
    """A unary operator, e.g. ``not x`` or ``-x``."""

    op: str
    operand: "Expr"
    kind: str = "unaryop"


@dataclass
class CallExpr:
    """A function or method call."""

    func: "Expr"
    args: list["Expr"] = field(default_factory=list)
    kind: str = "call"


@dataclass
class AttributeExpr:
    """Attribute access, e.g. ``self.x`` or ``requests.get``."""

    value: "Expr"
    attr: str
    kind: str = "attribute"


@dataclass
class SubscriptExpr:
    """Indexing, e.g. ``items[0]``."""

    value: "Expr"
    index: "Expr"
    kind: str = "subscript"


@dataclass
class ListExpr:
    """A list literal."""

    elements: list["Expr"] = field(default_factory=list)
    kind: str = "list"


@dataclass
class DictExpr:
    """A dict literal."""

    keys: list["Expr"] = field(default_factory=list)
    values: list["Expr"] = field(default_factory=list)
    kind: str = "dict"


Expr = Union[
    ConstantExpr,
    NameExpr,
    BinOpExpr,
    CompareExpr,
    BoolOpExpr,
    UnaryOpExpr,
    CallExpr,
    AttributeExpr,
    SubscriptExpr,
    ListExpr,
    DictExpr,
]


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class AssignStmt:
    """An assignment.

    ``target_kind`` distinguishes:

    * ``"name"`` -- a fresh local binding -> Rust ``let`` (``let mut`` if
      ``mutable`` is set because the name is reassigned later).
    * ``"self_attr"`` -- a mutation of an existing struct field -> plain
      ``self.x = ...;``, no ``let``/type, since the field's type was
      already declared on the struct.
    * ``"reassign"`` -- a later assignment to a name already bound earlier
      in the same function (e.g. a loop accumulator) -> plain
      ``x = ...;``, no ``let``/type, since re-declaring would shadow
      rather than mutate. See
      :func:`pyrite.ir.builder.apply_mutability` for how this gets set.
    """

    target: str
    value: "Expr"
    type: TypeSlot
    mutable: bool = False
    target_kind: str = "name"  # "name" | "self_attr" | "reassign"
    comments: Comments = field(default_factory=Comments)
    kind: str = "assign"


@dataclass
class ReturnStmt:
    value: "Expr | None"
    comments: Comments = field(default_factory=Comments)
    kind: str = "return"


@dataclass
class ExprStmt:
    """A bare expression used as a statement, e.g. a call for side effects."""

    value: "Expr"
    comments: Comments = field(default_factory=Comments)
    kind: str = "expr_stmt"


@dataclass
class PassStmt:
    comments: Comments = field(default_factory=Comments)
    kind: str = "pass"


@dataclass
class IfStmt:
    test: "Expr"
    body: list["Stmt"]
    orelse: list["Stmt"] = field(default_factory=list)
    comments: Comments = field(default_factory=Comments)
    kind: str = "if"


@dataclass
class WhileStmt:
    test: "Expr"
    body: list["Stmt"]
    comments: Comments = field(default_factory=Comments)
    kind: str = "while"


@dataclass
class ForStmt:
    """A ``for target in iter:`` loop.

    ``iter_kind`` records whether the iterable was recognized as a
    ``range(...)`` call (translated to a Rust range expression) or a
    generic sequence (translated to ``.iter()``) -- this itself is an
    ambiguity-adjacent decision worth keeping explicit rather than
    silently picking one.
    """

    target: str
    iter: "Expr"
    iter_kind: str  # "range" | "sequence"
    body: list["Stmt"]
    comments: Comments = field(default_factory=Comments)
    kind: str = "for"


@dataclass
class RaiseStmt:
    """A ``raise`` statement.

    Rust has no exceptions, so this is always ambiguity-marked in codegen
    (see :mod:`pyrite.codegen.rust_writer`) -- the default translation is
    a ``panic!``, clearly marked as a placeholder for a ``Result``-based
    rewrite.
    """

    message: "Expr | None"
    comments: Comments = field(default_factory=Comments)
    kind: str = "raise"


@dataclass
class UnsupportedStmt:
    """An opaque placeholder for a construct v1 doesn't understand yet.

    Carries the exact original source text and location so a future
    revision can find and re-parse just this fragment without touching
    the rest of the already-converted IR.
    """

    source_text: str
    reason: str
    comments: Comments = field(default_factory=Comments)
    kind: str = "unsupported"


Stmt = Union[
    AssignStmt,
    ReturnStmt,
    ExprStmt,
    PassStmt,
    IfStmt,
    WhileStmt,
    ForStmt,
    RaiseStmt,
    UnsupportedStmt,
]


# ---------------------------------------------------------------------------
# Top-level nodes
# ---------------------------------------------------------------------------


@dataclass
class FunctionDefNode:
    node_id: str
    name: str
    params: list[Param]
    return_type: TypeSlot
    body: list[Stmt]
    source_span: SourceSpan
    comments: Comments = field(default_factory=Comments)
    ambiguity: "Ambiguity | None" = None
    kind: str = "function_def"


@dataclass
class ClassFieldNode:
    """A struct field inferred from a ``self.x = ...`` assignment in ``__init__``."""

    name: str
    type: TypeSlot


@dataclass
class ClassDefNode:
    node_id: str
    name: str
    fields: list[ClassFieldNode]
    methods: list[FunctionDefNode]
    source_span: SourceSpan
    comments: Comments = field(default_factory=Comments)
    ambiguity: "Ambiguity | None" = None
    unsupported_bases: list[str] = field(default_factory=list)
    kind: str = "class_def"


@dataclass
class ImportNode:
    """A recorded ``import`` statement.

    Not translated to Rust directly in v1 -- kept so plugins (e.g. the
    crate-substitution plugin) can recognize which module a later
    ``module.call(...)`` belongs to.
    """

    module: str
    alias: "str | None"
    source_span: SourceSpan
    kind: str = "import"


TopLevel = Union[FunctionDefNode, ClassDefNode, ImportNode, UnsupportedStmt]


@dataclass
class ModuleNode:
    """The root IR node for one source file."""

    schema_version: str
    source_file: str
    body: list[TopLevel] = field(default_factory=list)

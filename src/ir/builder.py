"""Stages 1-4: parse Python source, build the IR, mark ambiguities.

This is the front end's core: it walks a ``libcst`` concrete syntax tree
(chosen specifically because it keeps every comment attached to the node
it belongs to -- no heuristic re-association needed) and produces the
:mod:`ir.schema` data structures that get serialized to disk.

Only the v2 MVP subset is understood here. Anything else becomes an
:class:`~ir.schema.UnsupportedStmt` carrying the exact original
source text, per the "capture, don't drop" principle in
``ARCHITECTURE.md``.

v2 (Milestone 1): type resolution now goes through the shared
:class:`~typing_inference.resolver.TypeResolver` instead of v1's
hole-producing ``typing_inference.infer`` helpers. The builder assumes
:func:`preflight.checks.run_preflight` has already hard-rejected any file
with a missing mandatory hint -- a :class:`~typing_inference.resolver.MandatoryHintError`
raised here indicates that invariant was violated (builder called
without a passing preflight run), not a normal "no hint" case.
"""

from __future__ import annotations

import itertools

import libcst as cst
from libcst.metadata import CodeRange, PositionProvider

from ambiguity import resolver as ambiguity
from ir import schema
from typing_inference.resolver import TypeResolver

_node_counter = itertools.count(1)


def reset_node_counter() -> None:
    """Reset the node ID counter. Mainly useful for deterministic tests."""

    global _node_counter
    _node_counter = itertools.count(1)


def _next_id(prefix: str) -> str:
    return f"{prefix}_{next(_node_counter):04d}"


_BIN_OPS = {
    cst.Add: "+",
    cst.Subtract: "-",
    cst.Multiply: "*",
    cst.Divide: "/",
    cst.Modulo: "%",
}

_COMPARE_OPS = {
    cst.Equal: "==",
    cst.NotEqual: "!=",
    cst.LessThan: "<",
    cst.LessThanEqual: "<=",
    cst.GreaterThan: ">",
    cst.GreaterThanEqual: ">=",
}

_BOOL_OPS = {
    cst.And: "and",
    cst.Or: "or",
}


class IRBuilder:
    """Builds a :class:`~ir.schema.ModuleNode` from Python source.

    One builder instance corresponds to one source file. Position lookups
    are resolved once up front via ``libcst``'s metadata system.
    """

    def __init__(self, source: str, source_file: str) -> None:
        self._source_file = source_file
        wrapper = cst.MetadataWrapper(cst.parse_module(source))
        self._positions: dict[cst.CSTNode, CodeRange] = wrapper.resolve(PositionProvider)
        self._module = wrapper.module
        # One flat, whole-file resolver -- see typing_inference.resolver
        # module docstring for why this isn't per-function/per-class
        # scoped. `self.x` fields naturally persist across a class's
        # methods through this same table (keyed as "self.<attr>"), which
        # replaces v1's separate `_current_field_types` dict.
        self._resolver = TypeResolver()

    # -- helpers -----------------------------------------------------

    def _span(self, node: cst.CSTNode) -> schema.SourceSpan:
        pos = self._positions.get(node)
        if pos is None:
            return schema.SourceSpan(self._source_file, 0, 0)
        return schema.SourceSpan(self._source_file, pos.start.line, pos.end.line)

    def _source_text(self, node: cst.CSTNode) -> str:
        return cst.Module([]).code_for_node(node).strip()

    def _leading_comments(self, leading_lines: tuple[cst.EmptyLine, ...]) -> list[schema.Comment]:
        return [
            schema.Comment(text=line.comment.value)
            for line in leading_lines
            if line.comment is not None
        ]

    def _trailing_comment(
        self, trailing_whitespace: cst.TrailingWhitespace | None
    ) -> list[schema.Comment]:
        if trailing_whitespace is not None and trailing_whitespace.comment is not None:
            return [schema.Comment(text=trailing_whitespace.comment.value)]
        return []

    def _simple_stmt_comments(self, node: cst.SimpleStatementLine) -> schema.Comments:
        return schema.Comments(
            leading=self._leading_comments(node.leading_lines),
            trailing=self._trailing_comment(node.trailing_whitespace),
        )

    def _compound_comments(self, node: cst.CSTNode) -> schema.Comments:
        """Leading/header comments for If/While/For (no same-line trailing
        capture for compound statements in v1 -- see ARCHITECTURE.md open
        questions)."""

        leading_lines = getattr(node, "leading_lines", ())
        return schema.Comments(leading=self._leading_comments(leading_lines))

    # -- expressions ---------------------------------------------------

    def build_expr(self, node: cst.BaseExpression) -> schema.Expr:
        if isinstance(node, cst.Integer):
            return schema.ConstantExpr(value=int(node.value), py_type="int")
        if isinstance(node, cst.Float):
            return schema.ConstantExpr(value=float(node.value), py_type="float")
        if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
            return schema.ConstantExpr(value=node.evaluated_value, py_type="str")
        if isinstance(node, cst.Name):
            if node.value == "True":
                return schema.ConstantExpr(value=True, py_type="bool")
            if node.value == "False":
                return schema.ConstantExpr(value=False, py_type="bool")
            if node.value == "None":
                return schema.ConstantExpr(value=None, py_type="None")
            return schema.NameExpr(name=node.value)
        if isinstance(node, cst.BinaryOperation):
            op = _BIN_OPS.get(type(node.operator), "?")
            return schema.BinOpExpr(
                op=op, left=self.build_expr(node.left), right=self.build_expr(node.right)
            )
        if isinstance(node, cst.Comparison):
            comp = node.comparisons[0]
            op = _COMPARE_OPS.get(type(comp.operator), "?")
            return schema.CompareExpr(
                op=op, left=self.build_expr(node.left), right=self.build_expr(comp.comparator)
            )
        if isinstance(node, cst.BooleanOperation):
            op = _BOOL_OPS.get(type(node.operator), "?")
            return schema.BoolOpExpr(
                op=op, values=[self.build_expr(node.left), self.build_expr(node.right)]
            )
        if isinstance(node, cst.UnaryOperation):
            op = "-" if isinstance(node.operator, cst.Minus) else "not"
            return schema.UnaryOpExpr(op=op, operand=self.build_expr(node.expression))
        if isinstance(node, cst.Call):
            return schema.CallExpr(
                func=self.build_expr(node.func),
                args=[self.build_expr(a.value) for a in node.args],
            )
        if isinstance(node, cst.Attribute):
            return schema.AttributeExpr(value=self.build_expr(node.value), attr=node.attr.value)
        if isinstance(node, cst.Subscript):
            index_node = node.slice[0].slice
            index_expr = index_node.value if isinstance(index_node, cst.Index) else index_node
            return schema.SubscriptExpr(
                value=self.build_expr(node.value), index=self.build_expr(index_expr)
            )
        if isinstance(node, cst.List):
            return schema.ListExpr(elements=[self.build_expr(e.value) for e in node.elements])
        if isinstance(node, cst.Dict):
            keys, values = [], []
            for el in node.elements:
                if isinstance(el, cst.DictElement):
                    keys.append(self.build_expr(el.key))
                    values.append(self.build_expr(el.value))
            return schema.DictExpr(keys=keys, values=values)
        # Fall back to a name-like placeholder rather than raising, so one
        # unrecognized expression doesn't take down the whole statement.
        return schema.NameExpr(name=f"/* unrecognized: {self._source_text(node)} */")

    # -- statements ------------------------------------------------------

    def build_stmt(self, stmt: cst.BaseStatement, sibling_body: list[cst.BaseStatement]) -> schema.Stmt:
        if isinstance(stmt, cst.SimpleStatementLine):
            return self._build_simple_stmt_line(stmt, sibling_body)
        if isinstance(stmt, cst.If):
            return schema.IfStmt(
                test=self.build_expr(stmt.test),
                body=self.build_block(stmt.body),
                orelse=self._build_orelse(stmt.orelse),
                comments=self._compound_comments(stmt),
            )
        if isinstance(stmt, cst.While):
            return schema.WhileStmt(
                test=self.build_expr(stmt.test),
                body=self.build_block(stmt.body),
                comments=self._compound_comments(stmt),
            )
        if isinstance(stmt, cst.For):
            return self._build_for(stmt)
        return schema.UnsupportedStmt(
            source_text=self._source_text(stmt),
            reason=f"'{type(stmt).__name__}' is not part of the v1 core subset",
        )

    def _build_orelse(self, orelse: cst.Else | None) -> list[schema.Stmt]:
        if orelse is None:
            return []
        return self.build_block(orelse.body)

    def _build_for(self, stmt: cst.For) -> schema.ForStmt:
        target_name = stmt.target.value if isinstance(stmt.target, cst.Name) else "_"
        iter_expr = stmt.iter
        if (
            isinstance(iter_expr, cst.Call)
            and isinstance(iter_expr.func, cst.Name)
            and iter_expr.func.value == "range"
        ):
            iter_kind = "range"
        else:
            iter_kind = "sequence"
        # Mandatory-hint resolution: no hint syntax exists for a `for`
        # target, so its type is always derived from the iterable (range
        # -> int, a hinted list -> its element type). Preflight has
        # already validated this is derivable; register it here too so
        # statements in the loop body that reference the target resolve
        # correctly.
        self._resolver.resolve_for_target(target_name, iter_kind, iter_expr)
        return schema.ForStmt(
            target=target_name,
            iter=self.build_expr(iter_expr),
            iter_kind=iter_kind,
            body=self.build_block(stmt.body),
            comments=self._compound_comments(stmt),
        )

    #: Maps an augmented-assignment operator to its plain binary-op text,
    #: so `x += 1` can be rebuilt as an ordinary mutation `x = x + 1`
    #: (`target_kind="reassign"`, no `let`/type in codegen) rather than
    #: needing a dedicated compound-assignment codegen path -- that's a
    #: reasonable Milestone 3 refinement, not required to keep the
    #: pipeline working end-to-end for Milestone 1.
    _AUG_OPS = {
        cst.AddAssign: "+",
        cst.SubtractAssign: "-",
        cst.MultiplyAssign: "*",
        cst.DivideAssign: "/",
        cst.ModuloAssign: "%",
    }

    def _build_simple_stmt_line(
        self, node: cst.SimpleStatementLine, sibling_body: list[cst.BaseStatement]
    ) -> schema.Stmt:
        comments = self._simple_stmt_comments(node)
        small = node.body[0]
        if isinstance(small, cst.Assign):
            return self._build_assign(small.targets[0].target, None, small.value, comments)
        if isinstance(small, cst.AnnAssign):
            if small.value is None:
                # Bare declaration with no value (`x: int`) -- not part of
                # the v2 MVP subset (nothing to initialize with in Rust).
                return schema.UnsupportedStmt(
                    source_text=self._source_text(node),
                    reason="bare annotated declaration with no value is not part of the v2 MVP subset",
                    comments=comments,
                )
            return self._build_assign(small.target, small.annotation, small.value, comments)
        if isinstance(small, cst.AugAssign):
            return self._build_aug_assign(small, comments)
        if isinstance(small, cst.Return):
            value = self.build_expr(small.value) if small.value is not None else None
            return schema.ReturnStmt(value=value, comments=comments)
        if isinstance(small, cst.Expr):
            return schema.ExprStmt(value=self.build_expr(small.value), comments=comments)
        if isinstance(small, cst.Pass):
            return schema.PassStmt(comments=comments)
        if isinstance(small, cst.Raise):
            message = self.build_expr(small.exc) if small.exc is not None else None
            return schema.RaiseStmt(message=message, comments=comments)
        return schema.UnsupportedStmt(
            source_text=self._source_text(node),
            reason=f"'{type(small).__name__}' is not part of the v2 MVP subset",
            comments=comments,
        )

    def _build_assign(
        self,
        target: cst.BaseExpression,
        annotation: cst.Annotation | None,
        value: cst.BaseExpression,
        comments: schema.Comments,
    ) -> schema.Stmt:
        if isinstance(target, (cst.Tuple, cst.List)):
            # Preflight already hard-rejects this; this is only a defensive
            # fallback for callers that build IR without running preflight
            # first (e.g. a unit test targeting the builder in isolation).
            return schema.UnsupportedStmt(
                source_text=self._source_text(target),
                reason="tuple/multi-target assignment is not yet supported",
                comments=comments,
            )
        if (
            isinstance(target, cst.Attribute)
            and isinstance(target.value, cst.Name)
            and target.value.value == "self"
        ):
            attr_name = target.attr.value
            resolved = self._resolver.resolve_assignment(attr_name, annotation, value, is_self_attr=True)
            return schema.AssignStmt(
                target=f"self.{attr_name}",
                value=self.build_expr(value),
                type=resolved,
                target_kind="self_attr",
                comments=comments,
            )
        target_name = target.value if isinstance(target, cst.Name) else self._source_text(target)
        resolved = self._resolver.resolve_assignment(target_name, annotation, value)
        return schema.AssignStmt(
            target=target_name,
            value=self.build_expr(value),
            type=resolved,
            comments=comments,
        )

    def _build_aug_assign(self, node: cst.AugAssign, comments: schema.Comments) -> schema.Stmt:
        target = node.target
        op = self._AUG_OPS.get(type(node.operator), "?")
        if (
            isinstance(target, cst.Attribute)
            and isinstance(target.value, cst.Name)
            and target.value.value == "self"
        ):
            attr_name = target.attr.value
            resolved = self._resolver.resolve_assignment(
                attr_name, None, node.value, is_self_attr=True, is_aug_assign=True
            )
            new_value = schema.BinOpExpr(
                op=op,
                left=schema.AttributeExpr(value=schema.NameExpr(name="self"), attr=attr_name),
                right=self.build_expr(node.value),
            )
            return schema.AssignStmt(
                target=f"self.{attr_name}",
                value=new_value,
                type=resolved,
                target_kind="self_attr",
                comments=comments,
            )
        target_name = target.value if isinstance(target, cst.Name) else self._source_text(target)
        resolved = self._resolver.resolve_assignment(target_name, None, node.value, is_aug_assign=True)
        new_value = schema.BinOpExpr(op=op, left=schema.NameExpr(name=target_name), right=self.build_expr(node.value))
        # target_kind is deliberately left as the default "name" here (not
        # "reassign") so this flows through apply_mutability's usual
        # count-and-mark pass -- the same pass that turns a repeated plain
        # `t = t + i` accumulator into `let mut` + later `reassign`. `x +=
        # 1` is exactly that pattern with different surface syntax, and
        # should be treated identically rather than needing its own
        # mutability bookkeeping.
        return schema.AssignStmt(
            target=target_name,
            value=new_value,
            type=resolved,
            comments=comments,
        )

    def build_block(self, body: cst.BaseSuite) -> list[schema.Stmt]:
        if not isinstance(body, cst.IndentedBlock):
            return []
        statements = list(body.body)
        return [self.build_stmt(s, statements) for s in statements]

    # -- top level -------------------------------------------------------

    def build_function(self, node: cst.FunctionDef) -> schema.FunctionDefNode:
        params = []
        for i, p in enumerate(node.params.params):
            if i == 0 and p.name.value == "self":
                # Rust methods take &self / &mut self implicitly; not a
                # regular typed parameter. See codegen for the self-vs-
                # mut-self heuristic.
                continue
            # Mandatory hint -- preflight has already hard-rejected a
            # missing one, so this should never raise in normal pipeline
            # use. See TypeResolver.resolve_param.
            p_type = self._resolver.resolve_param(p.name.value, p.annotation)
            params.append(schema.Param(name=p.name.value, type=p_type))

        # v2: return type is always an explicit, mandatory hint -- no more
        # inferring it from `return` statements like v1 did.
        return_type = self._resolver.resolve_return(node.name.value, node.returns)

        body = self.build_block(node.body)
        apply_mutability(body)

        return schema.FunctionDefNode(
            node_id=_next_id("fn"),
            name=node.name.value,
            params=params,
            return_type=return_type,
            body=body,
            source_span=self._span(node),
            comments=self._compound_comments(node),
        )

    def build_class(self, node: cst.ClassDef) -> schema.ClassDefNode:
        unsupported_bases = [self._source_text(b.value) for b in node.bases]
        fields: list[schema.ClassFieldNode] = []
        methods: list[schema.FunctionDefNode] = []

        body = node.body.body if isinstance(node.body, cst.IndentedBlock) else []

        # First pass: find __init__ (if any) and register its params/fields
        # with the shared resolver, so the second pass can let other
        # methods' `self.x = ...` reuse those types (looked up under the
        # "self.<attr>" key) instead of re-deriving anything.
        for member in body:
            if isinstance(member, cst.FunctionDef) and member.name.value == "__init__":
                fields = self._fields_from_init(member)
                break

        for member in body:
            if isinstance(member, cst.FunctionDef) and member.name.value != "__init__":
                methods.append(self.build_function(member))

        class_ir = schema.ClassDefNode(
            node_id=_next_id("class"),
            name=node.name.value,
            fields=fields,
            methods=methods,
            source_span=self._span(node),
            comments=self._compound_comments(node),
            unsupported_bases=unsupported_bases,
            ambiguity=ambiguity.mark_class_shape(node.name.value),
        )
        return class_ir

    def _fields_from_init(self, init: cst.FunctionDef) -> list[schema.ClassFieldNode]:
        fields: list[schema.ClassFieldNode] = []

        # Register __init__'s params with the shared resolver -- mandatory
        # hints, same as any other function. This is what lets the very
        # common `self.x = x` passthrough below (and `_derive_from_expr`
        # for a `self.x = [x]`-style wrap) resolve without a redundant
        # hint on the field assignment itself.
        for p in init.params.params:
            if p.name.value == "self":
                continue
            self._resolver.resolve_param(p.name.value, p.annotation)

        if not isinstance(init.body, cst.IndentedBlock):
            return fields

        for stmt in init.body.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for small in stmt.body:
                target: cst.BaseExpression | None = None
                annotation: cst.Annotation | None = None
                value: cst.BaseExpression | None = None
                if isinstance(small, cst.Assign):
                    target = small.targets[0].target
                    value = small.value
                elif isinstance(small, cst.AnnAssign) and small.value is not None:
                    target = small.target
                    annotation = small.annotation
                    value = small.value
                else:
                    continue

                if (
                    isinstance(target, cst.Attribute)
                    and isinstance(target.value, cst.Name)
                    and target.value.value == "self"
                ):
                    field_type = self._resolver.resolve_assignment(
                        target.attr.value, annotation, value, is_self_attr=True
                    )
                    fields.append(schema.ClassFieldNode(name=target.attr.value, type=field_type))
        return fields

    def build_module(self) -> schema.ModuleNode:
        body: list[schema.TopLevel] = []
        for stmt in self._module.body:
            if isinstance(stmt, cst.FunctionDef):
                body.append(self.build_function(stmt))
            elif isinstance(stmt, cst.ClassDef):
                body.append(self.build_class(stmt))
            elif isinstance(stmt, cst.SimpleStatementLine):
                body.extend(self._build_top_level_simple(stmt))
            else:
                body.append(
                    schema.UnsupportedStmt(
                        source_text=self._source_text(stmt),
                        reason=f"top-level '{type(stmt).__name__}' is not part of the v1 core subset",
                    )
                )
        return schema.ModuleNode(
            schema_version=schema.SCHEMA_VERSION,
            source_file=self._source_file,
            body=body,
        )

    def _build_top_level_simple(self, node: cst.SimpleStatementLine) -> list[schema.TopLevel]:
        results: list[schema.TopLevel] = []
        for small in node.body:
            if isinstance(small, cst.Import):
                for alias in small.names:
                    module_name = self._source_text(alias.name)
                    as_name = alias.asname.name.value if alias.asname else None
                    results.append(
                        schema.ImportNode(module=module_name, alias=as_name, source_span=self._span(node))
                    )
            else:
                results.append(
                    schema.UnsupportedStmt(
                        source_text=self._source_text(node),
                        reason=f"top-level '{type(small).__name__}' is not part of the v1 core subset",
                    )
                )
        return results


def apply_mutability(body: list[schema.Stmt]) -> None:
    """Turn a name assigned more than once into ``let mut`` + reassignment.

    Without this pass, a loop accumulator like ``total = total + i``
    inside a ``for`` loop would re-emit ``let total: ... = ...`` on every
    textual occurrence, which shadows rather than mutates and silently
    produces the wrong Rust semantics for the classic accumulator pattern.

    This is a flat, order-based heuristic, not real scope analysis: it
    assumes a name reassigned anywhere in the function refers to the same
    binding. That's true for the common cases this prototype targets
    (accumulators, running totals) but could misfire for two
    independently-scoped variables in different ``if``/``else`` branches
    that happen to share a name. A real per-block scope resolver is a
    natural next step (see ``ARCHITECTURE.md``).
    """

    counts: dict[str, int] = {}

    def _count(stmts: list[schema.Stmt]) -> None:
        for s in stmts:
            if isinstance(s, schema.AssignStmt) and s.target_kind == "name":
                counts[s.target] = counts.get(s.target, 0) + 1
            if isinstance(s, schema.IfStmt):
                _count(s.body)
                _count(s.orelse)
            elif isinstance(s, (schema.WhileStmt, schema.ForStmt)):
                _count(s.body)

    _count(body)

    seen: set[str] = set()

    def _mark(stmts: list[schema.Stmt]) -> None:
        for s in stmts:
            if isinstance(s, schema.AssignStmt) and s.target_kind == "name":
                if counts.get(s.target, 0) > 1:
                    s.mutable = True
                if s.target in seen:
                    s.target_kind = "reassign"
                else:
                    seen.add(s.target)
            if isinstance(s, schema.IfStmt):
                _mark(s.body)
                _mark(s.orelse)
            elif isinstance(s, (schema.WhileStmt, schema.ForStmt)):
                _mark(s.body)

    _mark(body)


def build_module_ir(source: str, source_file: str) -> schema.ModuleNode:
    """Convenience entry point: parse ``source`` and build its IR in one call."""

    return IRBuilder(source, source_file).build_module()


def apply_collection_ambiguities(module: schema.ModuleNode) -> None:
    """Walk a built module and attach collection-type ambiguity markers.

    Separate from :meth:`IRBuilder.build_module` so the "mark ambiguities"
    stage stays a distinct, independently testable pipeline step (stage 4
    in ``ARCHITECTURE.md``), even though in this prototype it's cheap
    enough to run as a follow-up walk rather than a full IR-to-IR pass.
    """

    def _mark_type_slot(slot: schema.TypeSlot) -> schema.TypeSlot:
        return slot

    def _walk_stmt(stmt: schema.Stmt) -> None:
        if isinstance(stmt, schema.AssignStmt) and isinstance(stmt.type, schema.ConcreteType):
            marker = ambiguity.mark_collection_type(stmt.type)
            if marker is not None and stmt.comments.trailing == []:
                stmt.comments.trailing.append(
                    schema.Comment(text=f"AMBIGUOUS[{marker.category}]: {marker.rationale}")
                )
        elif isinstance(stmt, schema.RaiseStmt):
            hint = "..."
            marker = ambiguity.mark_raise(hint)
            stmt.comments.leading.append(
                schema.Comment(text=f"AMBIGUOUS[{marker.category}]: {marker.rationale}")
            )
        elif isinstance(stmt, schema.ForStmt):
            marker = ambiguity.mark_for_loop(stmt.iter_kind)
            if marker is not None:
                stmt.comments.leading.append(
                    schema.Comment(text=f"AMBIGUOUS[{marker.category}]: {marker.rationale}")
                )
            for s in stmt.body:
                _walk_stmt(s)
        elif isinstance(stmt, schema.IfStmt):
            for s in stmt.body:
                _walk_stmt(s)
            for s in stmt.orelse:
                _walk_stmt(s)
        elif isinstance(stmt, schema.WhileStmt):
            for s in stmt.body:
                _walk_stmt(s)

    for top in module.body:
        if isinstance(top, schema.FunctionDefNode):
            for s in top.body:
                _walk_stmt(s)
        elif isinstance(top, schema.ClassDefNode):
            for m in top.methods:
                for s in m.body:
                    _walk_stmt(s)

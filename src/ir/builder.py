"""Stages 1-4: parse Python source, build the IR, mark ambiguities."""

from __future__ import annotations

import itertools

import libcst as cst
from libcst.metadata import CodeRange, PositionProvider

from ambiguity import resolver as ambiguity
from directives import parser as directive_parser
from ir import schema
from ownership import resolver as ownership
from typing_inference import infer

_node_counter = itertools.count(1)


def reset_node_counter() -> None:
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
    def __init__(self, source: str, source_file: str) -> None:
        self._source_file = source_file
        wrapper = cst.MetadataWrapper(cst.parse_module(source))
        self._positions: dict[cst.CSTNode, CodeRange] = wrapper.resolve(PositionProvider)
        self._module = wrapper.module
        self._current_field_types: dict[str, schema.TypeSlot] = {}

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
        leading_lines = getattr(node, "leading_lines", ())
        return schema.Comments(leading=self._leading_comments(leading_lines))

    def _directive_from_comment_text(self, text: str | None) -> schema.Directive | None:
        if text is None:
            return None
        return directive_parser.parse_directive_text(text)

    def _assignment_directive(
        self, trailing_whitespace: cst.TrailingWhitespace | None
    ) -> schema.Directive | None:
        if trailing_whitespace is None or trailing_whitespace.comment is None:
            return None
        return self._directive_from_comment_text(trailing_whitespace.comment.value)

    def _param_directive(self, param: cst.Param) -> schema.Directive | None:
        comma = param.comma
        if not isinstance(comma, cst.Comma):
            return None
        ws_after = comma.whitespace_after
        first_line = getattr(ws_after, "first_line", None)
        if first_line is None or first_line.comment is None:
            return None
        return self._directive_from_comment_text(first_line.comment.value)

    def _return_type_directive(self, body: cst.BaseSuite) -> schema.Directive | None:
        if not isinstance(body, cst.IndentedBlock):
            return None
        header = body.header
        if header is None or header.comment is None:
            return None
        return self._directive_from_comment_text(header.comment.value)

    def build_expr(self, node: cst.BaseExpression) -> schema.Expr:
        if isinstance(node, cst.Integer):
            return schema.ConstantExpr(value=int(node.value, 0), py_type="int")
        if isinstance(node, cst.Float):
            return schema.ConstantExpr(value=float(node.value), py_type="float")
        if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
            evaluated = node.evaluated_value
            if isinstance(evaluated, bytes):
                return schema.NameExpr(name=f"/* unrecognized: {self._source_text(node)} */")
            return schema.ConstantExpr(value=evaluated, py_type="str")
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
        return schema.NameExpr(name=f"/* unrecognized: {self._source_text(node)} */")

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
        return schema.ForStmt(
            target=target_name,
            iter=self.build_expr(iter_expr),
            iter_kind=iter_kind,
            body=self.build_block(stmt.body),
            comments=self._compound_comments(stmt),
        )

    def _build_simple_stmt_line(
        self, node: cst.SimpleStatementLine, sibling_body: list[cst.BaseStatement]
    ) -> schema.Stmt:
        comments = self._simple_stmt_comments(node)
        small = node.body[0]
        if isinstance(small, cst.Assign):
            directive = self._assignment_directive(node.trailing_whitespace)
            if directive is not None:
                comments.trailing = []
            target = small.targets[0].target
            if (
                isinstance(target, cst.Attribute)
                and isinstance(target.value, cst.Name)
                and target.value.value == "self"
            ):
                attr_name = target.attr.value
                inferred = self._current_field_types.get(attr_name) or infer.infer_assignment_type(
                    attr_name, small.value, sibling_body
                )
                inferred_own_value, inferred_own_evidence = ownership.infer_assignment_ownership(
                    f"self.{attr_name}"
                )
                own_decision = ownership.resolve_ownership(
                    directive, inferred_own_value, inferred_own_evidence
                )
                return schema.AssignStmt(
                    target=f"self.{attr_name}",
                    value=self.build_expr(small.value),
                    type=inferred,
                    target_kind="self_attr",
                    comments=comments,
                    ownership=own_decision,
                )
            target_name = target.value if isinstance(target, cst.Name) else self._source_text(target)
            inferred = infer.infer_assignment_type(target_name, small.value, sibling_body)
            inferred_own_value, inferred_own_evidence = ownership.infer_assignment_ownership(
                target_name
            )
            own_decision = ownership.resolve_ownership(
                directive, inferred_own_value, inferred_own_evidence
            )
            return schema.AssignStmt(
                target=target_name,
                value=self.build_expr(small.value),
                type=inferred,
                comments=comments,
                ownership=own_decision,
            )
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
            reason=f"'{type(small).__name__}' is not part of the v1 core subset",
            comments=comments,
        )

    def build_block(self, body: cst.BaseSuite) -> list[schema.Stmt]:
        if not isinstance(body, cst.IndentedBlock):
            return []
        statements = list(body.body)
        return [self.build_stmt(s, statements) for s in statements]

    def build_function(self, node: cst.FunctionDef) -> schema.FunctionDefNode:
        body_stmts = list(node.body.body) if isinstance(node.body, cst.IndentedBlock) else []

        params = []
        param_type_lookup: dict[str, schema.TypeSlot] = {}
        for i, p in enumerate(node.params.params):
            if i == 0 and p.name.value == "self":
                continue
            annotated = infer.type_from_annotation(p.annotation)
            if annotated is not None:
                p_type = annotated
            else:
                p_type = infer.new_hole(["no type hint; not yet inferred from call sites"])

            directive = self._param_directive(p)
            inferred_value, inferred_evidence = ownership.infer_param_ownership(
                p.name.value, body_stmts, p_type
            )
            own_decision = ownership.resolve_ownership(directive, inferred_value, inferred_evidence)

            params.append(schema.Param(name=p.name.value, type=p_type, ownership=own_decision))
            param_type_lookup[p.name.value] = p_type

        explicit_return = infer.type_from_annotation(node.returns)
        if explicit_return is not None:
            return_type = explicit_return
        else:
            return_type = infer.infer_return_type(
                body_stmts,
                param_type_lookup,
                self._current_field_types,
            )

        return_directive = self._return_type_directive(node.body)
        return_texts = self._collect_return_texts(body_stmts)
        param_ownership_lookup = {
            p.name: p.ownership.value for p in params if p.ownership is not None
        }
        inferred_return_value, inferred_return_evidence = ownership.infer_return_ownership(
            return_texts, param_ownership_lookup
        )
        return_ownership = ownership.resolve_ownership(
            return_directive, inferred_return_value, inferred_return_evidence
        )

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
            return_ownership=return_ownership,
        )

    def _collect_return_texts(self, body: list[cst.BaseStatement]) -> list[str]:
        texts: list[str] = []

        class _ReturnTextFinder(cst.CSTVisitor):
            def visit_Return(inner_self, node: cst.Return) -> None:
                if node.value is not None:
                    texts.append(self._source_text(node.value))

            def visit_FunctionDef(inner_self, node: cst.FunctionDef) -> bool:
                return False

        for stmt in body:
            stmt.visit(_ReturnTextFinder())
        return texts

    def build_class(self, node: cst.ClassDef) -> schema.ClassDefNode:
        unsupported_bases = [self._source_text(b.value) for b in node.bases]
        fields: list[schema.ClassFieldNode] = []
        methods: list[schema.FunctionDefNode] = []

        body = node.body.body if isinstance(node.body, cst.IndentedBlock) else []

        for member in body:
            if isinstance(member, cst.FunctionDef) and member.name.value == "__init__":
                fields = self._fields_from_init(member)
                break

        self._current_field_types = {f.name: f.type for f in fields}
        try:
            for member in body:
                if isinstance(member, cst.FunctionDef) and member.name.value != "__init__":
                    methods.append(self.build_function(member))
        finally:
            self._current_field_types = {}

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
        if not isinstance(init.body, cst.IndentedBlock):
            return fields

        param_types: dict[str, schema.TypeSlot] = {}
        for p in init.params.params:
            if p.name.value == "self":
                continue
            annotated = infer.type_from_annotation(p.annotation)
            param_types[p.name.value] = annotated or infer.new_hole(
                ["no type hint on constructor parameter"]
            )

        statements = list(init.body.body)
        for stmt in statements:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for small in stmt.body:
                if not isinstance(small, cst.Assign):
                    continue
                target = small.targets[0].target
                if (
                    isinstance(target, cst.Attribute)
                    and isinstance(target.value, cst.Name)
                    and target.value.value == "self"
                ):
                    field_type = self._field_type_with_param_lookup(
                        small.value, param_types, target.attr.value, statements
                    )
                    fields.append(schema.ClassFieldNode(name=target.attr.value, type=field_type))
        return fields

    def _field_type_with_param_lookup(
        self,
        value: cst.BaseExpression,
        param_types: dict[str, schema.TypeSlot],
        field_name: str,
        sibling_body: list[cst.BaseStatement],
    ) -> schema.TypeSlot:
        if isinstance(value, cst.Name) and value.value in param_types:
            return param_types[value.value]

        if isinstance(value, cst.List) and value.elements:
            first = value.elements[0].value
            if isinstance(first, cst.Name) and first.value in param_types:
                elem_type = param_types[first.value]
                if isinstance(elem_type, schema.ConcreteType):
                    return schema.ConcreteType(value=f"Vec<{elem_type.value}>")

        return infer.infer_assignment_type(field_name, value, sibling_body)

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
    return IRBuilder(source, source_file).build_module()


def apply_collection_ambiguities(module: schema.ModuleNode) -> None:
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

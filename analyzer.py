"""Generate the file graph consumed by the Codebase Archaeologist UI."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, NamedTuple

from repository_loader import (
    RepositoryLoadError,
    cleanup_repository,
    load_repository,
    resolve_commit_sha,
    validate_github_url,
)


IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "build",
    "dist",
    ".eggs",
    "site-packages",
}

MAX_SOURCE_BYTES = 200 * 1024  # 200 KB per file, applied to the encoded UTF-8 text
MAX_PYTHON_FILES = 2000  # hard cap on how many discovered files a single run will analyze
MAX_FLOW_DEPTH = 8
MAX_REPRESENTATIVE_FLOWS = 3
MAX_FLOW_CANDIDATES_PER_ENTRYPOINT = 12
MAX_FLOW_CANDIDATES = 500
LARGE_SYMBOL_LINES = 80
HIGH_SYMBOL_FAN_IN = 8
HIGH_SYMBOL_FAN_OUT = 8
FASTAPI_ROUTE_METHODS = {
    "get", "post", "put", "patch", "delete", "options", "head", "websocket"
}


def find_python_files(repo_root: Path) -> list[Path]:
    """Return Python files under repo_root, excluding generated/vendor folders."""
    return [
        path
        for path in repo_root.rglob("*.py")
        if not any(part in IGNORED_DIR_NAMES for part in path.relative_to(repo_root).parts)
    ]


def read_source(path: Path) -> tuple[str | None, bool, str | None]:
    """Read a file's source as UTF-8, truncated to MAX_SOURCE_BYTES.

    Returns (source, truncated, error):
    - On success: (text, was_it_truncated, None)
    - On failure (unreadable or not valid UTF-8): (None, False, error_message)

    Truncation happens on the encoded bytes (so the 200 KB limit is exact),
    but is applied to already-decoded text and re-cut on a UTF-8 boundary so
    a mid-character cut can never masquerade as a genuine decoding failure.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, False, f"could not read file: {exc}"

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, False, f"could not decode as UTF-8: {exc}"

    if len(raw) <= MAX_SOURCE_BYTES:
        return text, False, None

    truncated_text = raw[:MAX_SOURCE_BYTES].decode("utf-8", errors="ignore")
    return truncated_text, True, None


def build_file_nodes(repo_root: Path, python_files: list[Path]) -> list[dict[str, Any]]:
    """Build stable file nodes using repository-relative paths as identifiers.

    Every node includes its source text (UTF-8, capped at MAX_SOURCE_BYTES,
    with source_truncated indicating whether it was cut short). Files that
    can't be read or decoded get a source_error instead of a source, rather
    than crashing the whole analysis run.
    """
    nodes: list[dict[str, Any]] = []
    for path in sorted(python_files):
        relative_path = path.relative_to(repo_root).as_posix()
        node: dict[str, Any] = {
            "id": f"file:{relative_path}",
            "kind": "file",
            "path": relative_path,
            "size_bytes": path.stat().st_size,
        }

        source, truncated, error = read_source(path)
        if error is not None:
            node["source_error"] = error
        else:
            node["source"] = source
            node["source_truncated"] = truncated

        nodes.append(node)
    return nodes


# --------------------------------------------------------------------------
# Symbol extraction
# --------------------------------------------------------------------------

def module_qualified_name(relative_path: Path) -> str:
    """Return a stable dotted module name for a repository-relative Python file."""
    parts = list(relative_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or relative_path.stem


def symbol_start_line(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Include decorators in a symbol's exact displayed source range."""
    decorator_lines = [decorator.lineno for decorator in node.decorator_list]
    return min([node.lineno, *decorator_lines])


class SymbolVisitor(ast.NodeVisitor):
    """Extract nested class, function, and method nodes with containment edges."""

    def __init__(self, relative_path: Path, module_name: str) -> None:
        self.path = relative_path.as_posix()
        self.module_name = module_name
        self.parents: list[tuple[str, str, str]] = []
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []

    def _visit_symbol(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: str,
    ) -> None:
        local_parts = [parent[0] for parent in self.parents] + [node.name]
        qualified_name = ".".join([self.module_name, *local_parts])
        start_line = symbol_start_line(node)
        end_line = getattr(node, "end_lineno", None) or node.lineno
        symbol_id = f"symbol:{self.path}:{qualified_name}:{node.lineno}"
        parent_id = self.parents[-1][1] if self.parents else f"file:{self.path}"
        decorators = [ast.unparse(decorator) for decorator in node.decorator_list]

        symbol: dict[str, Any] = {
            "id": symbol_id,
            "kind": kind,
            "name": node.name,
            "qualified_name": qualified_name,
            "module": self.module_name,
            "path": self.path,
            "start_line": start_line,
            "definition_line": node.lineno,
            "end_line": end_line,
            "parent_id": parent_id,
            "decorators": decorators,
        }
        if isinstance(node, ast.ClassDef):
            symbol["bases"] = [ast.unparse(base) for base in node.bases]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol["is_async"] = isinstance(node, ast.AsyncFunctionDef)
        docstring = ast.get_docstring(node, clean=True)
        if docstring:
            symbol["docstring"] = docstring
        self.nodes.append(symbol)
        self.edges.append(
            {
                "id": f"contains:{parent_id}->{symbol_id}",
                "source": parent_id,
                "target": symbol_id,
                "kind": "contains",
                "confidence": 1.0,
                "resolution_method": "ast-static",
                "evidence": {
                    "path": self.path,
                    "line": start_line,
                    "end_line": end_line,
                },
            }
        )

        self.parents.append((node.name, symbol_id, kind))
        self.generic_visit(node)
        self.parents.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_symbol(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        direct_parent_kind = self.parents[-1][2] if self.parents else None
        self._visit_symbol(node, "method" if direct_parent_kind == "class" else "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        direct_parent_kind = self.parents[-1][2] if self.parents else None
        self._visit_symbol(node, "method" if direct_parent_kind == "class" else "function")


def extract_symbol_graph(
    repo_root: Path,
    python_files: list[Path],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Return symbol nodes, containment edges, and the number of parse failures."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    parse_failures = 0
    roots = find_module_roots(repo_root, python_files)

    for path in sorted(python_files):
        relative_path = path.relative_to(repo_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            parse_failures += 1
            continue

        module_candidates = [
            (len(root.parts), dotted)
            for root in roots
            if (dotted := path_to_dotted(path, root)) is not None
        ]
        module_name = max(module_candidates, default=(0, module_qualified_name(relative_path)))[1]
        visitor = SymbolVisitor(relative_path, module_name)
        visitor.visit(tree)
        nodes.extend(visitor.nodes)
        edges.extend(visitor.edges)

    return nodes, edges, parse_failures


# --------------------------------------------------------------------------
# Import extraction
# --------------------------------------------------------------------------

class ImportStatement(NamedTuple):
    """A single import statement extracted from a module's AST."""

    module: str | None  # dotted module named in "import X" or "from X import ..."; None for "from . import x"
    level: int  # 0 = absolute; 1+ = number of leading dots in a relative import
    names: list[str]  # imported names ("os.path" for `import os.path`; "y" for `from x import y`)
    lineno: int


def extract_import_statements(path: Path) -> list[ImportStatement]:
    """Parse a file's AST and return every import/from-import statement it contains."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    statements: list[ImportStatement] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            statements.append(
                ImportStatement(
                    module=None,
                    level=0,
                    names=[alias.name for alias in node.names],
                    lineno=node.lineno,
                )
            )
        elif isinstance(node, ast.ImportFrom):
            statements.append(
                ImportStatement(
                    module=node.module,
                    level=node.level,
                    names=[alias.name for alias in node.names],
                    lineno=node.lineno,
                )
            )
    return statements


# --------------------------------------------------------------------------
# Module resolution
# --------------------------------------------------------------------------

def find_module_roots(repo_root: Path, python_files: list[Path]) -> list[Path]:
    """Find every directory Python import statements are likely resolved against.

    Always includes repo_root itself (flat layout). Also includes any "src"
    directory found in the tree, since src-layout packages are imported
    relative to "src", not relative to the repo root.
    """
    roots = {repo_root}
    for path in python_files:
        for parent in path.parents:
            if parent == repo_root:
                break
            if parent.name == "src":
                roots.add(parent)
    return sorted(roots)


def path_to_dotted(path: Path, root: Path) -> str | None:
    """Convert a file path to its dotted module name relative to a root, or None if unrelated."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def build_module_map(repo_root: Path, python_files: list[Path], roots: list[Path]) -> dict[str, str]:
    """Map every resolvable dotted module name to its file node id."""
    module_map: dict[str, str] = {}
    for path in python_files:
        relative_path = path.relative_to(repo_root).as_posix()
        node_id = f"file:{relative_path}"
        for root in roots:
            dotted = path_to_dotted(path, root)
            if dotted is not None:
                # First match wins if a name is ambiguous across roots.
                module_map.setdefault(dotted, node_id)
    return module_map


def relative_base(importer_rel_path: Path, level: int) -> str:
    """Compute the dotted package name a relative import (level >= 1) is anchored to."""
    package_parts = list(importer_rel_path.parent.parts)
    # level=1 means "this package" (the importer's own directory).
    climb = level - 1
    if climb > 0:
        package_parts = package_parts[:-climb] if climb <= len(package_parts) else []
    return ".".join(package_parts)


def candidate_targets(stmt: ImportStatement, importer_rel_path: Path) -> list[list[str]]:
    """For each imported name in a statement, return ranked (specific -> general) module-name candidates."""
    per_name_candidates: list[list[str]] = []

    if stmt.level == 0 and stmt.module is None:
        # Plain "import a.b.c[, d.e]" - each name is already a full dotted path.
        for name in stmt.names:
            per_name_candidates.append([name])
        return per_name_candidates

    if stmt.level == 0:
        # "from base import name1, name2"
        base = stmt.module or ""
        for name in stmt.names:
            per_name_candidates.append([f"{base}.{name}", base] if base else [name])
        return per_name_candidates

    # Relative import: "from . import x" / "from .pkg import y" / "from .. import z"
    base = relative_base(importer_rel_path, stmt.level)
    if stmt.module:
        full_base = f"{base}.{stmt.module}" if base else stmt.module
        for name in stmt.names:
            per_name_candidates.append([f"{full_base}.{name}", full_base])
    else:
        for name in stmt.names:
            per_name_candidates.append([f"{base}.{name}" if base else name])
    return per_name_candidates


def resolve_first(candidates: list[str], module_map: dict[str, str]) -> str | None:
    for candidate in candidates:
        target_id = module_map.get(candidate)
        if target_id is not None:
            return target_id
    return None


# --------------------------------------------------------------------------
# Symbol relationship resolution
# --------------------------------------------------------------------------

def dotted_expression(node: ast.AST) -> str | None:
    """Return a dotted name for Name/Attribute expressions, or None when dynamic."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_expression(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def import_bindings(tree: ast.Module, module_name: str, is_package: bool) -> dict[str, str]:
    """Map names introduced by module-level imports to their absolute dotted targets."""
    bindings: dict[str, str] = {}
    package = module_name if is_package else module_name.rpartition(".")[0]

    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                local_name = alias.asname or alias.name.split(".")[0]
                bindings[local_name] = alias.name if alias.asname else local_name
        elif isinstance(statement, ast.ImportFrom):
            package_parts = package.split(".") if package else []
            if statement.level:
                climb = statement.level - 1
                if climb:
                    package_parts = package_parts[:-climb]
                base = ".".join(package_parts)
            else:
                base = ""
            module_parts = [part for part in (base, statement.module or "") if part]
            imported_from = ".".join(module_parts)
            for alias in statement.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                bindings[local_name] = ".".join(
                    part for part in (imported_from, alias.name) if part
                )
    return bindings


def expand_bound_name(name: str, bindings: dict[str, str]) -> str:
    first, separator, remainder = name.partition(".")
    bound = bindings.get(first)
    if not bound:
        return name
    return f"{bound}.{remainder}" if separator else bound


def exact_symbol_target(
    expression: str,
    source_symbol: dict[str, Any],
    bindings: dict[str, str],
    qualified_index: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None, float]:
    """Resolve lexical, imported, qualified, and self/cls symbol references."""
    candidates: list[tuple[str, str, float]] = []
    module_name = source_symbol["module"]

    if expression.startswith(("self.", "cls.")) and source_symbol["kind"] == "method":
        owner = source_symbol["qualified_name"].rsplit(".", 1)[0]
        candidates.append(
            (f"{owner}.{expression.split('.', 1)[1]}", "ast-self-method", 0.98)
        )

    expanded = expand_bound_name(expression, bindings)
    if expanded != expression:
        candidates.append((expanded, "ast-import-binding", 0.96))
    candidates.append((expression, "ast-qualified-name", 1.0))

    if "." not in expression:
        parent_scope = source_symbol["qualified_name"].rsplit(".", 1)[0]
        candidates.extend(
            [
                (f"{source_symbol['qualified_name']}.{expression}", "ast-nested-scope", 1.0),
                (f"{parent_scope}.{expression}", "ast-lexical-scope", 1.0),
                (f"{module_name}.{expression}", "ast-module-scope", 1.0),
            ]
        )

    for candidate, method, confidence in candidates:
        target = qualified_index.get(candidate)
        if target is not None:
            return target, method, confidence
    return None, None, 0.0


class DirectCallVisitor(ast.NodeVisitor):
    """Collect calls belonging to one function without entering nested definitions."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def direct_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    visitor = DirectCallVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.calls


def fastapi_instances(tree: ast.Module, bindings: dict[str, str]) -> set[str]:
    """Return module-level names assigned a FastAPI application or router."""
    instances: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        if not isinstance(value, ast.Call):
            continue
        constructor = dotted_expression(value.func)
        if not constructor:
            continue
        expanded = expand_bound_name(constructor, bindings)
        if expanded not in {"fastapi.FastAPI", "fastapi.APIRouter"}:
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        instances.update(target.id for target in targets if isinstance(target, ast.Name))
    return instances


def fastapi_route_metadata(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    instances: set[str],
) -> tuple[dict[str, Any] | None, ast.AST | None]:
    """Recognize routes registered on proven FastAPI app/router instances."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        expression = dotted_expression(decorator.func)
        if not expression or "." not in expression:
            continue
        owner, method = expression.rsplit(".", 1)
        if owner not in instances or method not in FASTAPI_ROUTE_METHODS:
            continue
        route_path = None
        if decorator.args and isinstance(decorator.args[0], ast.Constant):
            if isinstance(decorator.args[0].value, str):
                route_path = decorator.args[0].value
        label = f"{method.upper()} {route_path or '(dynamic path)'}"
        return (
            {
                "framework": "fastapi",
                "kind": "route",
                "method": method.upper(),
                "route_path": route_path,
                "label": label,
            },
            decorator,
        )
    return None, None


def function_defaults(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.expr]:
    """Return every positional and keyword-only parameter default."""
    return [
        *node.args.defaults,
        *(default for default in node.args.kw_defaults if default is not None),
    ]


def fastapi_dependency_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    bindings: dict[str, str],
) -> list[ast.Call]:
    """Return Depends(...) calls used in defaults or modern Annotated parameters."""
    dependencies: list[ast.Call] = []
    parameter_annotations = [
        argument.annotation
        for argument in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        if argument.annotation is not None
    ]
    seen: set[tuple[int, int]] = set()
    for expression_root in [*function_defaults(node), *parameter_annotations]:
        for candidate in ast.walk(expression_root):
            if not isinstance(candidate, ast.Call):
                continue
            expression = dotted_expression(candidate.func)
            if expression and expand_bound_name(expression, bindings) == "fastapi.Depends":
                location = (candidate.lineno, candidate.col_offset)
                if location in seen:
                    continue
                seen.add(location)
                dependencies.append(candidate)
    return dependencies


def assignment_value(
    statement: ast.Assign | ast.AnnAssign,
) -> ast.expr | None:
    return statement.value


def class_sqlalchemy_metadata(
    node: ast.ClassDef,
    bindings: dict[str, str],
) -> dict[str, Any]:
    """Extract table, mapped-column, and relationship evidence from one class."""
    table_expression: str | None = None
    table_name: str | None = None
    is_abstract = False
    columns: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    for statement in node.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            value = assignment_value(statement)
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "__tablename__" and value is not None:
                    table_expression = ast.unparse(value)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        table_name = value.value
                if (
                    target.id == "__abstract__"
                    and isinstance(value, ast.Constant)
                    and value.value is True
                ):
                    is_abstract = True

            if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                continue
            target_name = statement.target.id
            annotation = dotted_expression(statement.annotation)
            annotation_root = None
            if isinstance(statement.annotation, ast.Subscript):
                annotation_root = dotted_expression(statement.annotation.value)
            mapped_annotation = annotation_root or annotation
            if not mapped_annotation or expand_bound_name(mapped_annotation, bindings) != "sqlalchemy.orm.Mapped":
                continue
            value = statement.value
            call_name = dotted_expression(value.func) if isinstance(value, ast.Call) else None
            expanded_call = expand_bound_name(call_name, bindings) if call_name else None
            evidence = {
                "name": target_name,
                "line": statement.lineno,
                "annotation": ast.unparse(statement.annotation),
            }
            if expanded_call == "sqlalchemy.orm.relationship":
                relationships.append(evidence)
            else:
                columns.append(evidence)

    return {
        "table_name": table_name,
        "table_expression": table_expression,
        "is_abstract": is_abstract,
        "columns": columns,
        "relationships": relationships,
    }


def enrich_sqlalchemy_models(
    repo_root: Path,
    python_files: list[Path],
    symbol_nodes: list[dict[str, Any]],
    relationship_edges: list[dict[str, Any]],
) -> dict[str, int]:
    """Classify proven SQLAlchemy declarative roots, abstract bases, and models."""
    location_index = {
        (node["path"], node["definition_line"]): node
        for node in symbol_nodes
        if node["kind"] == "class"
    }
    metadata_by_id: dict[str, dict[str, Any]] = {}
    sqlalchemy_ids: set[str] = set()
    declarative_roots: set[str] = set()

    for path in sorted(python_files):
        relative_posix = path.relative_to(repo_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        file_symbols = [node for node in symbol_nodes if node["path"] == relative_posix]
        module_name = file_symbols[0]["module"] if file_symbols else module_qualified_name(path.relative_to(repo_root))
        bindings = import_bindings(tree, module_name, path.name == "__init__.py")
        legacy_bases: set[str] = set()
        for statement in tree.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            value = assignment_value(statement)
            if not isinstance(value, ast.Call):
                continue
            constructor = dotted_expression(value.func)
            if not constructor:
                continue
            expanded = expand_bound_name(constructor, bindings)
            if expanded not in {
                "sqlalchemy.orm.declarative_base",
                "sqlalchemy.ext.declarative.declarative_base",
            }:
                continue
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            legacy_bases.update(target.id for target in targets if isinstance(target, ast.Name))

        for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            symbol = location_index.get((relative_posix, class_node.lineno))
            if symbol is None:
                continue
            metadata = class_sqlalchemy_metadata(class_node, bindings)
            metadata_by_id[symbol["id"]] = metadata
            for base in class_node.bases:
                expression = dotted_expression(base)
                if not expression:
                    continue
                expanded = expand_bound_name(expression, bindings)
                if expanded == "sqlalchemy.orm.DeclarativeBase" or expression in legacy_bases:
                    sqlalchemy_ids.add(symbol["id"])
                    declarative_roots.add(symbol["id"])

    extends_edges = [
        edge for edge in relationship_edges
        if edge["kind"] == "extends" and float(edge.get("confidence", 0.0)) >= 0.9
    ]
    changed = True
    while changed:
        changed = False
        for edge in extends_edges:
            if edge["target"] in sqlalchemy_ids and edge["source"] not in sqlalchemy_ids:
                sqlalchemy_ids.add(edge["source"])
                changed = True

    metrics = {
        "sqlalchemy_models": 0,
        "sqlalchemy_abstract_models": 0,
        "sqlalchemy_columns": 0,
        "sqlalchemy_relationships": 0,
    }
    for symbol in symbol_nodes:
        if symbol["id"] not in sqlalchemy_ids:
            continue
        metadata = metadata_by_id.get(symbol["id"], {
            "table_name": None,
            "table_expression": None,
            "is_abstract": False,
            "columns": [],
            "relationships": [],
        })
        if symbol["id"] in declarative_roots:
            model_kind = "declarative-base"
        elif metadata["is_abstract"]:
            model_kind = "abstract-model"
            metrics["sqlalchemy_abstract_models"] += 1
        else:
            model_kind = "model"
            metrics["sqlalchemy_models"] += 1
        symbol["framework"] = "sqlalchemy"
        symbol["architectural_role"] = "model" if model_kind == "model" else "model-base"
        symbol["sqlalchemy"] = {"kind": model_kind, **metadata}
        metrics["sqlalchemy_columns"] += len(metadata["columns"])
        metrics["sqlalchemy_relationships"] += len(metadata["relationships"])
    return metrics


class DirectAssignmentVisitor(ast.NodeVisitor):
    """Collect assignments owned by one function without entering nested scopes."""

    def __init__(self) -> None:
        self.assignments: list[ast.Assign | ast.AnnAssign | ast.AugAssign] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        self.assignments.append(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.assignments.append(node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.assignments.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def direct_assignments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Assign | ast.AnnAssign | ast.AugAssign]:
    visitor = DirectAssignmentVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.assignments


def unwrap_expression(node: ast.expr | None) -> ast.expr | None:
    while isinstance(node, (ast.Await, ast.Starred)):
        node = node.value
    return node


def sqlalchemy_model_target(
    expression: ast.expr | None,
    source_symbol: dict[str, Any],
    bindings: dict[str, str],
    qualified_index: dict[str, dict[str, Any]],
    model_ids: set[str],
) -> dict[str, Any] | None:
    """Resolve a class or class attribute expression to a proven model node."""
    expression = unwrap_expression(expression)
    dotted = dotted_expression(expression) if expression is not None else None
    while dotted:
        target, _, _ = exact_symbol_target(dotted, source_symbol, bindings, qualified_index)
        if target is not None and target["id"] in model_ids:
            return target
        dotted = dotted.rpartition(".")[0]
    return None


def extract_sqlalchemy_access_edges(
    repo_root: Path,
    python_files: list[Path],
    symbol_nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Extract conservative model reads and writes from common SQLAlchemy operations."""
    edges: list[dict[str, Any]] = []
    qualified_index = {node["qualified_name"]: node for node in symbol_nodes}
    model_ids = {
        node["id"] for node in symbol_nodes
        if node.get("sqlalchemy", {}).get("kind") == "model"
    }
    location_index = {
        (node["path"], node["definition_line"]): node for node in symbol_nodes
    }
    seen: set[tuple[str, str, str, int, int]] = set()
    metrics = {"sqlalchemy_reads": 0, "sqlalchemy_writes": 0}

    def add_edge(
        source: dict[str, Any],
        target: dict[str, Any] | None,
        kind: str,
        evidence_node: ast.AST,
        expression: str,
        path: str,
        method: str,
        confidence: float,
    ) -> None:
        if target is None:
            return
        key = (source["id"], target["id"], kind, evidence_node.lineno, evidence_node.col_offset)
        if key in seen:
            return
        seen.add(key)
        edges.append(
            {
                "id": f"{kind}:{source['id']}->{target['id']}:{evidence_node.lineno}:{evidence_node.col_offset}",
                "source": source["id"],
                "target": target["id"],
                "kind": kind,
                "confidence": confidence,
                "resolution_method": method,
                "evidence": {
                    "path": path,
                    "line": evidence_node.lineno,
                    "column": evidence_node.col_offset,
                    "expression": expression,
                },
            }
        )
        metrics["sqlalchemy_reads" if kind == "reads" else "sqlalchemy_writes"] += 1

    for path in sorted(python_files):
        relative_path = path.relative_to(repo_root)
        relative_posix = relative_path.as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        file_symbols = [node for node in symbol_nodes if node["path"] == relative_posix]
        module_name = file_symbols[0]["module"] if file_symbols else module_qualified_name(relative_path)
        bindings = import_bindings(tree, module_name, path.name == "__init__.py")

        for function_node in (
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            source = location_index.get((relative_posix, function_node.lineno))
            if source is None:
                continue
            variable_models: dict[str, dict[str, Any]] = {}
            assignments = direct_assignments(function_node)
            for statement in assignments:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    target = sqlalchemy_model_target(
                        statement.annotation, source, bindings, qualified_index, model_ids
                    )
                    if target is not None:
                        variable_models[statement.target.id] = target
                value = statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign)) else None
                value = unwrap_expression(value)
                if isinstance(value, ast.Call):
                    target = sqlalchemy_model_target(
                        value.func, source, bindings, qualified_index, model_ids
                    )
                    call_name = dotted_expression(value.func)
                    receiver = call_name.rpartition(".")[0].rsplit(".", 1)[-1].lstrip("_") if call_name else ""
                    is_session_get = (
                        call_name is not None
                        and call_name.endswith(".get")
                        and (receiver in {"session", "db", "database"} or receiver.endswith("session"))
                    )
                    if target is None and is_session_get and value.args:
                        target = sqlalchemy_model_target(
                            value.args[0], source, bindings, qualified_index, model_ids
                        )
                    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                    if target is not None:
                        for assigned in targets:
                            if isinstance(assigned, ast.Name):
                                variable_models[assigned.id] = target

            for call in direct_calls(function_node):
                call_name = dotted_expression(call.func)
                if not call_name:
                    continue
                expanded_call = expand_bound_name(call_name, bindings)
                operation = expanded_call.rsplit(".", 1)[-1]
                receiver = call_name.rpartition(".")[0].rsplit(".", 1)[-1].lstrip("_")
                is_session_operation = receiver in {"session", "db", "database"} or receiver.endswith("session")
                if expanded_call in {"sqlalchemy.select", "sqlalchemy.exists"}:
                    for argument in call.args:
                        add_edge(
                            source,
                            sqlalchemy_model_target(argument, source, bindings, qualified_index, model_ids),
                            "reads", call, ast.unparse(call), relative_posix,
                            f"sqlalchemy-{operation}", 0.98,
                        )
                elif expanded_call in {"sqlalchemy.delete", "sqlalchemy.update", "sqlalchemy.insert"}:
                    for argument in call.args[:1]:
                        add_edge(
                            source,
                            sqlalchemy_model_target(argument, source, bindings, qualified_index, model_ids),
                            "writes", call, ast.unparse(call), relative_posix,
                            f"sqlalchemy-{operation}", 0.98,
                        )
                elif is_session_operation and operation == "get" and call.args:
                    add_edge(
                        source,
                        sqlalchemy_model_target(call.args[0], source, bindings, qualified_index, model_ids),
                        "reads", call, ast.unparse(call), relative_posix,
                        "sqlalchemy-session-get", 0.98,
                    )
                elif is_session_operation and operation in {"add", "merge", "delete"} and call.args:
                    argument = unwrap_expression(call.args[0])
                    target = variable_models.get(argument.id) if isinstance(argument, ast.Name) else None
                    add_edge(
                        source, target, "writes", call, ast.unparse(call), relative_posix,
                        f"sqlalchemy-session-{operation}", 0.9,
                    )

            for statement in assignments:
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for assigned in targets:
                    if not isinstance(assigned, ast.Attribute) or not isinstance(assigned.value, ast.Name):
                        continue
                    add_edge(
                        source,
                        variable_models.get(assigned.value.id),
                        "writes", statement, ast.unparse(statement), relative_posix,
                        "sqlalchemy-model-mutation", 0.9,
                    )
    return edges, metrics


def extract_symbol_relationship_edges(
    repo_root: Path,
    python_files: list[Path],
    symbol_nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Resolve deterministic inheritance/call edges and bounded dispatch candidates."""
    edges: list[dict[str, Any]] = []
    unresolved_sites: list[dict[str, Any]] = []
    qualified_index = {node["qualified_name"]: node for node in symbol_nodes}
    location_index = {
        (node["path"], node["definition_line"]): node for node in symbol_nodes
    }
    simple_index: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbol_nodes:
        simple_index.setdefault(symbol["name"], []).append(symbol)

    metrics = {
        "call_sites": 0,
        "resolved_calls": 0,
        "candidate_calls": 0,
        "unresolved_calls": 0,
        "inheritance_edges": 0,
        "fastapi_routes": 0,
        "dependency_edges": 0,
        "unresolved_dependencies": 0,
    }

    for path in sorted(python_files):
        relative_path = path.relative_to(repo_root)
        relative_posix = relative_path.as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue

        file_symbols = [node for node in symbol_nodes if node["path"] == relative_posix]
        module_name = file_symbols[0]["module"] if file_symbols else module_qualified_name(relative_path)
        bindings = import_bindings(tree, module_name, path.name == "__init__.py")
        route_instances = fastapi_instances(tree, bindings)

        for ast_node in ast.walk(tree):
            if not isinstance(ast_node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            source_symbol = location_index.get((relative_posix, ast_node.lineno))
            if source_symbol is None:
                continue

            if isinstance(ast_node, ast.ClassDef):
                for base in ast_node.bases:
                    expression = dotted_expression(base)
                    if not expression:
                        continue
                    target, method, confidence = exact_symbol_target(
                        expression, source_symbol, bindings, qualified_index
                    )
                    if target is None:
                        matches = [
                            item for item in simple_index.get(expression.rsplit(".", 1)[-1], [])
                            if item["kind"] == "class"
                        ]
                        if len(matches) == 1:
                            target, method, confidence = matches[0], "repo-unique-class-name", 0.7
                    if target is None or target["id"] == source_symbol["id"]:
                        continue
                    edges.append(
                        {
                            "id": f"extends:{source_symbol['id']}->{target['id']}:{base.lineno}",
                            "source": source_symbol["id"],
                            "target": target["id"],
                            "kind": "extends",
                            "confidence": confidence,
                            "resolution_method": method,
                            "evidence": {
                                "path": relative_posix,
                                "line": base.lineno,
                                "column": base.col_offset,
                                "expression": ast.unparse(base),
                            },
                        }
                    )
                    metrics["inheritance_edges"] += 1
                continue

            route, route_decorator = fastapi_route_metadata(ast_node, route_instances)
            if route is not None and route_decorator is not None:
                source_symbol["entrypoint"] = route
                source_symbol["framework"] = "fastapi"
                source_symbol["architectural_role"] = "route"
                source_symbol["entrypoint_evidence"] = {
                    "path": relative_posix,
                    "line": route_decorator.lineno,
                    "column": route_decorator.col_offset,
                    "expression": ast.unparse(route_decorator),
                }
                metrics["fastapi_routes"] += 1

                for dependency in fastapi_dependency_calls(ast_node, bindings):
                    if not dependency.args:
                        metrics["unresolved_dependencies"] += 1
                        unresolved_sites.append(
                            {
                                "source_id": source_symbol["id"],
                                "reason": "implicit-fastapi-dependency",
                                "evidence": {
                                    "path": relative_posix,
                                    "line": dependency.lineno,
                                    "column": dependency.col_offset,
                                    "expression": ast.unparse(dependency),
                                },
                            }
                        )
                        continue
                    expression = dotted_expression(dependency.args[0])
                    if not expression:
                        metrics["unresolved_dependencies"] += 1
                        unresolved_sites.append(
                            {
                                "source_id": source_symbol["id"],
                                "reason": "dynamic-fastapi-dependency",
                                "evidence": {
                                    "path": relative_posix,
                                    "line": dependency.lineno,
                                    "column": dependency.col_offset,
                                    "expression": ast.unparse(dependency),
                                },
                            }
                        )
                        continue
                    target, method, confidence = exact_symbol_target(
                        expression, source_symbol, bindings, qualified_index
                    )
                    if target is None:
                        metrics["unresolved_dependencies"] += 1
                        unresolved_sites.append(
                            {
                                "source_id": source_symbol["id"],
                                "reason": "unresolved-fastapi-dependency",
                                "evidence": {
                                    "path": relative_posix,
                                    "line": dependency.lineno,
                                    "column": dependency.col_offset,
                                    "expression": ast.unparse(dependency),
                                },
                            }
                        )
                        continue
                    edges.append(
                        {
                            "id": (
                                f"depends-on:{source_symbol['id']}->{target['id']}:"
                                f"{dependency.lineno}:{dependency.col_offset}"
                            ),
                            "source": source_symbol["id"],
                            "target": target["id"],
                            "kind": "depends-on",
                            "confidence": confidence,
                            "resolution_method": method,
                            "evidence": {
                                "path": relative_posix,
                                "line": dependency.lineno,
                                "column": dependency.col_offset,
                                "expression": ast.unparse(dependency),
                            },
                        }
                    )
                    metrics["dependency_edges"] += 1

            for call in direct_calls(ast_node):
                metrics["call_sites"] += 1
                expression = dotted_expression(call.func)
                if not expression:
                    metrics["unresolved_calls"] += 1
                    unresolved_sites.append(
                        {
                            "source_id": source_symbol["id"],
                            "reason": "dynamic-call-target",
                            "evidence": {
                                "path": relative_posix,
                                "line": call.lineno,
                                "column": call.col_offset,
                                "expression": ast.unparse(call.func),
                            },
                        }
                    )
                    continue
                target, method, confidence = exact_symbol_target(
                    expression, source_symbol, bindings, qualified_index
                )
                edge_kind = "calls"
                if target is None and "." in expression:
                    matches = [
                        item for item in simple_index.get(expression.rsplit(".", 1)[-1], [])
                        if item["kind"] == "method"
                    ]
                    if len(matches) == 1:
                        target, method, confidence = matches[0], "repo-unique-member-name", 0.55
                        edge_kind = "may-dispatch-to"
                if target is None:
                    metrics["unresolved_calls"] += 1
                    unresolved_sites.append(
                        {
                            "source_id": source_symbol["id"],
                            "reason": "target-outside-or-unresolved",
                            "evidence": {
                                "path": relative_posix,
                                "line": call.lineno,
                                "column": call.col_offset,
                                "expression": expression,
                            },
                        }
                    )
                    continue
                metrics["resolved_calls" if edge_kind == "calls" else "candidate_calls"] += 1
                edges.append(
                    {
                        "id": f"{edge_kind}:{source_symbol['id']}->{target['id']}:{call.lineno}:{call.col_offset}",
                        "source": source_symbol["id"],
                        "target": target["id"],
                        "kind": edge_kind,
                        "confidence": confidence,
                        "resolution_method": method,
                        "evidence": {
                            "path": relative_posix,
                            "line": call.lineno,
                            "column": call.col_offset,
                            "expression": ast.unparse(call.func),
                        },
                    }
                )

    return edges, metrics, unresolved_sites


def discover_representative_flows(
    symbol_nodes: list[dict[str, Any]],
    relationship_edges: list[dict[str, Any]],
    unresolved_sites: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Trace bounded, reproducible paths from recognized framework entrypoints."""
    traversable_kinds = {"calls", "depends-on", "may-dispatch-to", "reads", "writes"}
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for edge in relationship_edges:
        if edge["kind"] in traversable_kinds:
            adjacency.setdefault(edge["source"], []).append(edge)
    for outgoing in adjacency.values():
        outgoing.sort(key=lambda edge: (edge["kind"] == "may-dispatch-to", edge["id"]))

    unresolved_by_source: dict[str, list[dict[str, Any]]] = {}
    for site in unresolved_sites:
        unresolved_by_source.setdefault(site["source_id"], []).append(site)

    entrypoints = sorted(
        (node for node in symbol_nodes if node.get("entrypoint")),
        key=lambda node: (node["path"], node["definition_line"]),
    )
    symbol_index = {node["id"]: node for node in symbol_nodes}
    candidates: list[dict[str, Any]] = []
    candidate_counts: dict[str, int] = {}

    def walk(
        entrypoint: dict[str, Any],
        current_id: str,
        node_ids: list[str],
        edge_ids: list[str],
        confidence: float,
    ) -> None:
        if (
            len(candidates) >= MAX_FLOW_CANDIDATES
            or candidate_counts.get(entrypoint["id"], 0)
            >= MAX_FLOW_CANDIDATES_PER_ENTRYPOINT
        ):
            return
        outgoing = [
            edge for edge in adjacency.get(current_id, [])
            if edge["target"] not in node_ids
        ]
        depth_truncated = len(edge_ids) >= MAX_FLOW_DEPTH and bool(outgoing)
        if len(edge_ids) >= MAX_FLOW_DEPTH:
            outgoing = []
        if outgoing:
            for edge in outgoing:
                walk(
                    entrypoint,
                    edge["target"],
                    [*node_ids, edge["target"]],
                    [*edge_ids, edge["id"]],
                    min(confidence, float(edge.get("confidence", 1.0))),
                )
            return

        unresolved_steps = [
            step
            for node_id in node_ids
            for step in unresolved_by_source.get(node_id, [])
        ]
        if depth_truncated:
            terminal = symbol_index[current_id]
            unresolved_steps.append(
                {
                    "source_id": current_id,
                    "reason": "maximum-flow-depth-reached",
                    "evidence": {
                        "path": terminal["path"],
                        "line": terminal["definition_line"],
                        "expression": terminal["qualified_name"],
                    },
                }
            )
        candidates.append(
            {
                "entrypoint_id": entrypoint["id"],
                "label": entrypoint["entrypoint"]["label"],
                "framework": entrypoint["entrypoint"]["framework"],
                "ordered_node_ids": node_ids,
                "ordered_edge_ids": edge_ids,
                "confidence": confidence,
                "completeness": "partial" if unresolved_steps else "complete",
                "unresolved_steps": unresolved_steps,
            }
        )
        candidate_counts[entrypoint["id"]] = candidate_counts.get(entrypoint["id"], 0) + 1

    for entrypoint in entrypoints:
        walk(entrypoint, entrypoint["id"], [entrypoint["id"]], [], 1.0)

    candidates.sort(
        key=lambda flow: (
            -int(any(
                symbol_index[node_id].get("sqlalchemy", {}).get("kind") == "model"
                for node_id in flow["ordered_node_ids"]
            )),
            -len(flow["ordered_edge_ids"]),
            -flow["confidence"],
            flow["label"],
            flow["ordered_node_ids"],
        )
    )
    representatives: list[dict[str, Any]] = []
    represented_entrypoints: set[str] = set()
    for flow in candidates:
        if flow["entrypoint_id"] in represented_entrypoints:
            continue
        representatives.append(flow)
        represented_entrypoints.add(flow["entrypoint_id"])
        if len(representatives) == MAX_REPRESENTATIVE_FLOWS:
            break
    if len(representatives) < MAX_REPRESENTATIVE_FLOWS:
        for flow in candidates:
            if flow in representatives:
                continue
            representatives.append(flow)
            if len(representatives) == MAX_REPRESENTATIVE_FLOWS:
                break

    for index, flow in enumerate(representatives, start=1):
        flow["id"] = f"flow:{flow['entrypoint_id']}:{index}"
    return representatives


def import_cycle_components(
    file_nodes: list[dict[str, Any]],
    import_edges: list[dict[str, Any]],
) -> list[list[str]]:
    """Return deterministic strongly connected file components with real cycles."""
    adjacency = {node["id"]: [] for node in file_nodes}
    for edge in import_edges:
        adjacency.setdefault(edge["source"], []).append(edge["target"])
    for targets in adjacency.values():
        targets.sort()

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node_id: str) -> None:
        nonlocal index
        indices[node_id] = index
        lowlinks[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for target_id in adjacency.get(node_id, []):
            if target_id not in indices:
                visit(target_id)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target_id])
            elif target_id in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indices[target_id])
        if lowlinks[node_id] != indices[node_id]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node_id:
                break
        if len(component) > 1:
            components.append(sorted(component))

    for node_id in sorted(adjacency):
        if node_id not in indices:
            visit(node_id)
    return sorted(components, key=lambda component: component[0])


def detect_risk_findings(
    file_nodes: list[dict[str, Any]],
    symbol_nodes: list[dict[str, Any]],
    import_edges: list[dict[str, Any]],
    relationship_edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Produce bounded, evidence-backed structural risk heuristics."""
    findings: list[dict[str, Any]] = []
    file_index = {node["id"]: node for node in file_nodes}
    incoming_counts: dict[str, int] = {}
    outgoing_counts: dict[str, int] = {}
    structural_kinds = {"calls", "depends-on", "may-dispatch-to", "reads", "writes"}
    for edge in relationship_edges:
        if edge["kind"] not in structural_kinds:
            continue
        outgoing_counts[edge["source"]] = outgoing_counts.get(edge["source"], 0) + 1
        incoming_counts[edge["target"]] = incoming_counts.get(edge["target"], 0) + 1

    for symbol in sorted(symbol_nodes, key=lambda node: node["id"]):
        line_span = symbol["end_line"] - symbol["definition_line"] + 1
        if symbol["kind"] in {"function", "method"} and line_span >= LARGE_SYMBOL_LINES:
            findings.append(
                {
                    "id": f"risk:large-symbol:{symbol['id']}",
                    "rule_id": "large-symbol",
                    "node_id": symbol["id"],
                    "related_node_ids": [],
                    "title": "Large callable",
                    "severity": "high" if line_span >= LARGE_SYMBOL_LINES * 2 else "medium",
                    "classification": "heuristic",
                    "confidence": 0.9,
                    "summary": (
                        f"{symbol['qualified_name']} spans {line_span} lines, which can make "
                        "behavior harder to isolate, review, and test."
                    ),
                    "provenance": "Python AST source range",
                    "evidence": {
                        "path": symbol["path"],
                        "line": symbol["definition_line"],
                        "end_line": symbol["end_line"],
                        "expression": symbol["qualified_name"],
                    },
                    "metrics": {"line_span": line_span, "threshold": LARGE_SYMBOL_LINES},
                }
            )
        fan_in = incoming_counts.get(symbol["id"], 0)
        if fan_in >= HIGH_SYMBOL_FAN_IN:
            findings.append(
                {
                    "id": f"risk:high-fan-in:{symbol['id']}",
                    "rule_id": "high-fan-in",
                    "node_id": symbol["id"],
                    "related_node_ids": [],
                    "title": "Change-amplification hotspot",
                    "severity": "high" if fan_in >= HIGH_SYMBOL_FAN_IN * 2 else "medium",
                    "classification": "heuristic",
                    "confidence": 0.88,
                    "summary": (
                        f"{symbol['qualified_name']} has {fan_in} incoming execution relationships; "
                        "changes here may affect many callers."
                    ),
                    "provenance": "Resolved symbol relationship graph",
                    "evidence": {
                        "path": symbol["path"],
                        "line": symbol["definition_line"],
                        "end_line": symbol["end_line"],
                        "expression": symbol["qualified_name"],
                    },
                    "metrics": {"fan_in": fan_in, "threshold": HIGH_SYMBOL_FAN_IN},
                }
            )
        fan_out = outgoing_counts.get(symbol["id"], 0)
        if fan_out >= HIGH_SYMBOL_FAN_OUT:
            findings.append(
                {
                    "id": f"risk:high-fan-out:{symbol['id']}",
                    "rule_id": "high-fan-out",
                    "node_id": symbol["id"],
                    "related_node_ids": [],
                    "title": "Coordination hotspot",
                    "severity": "high" if fan_out >= HIGH_SYMBOL_FAN_OUT * 2 else "medium",
                    "classification": "heuristic",
                    "confidence": 0.88,
                    "summary": (
                        f"{symbol['qualified_name']} has {fan_out} outgoing execution relationships; "
                        "it coordinates many collaborators and may carry several responsibilities."
                    ),
                    "provenance": "Resolved symbol relationship graph",
                    "evidence": {
                        "path": symbol["path"],
                        "line": symbol["definition_line"],
                        "end_line": symbol["end_line"],
                        "expression": symbol["qualified_name"],
                    },
                    "metrics": {"fan_out": fan_out, "threshold": HIGH_SYMBOL_FAN_OUT},
                }
            )

    for component in import_cycle_components(file_nodes, import_edges):
        anchor = file_index[component[0]]
        paths = [file_index[node_id]["path"] for node_id in component]
        findings.append(
            {
                "id": f"risk:import-cycle:{'|'.join(component)}",
                "rule_id": "import-cycle",
                "node_id": component[0],
                "related_node_ids": component[1:],
                "title": "Circular import component",
                "severity": "high" if len(component) >= 4 else "medium",
                "classification": "heuristic",
                "confidence": 0.98,
                "summary": (
                    f"{len(component)} files form a closed import cycle: {', '.join(paths)}. "
                    "This can make initialization order and module boundaries fragile."
                ),
                "provenance": "Strongly connected component in the resolved Python import graph",
                "evidence": {
                    "path": anchor["path"],
                    "line": 1,
                    "expression": " -> ".join(paths),
                },
                "metrics": {"component_size": len(component), "threshold": 2},
            }
        )

    severity_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        findings,
        key=lambda finding: (severity_order[finding["severity"]], finding["evidence"]["path"], finding["id"]),
    )


def evidence_layer(path: str) -> tuple[str, str, str]:
    """Return a bounded architectural role and interpretation for a source path."""
    if path.startswith("tests/"):
        return (
            "test",
            "Verifies production behavior and records an expected outcome.",
            "Keeping verification code outside production modules separates test setup from runtime behavior.",
        )
    if "/entrypoints/" in path or path.endswith(("bootstrap.py", "views.py")):
        return (
            "entrypoint",
            "Receives or starts an execution flow and delegates work inward.",
            "A boundary module keeps delivery and startup concerns from leaking into application behavior.",
        )
    if "/service_layer/" in path or "/application/" in path:
        return (
            "application service",
            "Coordinates an application use case across domain and infrastructure collaborators.",
            "An application layer centralizes orchestration without coupling domain objects to delivery technology.",
        )
    if "/domain/" in path:
        return (
            "domain behavior",
            "Represents business vocabulary or behavior independently of delivery and persistence details.",
            "This placement protects business rules from framework and database dependencies.",
        )
    if "/adapters/" in path or "/infrastructure/" in path:
        return (
            "infrastructure adapter",
            "Translates between application-facing behavior and an external technology.",
            "An adapter boundary keeps replaceable infrastructure details behind application-facing code.",
        )
    return (
        "supporting code",
        "Provides reusable behavior or configuration to other repository modules.",
        "The available structural evidence does not prove a more specific architectural intent.",
    )


def build_symbol_evidence_packets(
    symbol_nodes: list[dict[str, Any]],
    relationship_edges: list[dict[str, Any]],
    flows: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> int:
    """Attach retrieval-ready, classification-preserving evidence to every symbol."""
    symbol_index = {node["id"]: node for node in symbol_nodes}
    relationship_kinds = {"calls", "extends", "may-dispatch-to", "depends-on", "reads", "writes"}
    incoming: dict[str, list[dict[str, Any]]] = {}
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in relationship_edges:
        if edge["kind"] not in relationship_kinds:
            continue
        outgoing.setdefault(edge["source"], []).append(edge)
        incoming.setdefault(edge["target"], []).append(edge)
    for edges in [*incoming.values(), *outgoing.values()]:
        edges.sort(key=lambda edge: edge["id"])

    flow_ids: dict[str, list[str]] = {}
    for flow in flows:
        for node_id in flow["ordered_node_ids"]:
            flow_ids.setdefault(node_id, []).append(flow["id"])
    finding_ids: dict[str, list[str]] = {}
    findings_by_id = {finding["id"]: finding for finding in findings}
    for finding in findings:
        for node_id in [finding["node_id"], *finding["related_node_ids"]]:
            finding_ids.setdefault(node_id, []).append(finding["id"])

    for symbol in symbol_nodes:
        symbol_incoming = incoming.get(symbol["id"], [])
        symbol_outgoing = outgoing.get(symbol["id"], [])
        layer, layer_role, layer_rationale = evidence_layer(symbol["path"])
        model = symbol.get("sqlalchemy", {}).get("kind") == "model"
        route = symbol.get("entrypoint", {}).get("kind") == "route"
        symbol_flows = sorted(flow_ids.get(symbol["id"], []))
        symbol_findings = sorted(finding_ids.get(symbol["id"], []))

        if symbol.get("docstring"):
            documented = " ".join(symbol["docstring"].split())
            summary_text = documented[:297] + "..." if len(documented) > 300 else documented
            summary_provenance = f"Python docstring at {symbol['path']}:{symbol['definition_line']}"
        elif route:
            summary_text = f"Handles {symbol['entrypoint']['label']} as a proven FastAPI route."
            summary_provenance = "FastAPI route decorator resolved from the Python AST"
        elif model:
            table = symbol["sqlalchemy"].get("table_name") or symbol["sqlalchemy"].get("table_expression")
            summary_text = f"Defines a SQLAlchemy model{f' mapped to {table}' if table else ''}."
            summary_provenance = "SQLAlchemy declarative inheritance and mapped annotations"
        else:
            async_prefix = "async " if symbol.get("is_async") else ""
            summary_text = f"Defines the {async_prefix}{symbol['kind']} {symbol['qualified_name']}."
            summary_provenance = "Python AST symbol definition"

        if route:
            role_text = f"Receives {symbol['entrypoint']['label']} and begins an HTTP execution flow."
            role_provenance = "Resolved FastAPI decorator"
            rationale_text = "Keeping HTTP routing at a boundary lets application and domain code remain independent of request delivery details."
        elif model:
            table = symbol["sqlalchemy"].get("table_name") or "a database table"
            role_text = f"Maps application state to {table} through SQLAlchemy."
            role_provenance = "Resolved SQLAlchemy declarative model"
            rationale_text = "A dedicated mapping type makes persistence structure explicit while callers can refer to a stable application concept."
        else:
            role_text = layer_role
            role_provenance = f"Path convention and symbol placement: {symbol['path']}"
            rationale_text = layer_rationale

        claims: list[dict[str, Any]] = [
            {
                "id": f"claim:{symbol['id']}:source-range",
                "classification": "fact",
                "text": (
                    f"{symbol['qualified_name']} occupies lines {symbol['start_line']}–{symbol['end_line']} "
                    f"in {symbol['path']}."
                ),
                "confidence": 1.0,
                "provenance": "Python AST source range",
                "evidence_refs": [symbol["id"]],
            },
            {
                "id": f"claim:{symbol['id']}:relationships",
                "classification": "fact",
                "text": (
                    f"{len(symbol_outgoing)} outgoing and {len(symbol_incoming)} incoming execution or structural "
                    "relationships were resolved."
                ),
                "confidence": 1.0,
                "provenance": "Resolved relationship edge IDs in this evidence packet",
                "evidence_refs": [
                    symbol["id"],
                    *[edge["id"] for edge in [*symbol_outgoing, *symbol_incoming]],
                ],
            },
        ]
        for edge in symbol_outgoing[:6]:
            target = symbol_index.get(edge["target"])
            if target is None:
                continue
            claims.append(
                {
                    "id": f"claim:{symbol['id']}:edge:{edge['id']}",
                    "classification": "fact" if edge["kind"] != "may-dispatch-to" else "heuristic",
                    "text": f"{edge['kind'].replace('-', ' ').capitalize()} {target['qualified_name']}.",
                    "confidence": float(edge.get("confidence", 1.0)),
                    "provenance": (
                        f"{edge.get('resolution_method', 'static relationship')} at "
                        f"{edge.get('evidence', {}).get('path', symbol['path'])}:"
                        f"{edge.get('evidence', {}).get('line', symbol['definition_line'])}"
                    ),
                    "evidence_refs": [edge["id"]],
                }
            )
        if symbol_flows:
            claims.append(
                {
                    "id": f"claim:{symbol['id']}:flows",
                    "classification": "fact",
                    "text": f"Participates in {len(symbol_flows)} representative execution flow{'s' if len(symbol_flows) != 1 else ''}.",
                    "confidence": 1.0,
                    "provenance": "Bounded flow traversal from proven framework entrypoints",
                    "evidence_refs": symbol_flows,
                }
            )
        for finding_id in symbol_findings:
            finding = findings_by_id[finding_id]
            claims.append(
                {
                    "id": f"claim:{symbol['id']}:finding:{finding_id}",
                    "classification": finding["classification"],
                    "text": finding["summary"],
                    "confidence": finding["confidence"],
                    "provenance": finding["provenance"],
                    "evidence_refs": [finding_id],
                }
            )
        claims.append(
            {
                "id": f"claim:{symbol['id']}:role",
                "classification": "heuristic" if not (route or model) else "fact",
                "text": role_text,
                "confidence": 0.9 if not (route or model) else 0.98,
                "provenance": role_provenance,
                "evidence_refs": [symbol["id"]],
            }
        )

        symbol["evidence_packet"] = {
            "version": "1",
            "node_id": symbol["id"],
            "source_range": {
                "path": symbol["path"],
                "start_line": symbol["start_line"],
                "end_line": symbol["end_line"],
            },
            "summary": {
                "text": summary_text,
                "classification": "fact",
                "confidence": 1.0,
                "provenance": summary_provenance,
            },
            "execution_role": {
                "text": role_text,
                "classification": "fact" if route or model else "heuristic",
                "confidence": 0.98 if route or model else 0.9,
                "provenance": role_provenance,
            },
            "structural_rationale": {
                "text": rationale_text,
                "classification": "interpretation",
                "confidence": 0.72,
                "provenance": f"Architectural pattern interpretation for the {layer} layer",
            },
            "related_edge_ids": [edge["id"] for edge in [*symbol_outgoing, *symbol_incoming]],
            "flow_ids": symbol_flows,
            "finding_ids": symbol_findings,
            "claims": claims,
        }
    return len(symbol_nodes)


# --------------------------------------------------------------------------
# Edge extraction
# --------------------------------------------------------------------------

def extract_import_edges(
    repo_root: Path,
    python_files: list[Path],
    module_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Build one 'imports' edge per resolved internal import relationship."""
    edges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for path in sorted(python_files):
        relative_path = path.relative_to(repo_root)
        source_id = f"file:{relative_path.as_posix()}"

        try:
            statements = extract_import_statements(path)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            print(f"Warning: skipping unreadable or unparsable file {relative_path.as_posix()} ({exc})")
            continue

        for stmt in statements:
            for candidates in candidate_targets(stmt, relative_path):
                target_id = resolve_first(candidates, module_map)
                if target_id is None or target_id == source_id:
                    continue
                pair = (source_id, target_id)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                edges.append(
                    {
                        "id": f"import:{source_id}->{target_id}",
                        "source": source_id,
                        "target": target_id,
                        "kind": "imports",
                        "confidence": 1.0,
                        "resolution_method": "ast-static",
                        "evidence": {"line": stmt.lineno},
                    }
                )
    return edges


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------

def analyze_repository(repo_root: Path) -> dict[str, Any]:
    """Create the versioned graph contract expected by the frontend."""
    repo_root = repo_root.resolve()
    if not repo_root.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_root}")

    discovered_files = find_python_files(repo_root)
    total_files_found = len(discovered_files)

    python_files = sorted(discovered_files)
    files_truncated = total_files_found > MAX_PYTHON_FILES
    if files_truncated:
        python_files = python_files[:MAX_PYTHON_FILES]

    file_nodes = build_file_nodes(repo_root, python_files)
    symbol_nodes, containment_edges, symbol_parse_failures = extract_symbol_graph(
        repo_root, python_files
    )

    roots = find_module_roots(repo_root, python_files)
    module_map = build_module_map(repo_root, python_files, roots)
    import_edges = extract_import_edges(repo_root, python_files, module_map)
    relationship_edges, relationship_coverage, unresolved_sites = extract_symbol_relationship_edges(
        repo_root, python_files, symbol_nodes
    )
    sqlalchemy_coverage = enrich_sqlalchemy_models(
        repo_root, python_files, symbol_nodes, relationship_edges
    )
    sqlalchemy_edges, sqlalchemy_access_coverage = extract_sqlalchemy_access_edges(
        repo_root, python_files, symbol_nodes
    )
    relationship_edges.extend(sqlalchemy_edges)
    flows = discover_representative_flows(
        symbol_nodes, relationship_edges, unresolved_sites
    )
    findings = detect_risk_findings(
        file_nodes, symbol_nodes, import_edges, relationship_edges
    )
    evidence_packets = build_symbol_evidence_packets(
        symbol_nodes, relationship_edges, flows, findings
    )

    return {
        "schema_version": "0.8",
        "repo_root": str(repo_root),
        "python_files_total_found": total_files_found,
        "python_files_analyzed": len(python_files),
        "python_files_truncated": files_truncated,
        "nodes": [*file_nodes, *symbol_nodes],
        "edges": [*import_edges, *containment_edges, *relationship_edges],
        "flows": flows,
        "findings": findings,
        "coverage": {
            "python_files": len(python_files),
            "symbol_nodes": len(symbol_nodes),
            "symbol_parse_failures": symbol_parse_failures,
            "representative_flows": len(flows),
            "risk_findings": len(findings),
            "high_risk_findings": sum(finding["severity"] == "high" for finding in findings),
            "medium_risk_findings": sum(finding["severity"] == "medium" for finding in findings),
            "evidence_packets": evidence_packets,
            **relationship_coverage,
            **sqlalchemy_coverage,
            **sqlalchemy_access_coverage,
        },
    }


def write_graph(graph: dict[str, Any], output_path: Path) -> None:
    """Write graph JSON, creating the output directory when needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as graph_file:
        graph_file.write(json.dumps(graph, indent=2) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repo",
        type=str,
        help=(
            "Local path or public GitHub URL "
            "(https://github.com/<owner>/<repository>) to analyze"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("graph.json"),
        help="Output JSON path (default: graph.json)",
    )
    return parser.parse_args()


def is_github_url(repo: str) -> bool:
    return repo.strip().lower().startswith(("http://", "https://"))


def main() -> None:
    args = parse_args()

    if is_github_url(args.repo):
        try:
            repo_path = load_repository(args.repo)
            commit_sha = resolve_commit_sha(repo_path)
        except RepositoryLoadError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

        try:
            graph = analyze_repository(repo_path)
            graph.pop("repo_root", None)
            owner, repository = validate_github_url(args.repo)
            graph["repository"] = {
                "name": f"{owner}/{repository}",
                "url": args.repo,
                "pinned_url": f"https://github.com/{owner}/{repository}/tree/{commit_sha}",
                "source": "github",
            }
            graph["snapshot"] = {"commit_sha": commit_sha}
            graph["source_url"] = args.repo
            write_graph(graph, args.output)
        finally:
            cleanup_repository(repo_path)
    else:
        repo_path = Path(args.repo)
        graph = analyze_repository(repo_path)
        write_graph(graph, args.output)

    print(f"Wrote {len(graph['nodes'])} nodes and {len(graph['edges'])} edges to {args.output}")


if __name__ == "__main__":
    main()






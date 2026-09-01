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
    traversable_kinds = {"calls", "depends-on", "may-dispatch-to"}
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
    flows = discover_representative_flows(
        symbol_nodes, relationship_edges, unresolved_sites
    )

    return {
        "schema_version": "0.5",
        "repo_root": str(repo_root),
        "python_files_total_found": total_files_found,
        "python_files_analyzed": len(python_files),
        "python_files_truncated": files_truncated,
        "nodes": [*file_nodes, *symbol_nodes],
        "edges": [*import_edges, *containment_edges, *relationship_edges],
        "flows": flows,
        "coverage": {
            "python_files": len(python_files),
            "symbol_nodes": len(symbol_nodes),
            "symbol_parse_failures": symbol_parse_failures,
            "representative_flows": len(flows),
            **relationship_coverage,
        },
    }


def write_graph(graph: dict[str, Any], output_path: Path) -> None:
    """Write graph JSON, creating the output directory when needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")


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



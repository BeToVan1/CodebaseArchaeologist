"""Generate the file graph consumed by the Codebase Archaeologist UI."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, NamedTuple

from repository_loader import RepositoryLoadError, cleanup_repository, load_repository


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
        except SyntaxError as exc:
            print(f"Warning: skipping unparsable file {relative_path.as_posix()} ({exc})")
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
                        "source_id": source_id,
                        "target_id": target_id,
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

    nodes = build_file_nodes(repo_root, python_files)

    roots = find_module_roots(repo_root, python_files)
    module_map = build_module_map(repo_root, python_files, roots)
    edges = extract_import_edges(repo_root, python_files, module_map)

    return {
        "schema_version": "0.2",
        "repo_root": str(repo_root),
        "python_files_total_found": total_files_found,
        "python_files_analyzed": len(python_files),
        "python_files_truncated": files_truncated,
        "nodes": nodes,
        "edges": edges,
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
        except RepositoryLoadError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

        try:
            graph = analyze_repository(repo_path)
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
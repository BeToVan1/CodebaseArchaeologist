"""Generate the file graph consumed by the Codebase Archaeologist UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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


def find_python_files(repo_root: Path) -> list[Path]:
    """Return Python files under repo_root, excluding generated/vendor folders."""
    return [
        path
        for path in repo_root.rglob("*.py")
        if not any(part in IGNORED_DIR_NAMES for part in path.relative_to(repo_root).parts)
    ]


def build_file_nodes(repo_root: Path, python_files: list[Path]) -> list[dict[str, str]]:
    """Build stable file nodes using repository-relative paths as identifiers."""
    nodes: list[dict[str, str]] = []
    for path in sorted(python_files):
        relative_path = path.relative_to(repo_root).as_posix()
        nodes.append(
            {
                "id": f"file:{relative_path}",
                "kind": "file",
                "path": relative_path,
            }
        )
    return nodes


def analyze_repository(repo_root: Path) -> dict[str, Any]:
    """Create the versioned graph contract expected by the frontend."""
    repo_root = repo_root.resolve()
    if not repo_root.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_root}")

    nodes = build_file_nodes(repo_root, find_python_files(repo_root))
    return {
        "schema_version": "0.1",
        "repo_root": str(repo_root),
        "nodes": nodes,
        "edges": [],
    }


def write_graph(graph: dict[str, Any], output_path: Path) -> None:
    """Write graph JSON, creating the output directory when needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, help="Path to the Python repository to analyze")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("graph.json"),
        help="Output JSON path (default: graph.json)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    graph = analyze_repository(args.repo)
    write_graph(graph, args.output)
    print(f"Wrote {len(graph['nodes'])} nodes to {args.output}")


if __name__ == "__main__":
    main()

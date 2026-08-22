#!/usr/bin/env python3
"""
AI Codebase Archaeologist — Analyzer Foundation (M1: Ingestion)

Walks a local repository, discovers every .py file, and emits one
"file" node per discovered file into graph.json. This is the seed
for the normalized code graph described in the design doc (section 7):
later stages (AST/symbol extraction, resolution, framework adapters)
will add more node kinds and edges to the same graph structure.

Usage:
    python analyzer.py /path/to/repo
    python analyzer.py /path/to/repo --output graph.json
"""

import argparse
import json
import sys
from pathlib import Path

# Directories we never want to walk into. Mirrors the "ignore rules"
# called out in design doc 6.1 (project discovery / classification).
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


def is_ignored_dir(dir_name: str) -> bool:
    if dir_name in IGNORED_DIR_NAMES:
        return True
    if dir_name.endswith(".egg-info"):
        return True
    return False


def find_python_files(repo_root: Path):
    """Yield every .py file under repo_root, skipping ignored directories."""
    for path in repo_root.rglob("*.py"):
        if not path.is_file():
            continue
        if any(is_ignored_dir(part) for part in path.relative_to(repo_root).parts[:-1]):
            continue
        yield path


def build_file_nodes(repo_root: Path):
    """Build one 'file' node per .py file, with a stable id and POSIX-style path."""
    nodes = []
    for index, path in enumerate(sorted(find_python_files(repo_root))):
        relative_path = path.relative_to(repo_root).as_posix()
        nodes.append(
            {
                "id": f"file:{index}",
                "kind": "file",
                "path": relative_path,
            }
        )
    return nodes


def main():
    parser = argparse.ArgumentParser(
        description="Discover .py files in a local repository and emit file nodes to graph.json."
    )
    parser.add_argument("repo_path", type=str, help="Path to the local repository root.")
    parser.add_argument(
        "--output",
        type=str,
        default="graph.json",
        help="Path to the output graph JSON file (default: graph.json).",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_path).resolve()
    if not repo_root.is_dir():
        print(f"Error: '{repo_root}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    nodes = build_file_nodes(repo_root)

    graph = {
        "repo_root": str(repo_root),
        "nodes": nodes,
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

    print(f"Discovered {len(nodes)} Python file(s).")
    print(f"Wrote graph to {output_path.resolve()}")


if __name__ == "__main__":
    main()
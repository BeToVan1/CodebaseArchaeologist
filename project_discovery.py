"""Bounded, read-only discovery of root pyproject.toml declarations.

Not a build frontend, dependency resolver, or validation of installed software.
Never follows declared file paths, imports a backend, or executes setup hooks.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import tomllib

MAX_MANIFEST_BYTES = 256 * 1024
MAX_DECLARATIONS = 128
MAX_ITEMS = 128
MAX_TEXT = 512


def discover_project(root: Path) -> dict:
    result = {"version": "1", "scope": "root-pyproject-only", "status": "missing",
              "path": "pyproject.toml", "sha256": None, "declarations": [], "warnings": [],
              "limitations": ["Declarations are not installed versions, proven runtime behavior, or resolved CLI targets.",
                  "Nested projects, setup.py/setup.cfg, requirements/lockfiles and tool-specific metadata are not interpreted."]}
    warnings = result["warnings"]
    def warn(message):
        if message not in warnings and len(warnings) < 12:
            warnings.append(message)
    path = root / "pyproject.toml"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            result["status"] = "skipped"
            warn("Manifest is not a regular non-symlink file.")
            return result
        if info.st_size > MAX_MANIFEST_BYTES:
            result["status"] = "skipped"
            warn("Manifest exceeds the 256 KiB byte limit.")
            return result
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(info, opened):
                raise OSError("Manifest changed during open")
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            result["status"] = "skipped"
            warn("Manifest exceeds the 256 KiB byte limit.")
            return result
    except FileNotFoundError:
        return result
    except OSError:
        result["status"] = "unreadable"
        warn("Manifest could not be read safely.")
        return result
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError, RecursionError):
        result["status"] = "invalid"
        warn("Manifest is not supported valid UTF-8 TOML; metadata was not extracted.")
        return result
    result["status"] = "parsed"
    declaration_bytes = 0

    def text(value):
        return isinstance(value, str) and 0 < len(value) <= MAX_TEXT and not any(ord(c) < 32 for c in value)

    def add(keys, value, *, array=False, dependency=False):
        nonlocal declaration_bytes
        values = value if array and isinstance(value, list) else [value] if not array else None
        if values is None or len(values) > MAX_ITEMS or not all(text(v) for v in values):
            warn("Unsupported, invalid, or oversized declaration omitted.")
            return
        if dependency and any("@" in v or "://" in v for v in values):
            warn("A dependency field containing direct references was omitted; URLs are not fetched or exposed.")
            return
        if len(result["declarations"]) >= MAX_DECLARATIONS:
            warn("Declaration count exceeded the 128-record limit; remaining records omitted.")
            return
        record = {"key": keys, "value": value, "classification": "fact",
            "confidence": 1, "provenance": "Literal pyproject.toml declaration; semantics not verified"}
        size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
        if declaration_bytes + size > 64 * 1024:
            warn("Declaration output exceeded the 64 KiB budget; additional records omitted.")
            return
        declaration_bytes += size
        result["declarations"].append(record)

    project = document.get("project")
    if project is None:
        warn("No standard project table; project metadata is not statically established here.")
    elif not isinstance(project, dict):
        warn("Project metadata table has an invalid shape.")
    else:
        dynamic = project.get("dynamic", [])
        if not isinstance(dynamic, list) or len(dynamic) > MAX_ITEMS or not all(text(v) for v in dynamic):
            warn("Invalid dynamic field list; project declarations omitted to avoid overstating completeness.")
        else:
            if dynamic:
                add(["project", "dynamic"], dynamic, array=True)
                warn("Dynamic project fields are unresolved and omitted; no backend was executed.")
            for key in ("name", "version", "requires-python", "dependencies"):
                if key in project and key not in dynamic:
                    add(["project", key], project[key], array=key == "dependencies", dependency=key == "dependencies")
            for key in ("optional-dependencies", "scripts", "gui-scripts"):
                if key not in project or key in dynamic:
                    continue
                table = project[key]
                if not isinstance(table, dict) or len(table) > MAX_ITEMS or not all(text(k) for k in table):
                    warn("Unsupported, invalid, or oversized declaration table omitted.")
                    continue
                for name in sorted(table):
                    add(["project", key, name], table[name], array=key == "optional-dependencies", dependency=key == "optional-dependencies")
    build = document.get("build-system")
    if build is not None:
        if not isinstance(build, dict):
            warn("Build-system metadata table has an invalid shape.")
        else:
            for key in ("requires", "build-backend"):
                if key in build:
                    add(["build-system", key], build[key], array=key == "requires", dependency=key == "requires")
    return result

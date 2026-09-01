"""Tests for the source-reading and file-count enforcement added to analyzer.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import analyzer


# --------------------------------------------------------------------------
# read_source
# --------------------------------------------------------------------------

def test_read_source_returns_full_text_for_small_file(tmp_path: Path) -> None:
    file_path = tmp_path / "small.py"
    file_path.write_bytes(b"class Batch:\n    pass\n")

    source, truncated, error = analyzer.read_source(file_path)

    assert error is None
    assert truncated is False
    assert source == "class Batch:\n    pass\n"


def test_read_source_truncates_large_file_and_sets_flag(tmp_path: Path) -> None:
    file_path = tmp_path / "big.py"
    # One byte over the limit so truncation is guaranteed.
    file_path.write_text("x" * (analyzer.MAX_SOURCE_BYTES + 1), encoding="utf-8")

    source, truncated, error = analyzer.read_source(file_path)

    assert error is None
    assert truncated is True
    assert source is not None
    assert len(source.encode("utf-8")) == analyzer.MAX_SOURCE_BYTES


def test_read_source_truncation_does_not_break_multibyte_characters(tmp_path: Path) -> None:
    file_path = tmp_path / "unicode_heavy.py"
    # Multi-byte characters (e.g. "é" = 2 bytes) so a naive byte-slice could
    # cut through the middle of one right at the boundary.
    file_path.write_text("é" * (analyzer.MAX_SOURCE_BYTES), encoding="utf-8")

    source, truncated, error = analyzer.read_source(file_path)

    assert error is None
    assert truncated is True
    # Should decode cleanly with no replacement/garbage characters.
    assert source is not None
    assert all(ch == "é" for ch in source)


def test_read_source_handles_undecodable_file_without_crashing(tmp_path: Path) -> None:
    file_path = tmp_path / "binary_like.py"
    file_path.write_bytes(b"\xff\xfe\x00\x01invalid utf-8 \xc0\xc1")

    source, truncated, error = analyzer.read_source(file_path)

    assert source is None
    assert truncated is False
    assert error is not None
    assert "utf-8" in error.lower()


def test_read_source_handles_unreadable_file_without_crashing(tmp_path: Path) -> None:
    missing_path = tmp_path / "does_not_exist.py"

    source, truncated, error = analyzer.read_source(missing_path)

    assert source is None
    assert truncated is False
    assert error is not None


# --------------------------------------------------------------------------
# build_file_nodes
# --------------------------------------------------------------------------

def test_build_file_nodes_includes_source_fields(tmp_path: Path) -> None:
    file_path = tmp_path / "model.py"
    file_path.write_bytes(b"class Batch:\n    ...\n")

    nodes = analyzer.build_file_nodes(tmp_path, [file_path])

    assert len(nodes) == 1
    node = nodes[0]
    assert node["id"] == "file:model.py"
    assert node["kind"] == "file"
    assert node["path"] == "model.py"
    assert node["size_bytes"] == len(b"class Batch:\n    ...\n")
    assert node["source"] == "class Batch:\n    ...\n"
    assert node["source_truncated"] is False


def test_build_file_nodes_omits_source_and_records_error_for_undecodable_file(tmp_path: Path) -> None:
    file_path = tmp_path / "bad.py"
    file_path.write_bytes(b"\xff\xfe garbage \xc0\xc1")

    nodes = analyzer.build_file_nodes(tmp_path, [file_path])

    assert len(nodes) == 1
    node = nodes[0]
    assert "source" not in node
    assert "source_truncated" not in node
    assert "source_error" in node


# --------------------------------------------------------------------------
# File-count enforcement
# --------------------------------------------------------------------------

def _make_python_files(root: Path, count: int) -> None:
    for i in range(count):
        (root / f"module_{i}.py").write_text(f"x = {i}\n", encoding="utf-8")


def test_analyze_repository_enforces_file_count_limit(tmp_path: Path) -> None:
    _make_python_files(tmp_path, 5)

    with patch.object(analyzer, "MAX_PYTHON_FILES", 3):
        graph = analyzer.analyze_repository(tmp_path)

    assert graph["python_files_total_found"] == 5
    assert graph["python_files_analyzed"] == 3
    assert graph["python_files_truncated"] is True
    assert len(graph["nodes"]) == 3


def test_analyze_repository_does_not_truncate_when_under_limit(tmp_path: Path) -> None:
    _make_python_files(tmp_path, 3)

    with patch.object(analyzer, "MAX_PYTHON_FILES", 2000):
        graph = analyzer.analyze_repository(tmp_path)

    assert graph["python_files_total_found"] == 3
    assert graph["python_files_analyzed"] == 3
    assert graph["python_files_truncated"] is False
    assert len(graph["nodes"]) == 3


def test_analyze_repository_file_selection_is_deterministic(tmp_path: Path) -> None:
    """When truncating, the same (sorted) subset of files should be picked every run."""
    _make_python_files(tmp_path, 5)

    with patch.object(analyzer, "MAX_PYTHON_FILES", 3):
        graph_a = analyzer.analyze_repository(tmp_path)
        graph_b = analyzer.analyze_repository(tmp_path)

    paths_a = sorted(node["path"] for node in graph_a["nodes"])
    paths_b = sorted(node["path"] for node in graph_b["nodes"])
    assert paths_a == paths_b
    assert paths_a == ["module_0.py", "module_1.py", "module_2.py"]

# --------------------------------------------------------------------------
# Full graph contract and error tolerance
# --------------------------------------------------------------------------

def test_analyze_repository_uses_frontend_edge_contract(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("import target\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("VALUE = 1\n", encoding="utf-8")

    graph = analyzer.analyze_repository(tmp_path)

    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["id"] == "import:file:source.py->file:target.py"
    assert edge["source"] == "file:source.py"
    assert edge["target"] == "file:target.py"
    assert "source_id" not in edge
    assert "target_id" not in edge


def test_analyze_repository_skips_imports_for_undecodable_file(tmp_path: Path) -> None:
    file_path = tmp_path / "binary_like.py"
    file_path.write_bytes(b"\xff\xfe\x00\x01invalid utf-8 \xc0\xc1")

    graph = analyzer.analyze_repository(tmp_path)

    assert len(graph["nodes"]) == 1
    assert "source_error" in graph["nodes"][0]
    assert graph["edges"] == []


# --------------------------------------------------------------------------
# Symbol extraction and exact source ranges
# --------------------------------------------------------------------------

def test_analyze_repository_extracts_nested_symbols_and_containment(tmp_path: Path) -> None:
    source = """@registry.register
class Service:
    @classmethod
    async def execute(cls, value: int) -> int:
        def normalize(item: int) -> int:
            return item + 1
        return normalize(value)

def bootstrap() -> Service:
    return Service()
"""
    (tmp_path / "service.py").write_text(source, encoding="utf-8")

    graph = analyzer.analyze_repository(tmp_path)
    symbols = [node for node in graph["nodes"] if node["kind"] != "file"]

    assert [(node["kind"], node["qualified_name"]) for node in symbols] == [
        ("class", "service.Service"),
        ("method", "service.Service.execute"),
        ("function", "service.Service.execute.normalize"),
        ("function", "service.bootstrap"),
    ]
    service, execute, normalize, bootstrap = symbols
    assert (service["start_line"], service["definition_line"], service["end_line"]) == (1, 2, 7)
    assert service["decorators"] == ["registry.register"]
    assert (execute["start_line"], execute["definition_line"], execute["end_line"]) == (3, 4, 7)
    assert execute["decorators"] == ["classmethod"]
    assert execute["is_async"] is True
    assert normalize["parent_id"] == execute["id"]
    assert bootstrap["parent_id"] == "file:service.py"

    contains = [edge for edge in graph["edges"] if edge["kind"] == "contains"]
    assert len(contains) == 4
    assert {edge["target"] for edge in contains} == {node["id"] for node in symbols}
    assert graph["coverage"] == {
        "python_files": 1,
        "symbol_nodes": 4,
        "symbol_parse_failures": 0,
        "representative_flows": 0,
        "call_sites": 2,
        "resolved_calls": 2,
        "candidate_calls": 0,
        "unresolved_calls": 0,
        "inheritance_edges": 0,
        "fastapi_routes": 0,
        "dependency_edges": 0,
        "unresolved_dependencies": 0,
        "sqlalchemy_models": 0,
        "sqlalchemy_abstract_models": 0,
        "sqlalchemy_columns": 0,
        "sqlalchemy_relationships": 0,
        "sqlalchemy_reads": 0,
        "sqlalchemy_writes": 0,
    }


def test_symbol_extraction_reports_parse_failures_without_dropping_file_node(
    tmp_path: Path,
) -> None:
    (tmp_path / "broken.py").write_text("def unfinished(:\n", encoding="utf-8")

    graph = analyzer.analyze_repository(tmp_path)

    assert [node["kind"] for node in graph["nodes"]] == ["file"]
    assert graph["coverage"]["symbol_parse_failures"] == 1


def test_symbol_qualified_name_uses_import_root_for_src_layout(tmp_path: Path) -> None:
    package = tmp_path / "src" / "allocation"
    package.mkdir(parents=True)
    (package / "service.py").write_text("def allocate():\n    pass\n", encoding="utf-8")

    graph = analyzer.analyze_repository(tmp_path)
    symbol = next(node for node in graph["nodes"] if node["kind"] == "function")

    assert symbol["qualified_name"] == "allocation.service.allocate"


def test_analyze_repository_resolves_inheritance_calls_and_dispatch_candidates(
    tmp_path: Path,
) -> None:
    (tmp_path / "base.py").write_text(
        """class Base:
    def save(self):
        pass

    def unique_method(self):
        pass
""",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        """from base import Base

class Child(Base):
    def run(self):
        self.helper()
        external.unknown()

    def helper(self):
        return create()

def create():
    return Child()

def dispatch(target):
    return target.unique_method()
""",
        encoding="utf-8",
    )

    graph = analyzer.analyze_repository(tmp_path)
    symbols = {node["qualified_name"]: node for node in graph["nodes"] if node["kind"] != "file"}
    relationships = [
        edge for edge in graph["edges"]
        if edge["kind"] in {"extends", "calls", "may-dispatch-to"}
    ]

    assert any(
        edge["kind"] == "extends"
        and edge["source"] == symbols["service.Child"]["id"]
        and edge["target"] == symbols["base.Base"]["id"]
        and edge["resolution_method"] == "ast-import-binding"
        for edge in relationships
    )
    assert any(
        edge["kind"] == "calls"
        and edge["source"] == symbols["service.Child.run"]["id"]
        and edge["target"] == symbols["service.Child.helper"]["id"]
        and edge["evidence"]["line"] == 5
        for edge in relationships
    )
    assert any(
        edge["kind"] == "calls"
        and edge["source"] == symbols["service.Child.helper"]["id"]
        and edge["target"] == symbols["service.create"]["id"]
        for edge in relationships
    )
    assert any(
        edge["kind"] == "calls"
        and edge["source"] == symbols["service.create"]["id"]
        and edge["target"] == symbols["service.Child"]["id"]
        for edge in relationships
    )
    assert any(
        edge["kind"] == "may-dispatch-to"
        and edge["source"] == symbols["service.dispatch"]["id"]
        and edge["target"] == symbols["base.Base.unique_method"]["id"]
        and edge["confidence"] == 0.55
        for edge in relationships
    )
    assert graph["coverage"]["python_files"] == 2
    assert graph["coverage"]["symbol_nodes"] == 8
    assert graph["coverage"]["call_sites"] == 5
    assert graph["coverage"]["resolved_calls"] == 3
    assert graph["coverage"]["candidate_calls"] == 1
    assert graph["coverage"]["unresolved_calls"] == 1
    assert graph["coverage"]["inheritance_edges"] == 1


def test_analyze_repository_discovers_fastapi_dependency_and_call_flows(
    tmp_path: Path,
) -> None:
    (tmp_path / "dependencies.py").write_text(
        """def get_repository():
    return Repository()

class Repository:
    def save(self):
        return None
""",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        """def create_item(repository):
    repository.save()
    external.audit()
""",
        encoding="utf-8",
    )
    (tmp_path / "api.py").write_text(
        """from typing import Annotated
from fastapi import APIRouter, Depends
from dependencies import get_repository
from service import create_item

router = APIRouter()

@router.post('/items')
def create_item_route(
    repository: Annotated[object, Depends(get_repository)],
    query: Annotated[object, Depends()] = None,
):
    return create_item(repository)
""",
        encoding="utf-8",
    )

    graph = analyzer.analyze_repository(tmp_path)
    symbols = {
        node["qualified_name"]: node
        for node in graph["nodes"]
        if node["kind"] != "file"
    }
    route = symbols["api.create_item_route"]

    assert graph["schema_version"] == "0.6"
    assert route["entrypoint"] == {
        "framework": "fastapi",
        "kind": "route",
        "method": "POST",
        "route_path": "/items",
        "label": "POST /items",
    }
    assert route["architectural_role"] == "route"
    assert route["entrypoint_evidence"]["line"] == 8

    dependency_edge = next(
        edge for edge in graph["edges"] if edge["kind"] == "depends-on"
    )
    assert dependency_edge["source"] == route["id"]
    assert dependency_edge["target"] == symbols["dependencies.get_repository"]["id"]
    assert dependency_edge["evidence"]["expression"] == "Depends(get_repository)"

    assert graph["coverage"]["fastapi_routes"] == 1
    assert graph["coverage"]["dependency_edges"] == 1
    assert graph["coverage"]["unresolved_dependencies"] == 1
    assert graph["coverage"]["representative_flows"] == 2
    assert len(graph["flows"]) == 2

    service_flow = next(
        flow for flow in graph["flows"]
        if symbols["service.create_item"]["id"] in flow["ordered_node_ids"]
    )
    assert service_flow["label"] == "POST /items"
    assert service_flow["framework"] == "fastapi"
    assert service_flow["ordered_node_ids"] == [
        route["id"],
        symbols["service.create_item"]["id"],
        symbols["dependencies.Repository.save"]["id"],
    ]
    assert service_flow["confidence"] == 0.55
    assert service_flow["completeness"] == "partial"
    assert any(
        step["evidence"]["expression"] == "external.audit"
        for step in service_flow["unresolved_steps"]
    )
    assert any(
        step["reason"] == "implicit-fastapi-dependency"
        for step in service_flow["unresolved_steps"]
    )


def test_analyze_repository_extracts_sqlalchemy_models_and_accesses(
    tmp_path: Path,
) -> None:
    (tmp_path / "models.py").write_text(
        '''from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class AbstractModel(Base):
    __abstract__ = True

class UserModel(AbstractModel):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    manager: Mapped["UserModel"] = relationship()
''',
        encoding="utf-8",
    )
    (tmp_path / "repository.py").write_text(
        '''from sqlalchemy import select
from models import UserModel

async def load(session):
    return await session.get(UserModel, 1)

async def list_users(session):
    return await session.execute(select(UserModel))

def create(session):
    user: UserModel = UserModel()
    session.add(user)

async def rename(session):
    user = await session.get(UserModel, 1)
    user.name = "updated"
''',
        encoding="utf-8",
    )

    graph = analyzer.analyze_repository(tmp_path)
    symbols = {
        node["qualified_name"]: node
        for node in graph["nodes"]
        if node["kind"] != "file"
    }
    user_model = symbols["models.UserModel"]

    assert symbols["models.Base"]["sqlalchemy"]["kind"] == "declarative-base"
    assert symbols["models.AbstractModel"]["sqlalchemy"]["kind"] == "abstract-model"
    assert user_model["architectural_role"] == "model"
    assert user_model["sqlalchemy"] == {
        "kind": "model",
        "table_name": "users",
        "table_expression": "'users'",
        "is_abstract": False,
        "columns": [{"name": "id", "line": 11, "annotation": "Mapped[int]"}],
        "relationships": [
            {"name": "manager", "line": 12, "annotation": "Mapped['UserModel']"}
        ],
    }

    accesses = [edge for edge in graph["edges"] if edge["kind"] in {"reads", "writes"}]
    assert all(edge["target"] == user_model["id"] for edge in accesses)
    assert {
        (edge["source"], edge["kind"], edge["resolution_method"])
        for edge in accesses
    } == {
        (symbols["repository.load"]["id"], "reads", "sqlalchemy-session-get"),
        (symbols["repository.list_users"]["id"], "reads", "sqlalchemy-select"),
        (symbols["repository.create"]["id"], "writes", "sqlalchemy-session-add"),
        (symbols["repository.rename"]["id"], "reads", "sqlalchemy-session-get"),
        (symbols["repository.rename"]["id"], "writes", "sqlalchemy-model-mutation"),
    }
    assert graph["coverage"]["sqlalchemy_models"] == 1
    assert graph["coverage"]["sqlalchemy_abstract_models"] == 1
    assert graph["coverage"]["sqlalchemy_columns"] == 1
    assert graph["coverage"]["sqlalchemy_relationships"] == 1
    assert graph["coverage"]["sqlalchemy_reads"] == 3
    assert graph["coverage"]["sqlalchemy_writes"] == 2


def test_fastapi_flow_reaches_a_sqlalchemy_model(tmp_path: Path) -> None:
    (tmp_path / "models.py").write_text(
        '''from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class ItemModel(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
''',
        encoding="utf-8",
    )
    (tmp_path / "repository.py").write_text(
        '''from sqlalchemy import select
from models import ItemModel

def list_items():
    return select(ItemModel)
''',
        encoding="utf-8",
    )
    (tmp_path / "api.py").write_text(
        '''from fastapi import APIRouter
from repository import list_items

router = APIRouter()

@router.get("/items")
def list_items_route():
    return list_items()
''',
        encoding="utf-8",
    )

    graph = analyzer.analyze_repository(tmp_path)
    symbols = {
        node["qualified_name"]: node
        for node in graph["nodes"]
        if node["kind"] != "file"
    }
    persistence_flow = next(
        flow for flow in graph["flows"]
        if symbols["models.ItemModel"]["id"] in flow["ordered_node_ids"]
    )

    assert persistence_flow["ordered_node_ids"] == [
        symbols["api.list_items_route"]["id"],
        symbols["repository.list_items"]["id"],
        symbols["models.ItemModel"]["id"],
    ]
    persistence_edge = next(
        edge for edge in graph["edges"]
        if edge["id"] == persistence_flow["ordered_edge_ids"][-1]
    )
    assert persistence_edge["kind"] == "reads"
    assert persistence_edge["confidence"] == 0.98


def test_flow_discovery_marks_the_bounded_depth_as_an_explicit_gap() -> None:
    symbols = [
        {
            "id": f"symbol:{index}",
            "path": "chain.py",
            "definition_line": index + 1,
            "qualified_name": f"chain.step_{index}",
            **(
                {"entrypoint": {"label": "GET /chain", "framework": "fastapi"}}
                if index == 0 else {}
            ),
        }
        for index in range(11)
    ]
    edges = [
        {
            "id": f"call:{index}",
            "source": f"symbol:{index}",
            "target": f"symbol:{index + 1}",
            "kind": "calls",
            "confidence": 1.0,
        }
        for index in range(10)
    ]

    [flow] = analyzer.discover_representative_flows(symbols, edges, [])

    assert len(flow["ordered_edge_ids"]) == analyzer.MAX_FLOW_DEPTH
    assert flow["completeness"] == "partial"
    assert flow["unresolved_steps"][-1]["reason"] == "maximum-flow-depth-reached"



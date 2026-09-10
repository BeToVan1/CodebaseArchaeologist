import ast

import pytest

from analyzer import fastapi_route_metadata, fastapi_router_prefix_declaration, fastapi_local_inclusions


@pytest.mark.parametrize("arguments,path,label", [
    ("'/items'", "/items", "GET /items"),
    ("path='/items'", "/items", "GET /items"),
    ("''", "", "GET (empty path)"),
    ("path=''", "", "GET (empty path)"),
    ("path=PREFIX", None, "GET (dynamic path)"),
    ("'/first', path='/second'", None, "GET (dynamic path)"),
    ("**options", None, "GET (dynamic path)"),
    ("'/items', **options", "/items", "GET /items"),
    ("123", None, "GET (dynamic path)"),
])
def test_route_path_arguments_are_literal_or_explicitly_unknown(arguments, path, label):
    node = ast.parse(f"@router.get({arguments})\ndef route():\n    pass\n").body[0]
    metadata, evidence = fastapi_route_metadata(node, {"router"})
    assert metadata["route_path"] == path
    assert metadata["label"] == label
    assert evidence.lineno == 1


@pytest.mark.parametrize("arguments,expected", [
    ("prefix='/api'", "/api"), ("", ""), ("prefix=''", ""),
    ("prefix=PREFIX", None), ("**options", None),
    ("prefix='/api', **options", None), ("'/api'", None),
])
def test_router_prefix_is_a_source_declaration_only(arguments, expected):
    tree = ast.parse(f"router = Router({arguments})")
    result = fastapi_router_prefix_declaration(tree, {"Router": "fastapi.APIRouter"}, "router")
    assert result["prefix"] == expected
    assert result["line"] == 1
    assert result["expression"].startswith("Router(")


def test_reassigned_router_and_lookalikes_do_not_establish_prefix():
    for source, bindings in [
        ("router = Router(prefix='/api')\nrouter = other", {"Router": "fastapi.APIRouter"}),
        ("router = Router(prefix='/api')", {}),
    ]:
        assert fastapi_router_prefix_declaration(ast.parse(source), bindings, "router") is None


@pytest.mark.parametrize("arguments,expected", [
    ("router, prefix='/v1'", "/v1/api/items"),
    ("router=router, prefix='/v2'", "/v2/api/items"),
    ("router", "/api/items"),
    ("router, prefix=PREFIX", None),
    ("router, **options", None),
])
def test_local_inclusion_composes_parent_relative_declarations(arguments, expected):
    tree = ast.parse(f"app = App()\nrouter = Router(prefix='/api')\n@app.get('/other')\ndef other(): pass\napp.include_router({arguments})")
    result = fastapi_local_inclusions(tree, {"App": "fastapi.FastAPI", "Router": "fastapi.APIRouter"}, "router", "/items", 4)
    assert len(result) == 1
    assert result[0]["path"] == expected
    assert result[0]["parent"] == "app"
    assert result[0]["line"] == 5


def test_inclusion_before_route_is_unresolved_and_multiple_sites_remain_distinct():
    tree = ast.parse("app = App()\nrouter = Router()\napp.include_router(router)\napp.include_router(router, prefix='/v2')")
    result = fastapi_local_inclusions(tree, {"App": "fastapi.FastAPI", "Router": "fastapi.APIRouter"}, "router", "/items", 3)
    assert [item["path"] for item in result] == [None, "/v2/items"]


def test_conditional_and_unknown_parent_inclusions_are_not_composed():
    tree = ast.parse("router = Router()\nif flag:\n app.include_router(router)\nother.include_router(router)")
    assert fastapi_local_inclusions(tree, {"Router": "fastapi.APIRouter"}, "router", "/items", 1) == []


@pytest.mark.parametrize("mutation", [
    "app = other", "del app", "app.prefix = '/changed'",
    "router.prefix = '/changed'", "if flag:\n app = other",
])
def test_rebinding_or_direct_mutation_prevents_path_composition(mutation):
    tree = ast.parse("app = App()\nrouter = Router()\n" + mutation + "\napp.include_router(router)")
    result = fastapi_local_inclusions(tree, {"App": "fastapi.FastAPI", "Router": "fastapi.APIRouter"}, "router", "/items", 2)
    assert len(result) == 1
    assert result[0]["path"] is None

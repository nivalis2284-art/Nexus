"""Architecture guards.

These tests do not check behaviour; they check that the *shape* of the codebase
stays sound as features are added. Each one exists because it can silently rot:

* layers drifting into upward imports
* the tool catalog and the registry disagreeing
* a tool whose requirements are unmet still appearing in the schema
* a tool spec whose handler or argument model is malformed
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from pydantic import BaseModel

from nexus.tools import build, tool_specs

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "nexus"

# Lower number = lower layer. A module may depend on the same or a lower layer.
LAYERS = {
    "model": 0,
    "tools": 1,
    "session": 1,
    "core": 2,
    "web": 3,
}


def _module_edges() -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for path in PACKAGE.rglob("*.py"):
        parts = list(path.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        module = ".".join(parts)
        package = module if path.name == "__init__.py" else ".".join(module.split(".")[:-1])
        src = ".".join(module.split(".")[:2])
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")
                    if node.level > 1:
                        base = base[: len(base) - (node.level - 1)]
                    target = ".".join([*base, node.module]) if node.module else ".".join(base)
                elif node.module and node.module.startswith("nexus"):
                    target = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("nexus"):
                        target = alias.name
            if target and target.startswith("nexus"):
                edges.append((src, ".".join(target.split(".")[:2])))
    return edges


def test_no_upward_layer_imports():
    violations = []
    for src, dst in _module_edges():
        name_src = src.split(".")[-1]
        name_dst = dst.split(".")[-1]
        if name_src in LAYERS and name_dst in LAYERS:
            if LAYERS[name_dst] > LAYERS[name_src]:
                violations.append(f"{src} -> {dst}")
    assert not violations, "layer violation: " + ", ".join(violations)


def test_web_is_not_imported_by_lower_layers():
    for src, dst in _module_edges():
        if dst == "nexus.web":
            assert src == "nexus.web" or src == "nexus.__main__", (
                f"{src} must not import the web layer"
            )


def test_catalog_and_registry_agree_when_everything_is_configured():
    full_config = {
        "webfetch_hosts": ["example.com"],
        "search_endpoint": "https://search.test/q",
    }
    registry, _ = build({}, config=full_config)
    registered = {spec.name for spec in registry.specs()}
    declared = {spec.name for spec in tool_specs()}
    assert registered == declared


def test_optional_tools_absent_without_configuration():
    registry, _ = build({}, config={})
    names = {spec.name for spec in registry.specs()}
    assert "webfetch" not in names
    assert "websearch" not in names
    # Core tools are always present.
    assert {"read_file", "write_file", "edit_file", "bash"} <= names


def test_every_tool_spec_is_well_formed():
    for spec in tool_specs():
        assert spec.name and spec.name.islower() or "_" in spec.name
        assert isinstance(spec.description, str) and len(spec.description) > 10
        assert issubclass(spec.parameters, BaseModel)
        assert callable(spec.handler)
        # A schema must be JSON-serialisable, since it goes into the request body.
        schema = spec.schema()
        assert schema["name"] == spec.name
        assert schema["parameters"]["type"] == "object"


def test_tool_names_are_unique():
    names = [spec.name for spec in tool_specs()]
    assert len(names) == len(set(names))


def test_requires_mentioned_in_specs_resolve_from_config_only():
    """A declared requirement must be a key the assembly code actually provides."""
    provided = {"webfetch_hosts", "search_endpoint"}
    for spec in tool_specs():
        for key in spec.requires:
            assert key in provided, f"{spec.name} requires unknown key {key}"


def test_core_does_not_import_tools_implementation_details():
    """core may use the registry, but must not reach into individual tools."""
    allowed = {"nexus.tools", "nexus.tools.registry"}
    for src, dst in _module_edges():
        if src == "nexus.core" and dst.startswith("nexus.tools"):
            assert dst in allowed, f"core should not import {dst}"


def test_no_wildcard_imports():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "*", f"wildcard import in {path}"

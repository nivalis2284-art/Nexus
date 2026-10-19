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


def _module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_name(path: pathlib.Path, module: str) -> str:
    if path.name == "__init__.py":
        return module
    return module.rpartition(".")[0]


def _resolved_imports(
    path: pathlib.Path, module: str, node: ast.Import | ast.ImportFrom
) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    if not node.level:
        return [node.module] if node.module else []

    package = _package_name(path, module).split(".")
    keep = len(package) - (node.level - 1)
    if keep < 1:
        return []
    base = package[:keep]
    if node.module:
        return [".".join([*base, node.module])]
    return [".".join([*base, alias.name]) for alias in node.names]


def _edges_from_tree(path: pathlib.Path, module: str, source: str) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for target in _resolved_imports(path, module, node):
            if target == "nexus" or target.startswith("nexus."):
                edges.add((module, target))
    return edges


def _module_edges() -> list[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for path in PACKAGE.rglob("*.py"):
        module = _module_name(path)
        edges.update(_edges_from_tree(path, module, path.read_text(encoding="utf-8")))
    return sorted(edges)


def _layer(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) > 1 and parts[0] == "nexus":
        return parts[1]
    return None


def _layer_violations(edges: list[tuple[str, str]]) -> list[str]:
    violations = []
    for src, dst in edges:
        src_layer = _layer(src)
        dst_layer = _layer(dst)
        if (
            src_layer in LAYERS
            and dst_layer in LAYERS
            and LAYERS[dst_layer] > LAYERS[src_layer]
        ):
            violations.append(f"{src} -> {dst}")
    return violations


def test_edges_are_real_and_relative_imports_resolve():
    edges = set(_module_edges())
    assert edges
    assert ("nexus.__main__", "nexus.web.server") in edges
    # This import is relative (from ..model), so it only appears when the
    # source package is computed from the repository-relative module name.
    assert ("nexus.core.agent", "nexus.model") in edges
    assert ("nexus.core.effort", "nexus.model") in edges


def test_import_extraction_handles_relative_and_multiple_imports():
    source = """
import nexus.model, nexus.session.store
from .. import mcp, skills
from . import prompt, retry
from ..tools.registry import Registry, ToolResult
"""
    path = PACKAGE / "core" / "example.py"
    assert _edges_from_tree(path, "nexus.core.example", source) == {
        ("nexus.core.example", "nexus.mcp"),
        ("nexus.core.example", "nexus.model"),
        ("nexus.core.example", "nexus.session.store"),
        ("nexus.core.example", "nexus.skills"),
        ("nexus.core.example", "nexus.core.prompt"),
        ("nexus.core.example", "nexus.core.retry"),
        ("nexus.core.example", "nexus.tools.registry"),
    }


def test_layer_guard_can_discover_a_violation():
    source = "from ..web import server\nfrom ..tools import registry\n"
    path = PACKAGE / "core" / "violating.py"
    edges = _edges_from_tree(path, "nexus.core.violating", source)
    assert _layer_violations(sorted(edges)) == [
        "nexus.core.violating -> nexus.web",
    ]
    source = "from ..model import types\nfrom ..core.protocol import request_body\n"
    edges = _edges_from_tree(path, "nexus.core.violating", source)
    assert _layer_violations(sorted(edges)) == []


def test_no_upward_layer_imports():
    violations = _layer_violations(_module_edges())
    assert not violations, "layer violation: " + ", ".join(violations)


def test_web_is_not_imported_by_lower_layers():
    for src, dst in _module_edges():
        if dst == "nexus.web" or dst.startswith("nexus.web."):
            assert _layer(src) == "web" or src == "nexus.__main__", (
                f"{src} must not import the web layer"
            )


def test_catalog_and_registry_agree_when_everything_is_configured():
    full_config = {
        "webfetch_hosts": ["example.com"],
        "search_endpoint": "https://search.test/q",
    }
    registry, _ = build({}, config=full_config, attachment_source=(object(), "sessions", "s1"))
    registered = {spec.name for spec in registry.specs()}
    declared = {spec.name for spec in tool_specs()}
    assert registered == declared


def test_optional_tools_absent_without_configuration():
    registry, _ = build({}, config={})
    names = {spec.name for spec in registry.specs()}
    assert "webfetch" not in names
    assert "websearch" not in names
    assert "read_attachment" not in names
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
    provided = {"webfetch_hosts", "search_endpoint", "attachments"}
    for spec in tool_specs():
        for key in spec.requires:
            assert key in provided, f"{spec.name} requires unknown key {key}"


def test_core_does_not_import_tools_implementation_details():
    """core may use the registry, but must not reach into individual tools."""
    allowed = {"nexus.tools", "nexus.tools.registry"}
    for src, dst in _module_edges():
        if _layer(src) == "core" and dst.startswith("nexus.tools"):
            assert dst in allowed, f"core should not import {dst}"


def test_no_wildcard_imports():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "*", f"wildcard import in {path}"

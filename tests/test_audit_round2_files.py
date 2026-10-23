from types import SimpleNamespace

from nexus.tools import files
from nexus.tools.registry import MAX_RESULT_CHARS, Registry


def context(tmp_path):
    return SimpleNamespace(root=tmp_path, policy=None)


def test_large_file_reports_continuation_before_registry_clipping(tmp_path):
    path = tmp_path / "big.py"
    path.write_text("\n".join(f"def fn_{index}(): pass # " + "x" * 50 for index in range(1200)),
                    encoding="utf-8")
    registry = Registry()
    registry.add(next(spec for spec in files.TOOLS if spec.name == "read_file"))
    result = registry.execute("read_file", {"path": "big.py"}, context(tmp_path))
    assert result.ok
    assert len(result.content) < MAX_RESULT_CHARS
    assert "continue with offset" in result.content
    assert result.data["returned"] < result.data["lines"]
    offset = result.data["returned"] + 1
    found = "fn_600" in result.content
    while offset <= 1200:
        page = registry.execute("read_file", {"path": "big.py", "offset": offset}, context(tmp_path))
        found |= "fn_600" in page.content
        assert page.data["returned"] > 0
        offset += page.data["returned"]
    assert found


def test_glob_distinguishes_recursive_patterns_and_includes_root(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.py").write_text("root")
    (tmp_path / "sub" / "inner.py").write_text("inner")
    ctx = context(tmp_path)
    flat = files.glob_paths(ctx, {"path": ".", "pattern": "*.py"})
    recursive = files.glob_paths(ctx, {"path": ".", "pattern": "**/*.py"})
    assert flat["content"] == "top.py"
    assert recursive["content"].splitlines() == ["sub/inner.py", "top.py"]
    nested = files.glob_paths(ctx, {"path": "sub", "pattern": "*.py"})
    assert nested["content"] == "sub/inner.py"


def test_grep_file_path_does_not_scan_its_siblings(tmp_path):
    (tmp_path / "top.py").write_text("top")
    (tmp_path / "other.py").write_text("inner")
    value = files.grep(context(tmp_path), {"path": "top.py", "pattern": "inner",
                                        "ignore_case": False, "glob": "", "max_results": 20})
    assert value["matches"] == 0
    assert value["files_scanned"] == 1



def test_line_limit_reports_next_offset_and_preserves_truncated_flag(tmp_path):
    (tmp_path / "lines.txt").write_text("x\n" * 5000, encoding="utf-8")
    registry = Registry()
    registry.add(next(spec for spec in files.TOOLS if spec.name == "read_file"))
    result = registry.execute("read_file", {"path": "lines.txt"}, context(tmp_path))
    assert result.data["returned"] == 2000
    assert result.data["truncated"] is True
    assert "continue with offset 2001" in result.content
    last = registry.execute("read_file", {"path": "lines.txt", "offset": 4001}, context(tmp_path))
    assert last.data["truncated"] is False


def test_byte_and_long_line_truncation_flags_survive_dispatch(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 80000, encoding="utf-8")
    registry = Registry()
    registry.add(next(spec for spec in files.TOOLS if spec.name == "read_file"))
    result = registry.execute("read_file", {"path": "big.txt"}, context(tmp_path))
    assert result.data["truncated"] is True
    assert "line truncated" in result.content

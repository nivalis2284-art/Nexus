"""Frontend assets are present in existing built wheels."""
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_built_wheel_contains_every_frontend_asset():
    wheels = list((ROOT / ".artifacts/plugin-ui/dist").glob("nexus-*.whl"))
    if not wheels:
        import pytest
        pytest.skip("No existing Nexus wheel to inspect")
    with zipfile.ZipFile(max(wheels, key=lambda path: path.stat().st_mtime_ns)) as archive:
        names = set(archive.namelist())
        for folder in ("css", "js", "assets", "vendor"):
            for path in (ROOT / "nexus/web" / folder).iterdir():
                if path.is_file():
                    assert path.relative_to(ROOT).as_posix() in names
        assert "nexus/web/js/plugin-ui.js" in names
        assert "nexus/web/css/plugin-ui.css" in names

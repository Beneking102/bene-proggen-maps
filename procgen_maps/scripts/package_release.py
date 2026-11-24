"""Standalone release packager: zips the addon folder for GitHub Releases.

Usage: python procgen_maps/scripts/package_release.py [output_dir]
(default output_dir: <repo>/dist).

Deliberately dependency-free (no numpy/bpy import) so it can run in any
plain Python 3 interpreter, independent of the Blender/pytest environments
used by the rest of the addon. Reads bl_info's version via regex rather
than importing procgen_maps, for the same reason.
"""
import re
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ADDON_DIR = REPO_ROOT / "procgen_maps"
DIST_DIR = REPO_ROOT / "dist"

VERSION_RE = re.compile(r'"version"\s*:\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)')


def read_version() -> str:
    """The addon version string from bl_info (regex, no import)."""
    text = (ADDON_DIR / "__init__.py").read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    return "{}.{}.{}".format(*match.groups())


def build_zip(version: str, dist_dir: Path = DIST_DIR) -> Path:
    """Write <dist_dir>/procgen_maps-v<version>.zip (sources only, no caches
    or tests' outputs); returns its path."""
    dist_dir.mkdir(parents=True, exist_ok=True)
    out_path = dist_dir / f"procgen_maps-v{version}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ADDON_DIR.rglob("*"):
            if path.is_dir():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            zf.write(path, path.relative_to(REPO_ROOT))
    return out_path


if __name__ == "__main__":
    import sys

    version = read_version()
    zip_path = build_zip(version, Path(sys.argv[1]) if len(sys.argv) > 1 else DIST_DIR)
    print(zip_path)

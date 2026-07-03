"""Headless Blender smoke test for the procgen_maps addon.

Run with:
    blender --background --factory-startup --python procgen_maps/tests/blender_integration/run_smoke_tests.py

Not a pytest file (pytest never runs inside Blender's interpreter here) -
a plain script that registers the addon, exercises every generator and
exporter operator, and exits 0/1 so it can gate CI. The pure-Python logic
these operators build on is covered separately by procgen_maps/tests/test_*.py.
"""
import os
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import bpy  # noqa: E402

import procgen_maps  # noqa: E402

_FAILURES = []
_SKIPPED = []


def _check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        _FAILURES.append(label)


def _run_stage(label, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - smoke test wants to report, not raise
        print(f"[FAIL] {label}: {exc}")
        _FAILURES.append(label)


def _run_optional_export(label, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] {label}: {exc}")
        _SKIPPED.append(label)


def main():
    procgen_maps.register()

    scene = bpy.context.scene
    settings = scene.procgen_maps
    settings.preset = "DORF"
    settings.seed = 7

    def generate_terrain():
        result = bpy.ops.procgen_maps.generate_terrain()
        _check("generate_terrain FINISHED", result == {'FINISHED'})
        terrain_obj = bpy.data.objects.get("ProcgenMaps_Terrain")
        _check("terrain object created", terrain_obj is not None)
        _check("terrain has vertices", terrain_obj is not None and len(terrain_obj.data.vertices) > 0)

    def generate_city():
        result = bpy.ops.procgen_maps.generate_city()
        _check("generate_city FINISHED", result == {'FINISHED'})
        _check("city produced objects", settings.stat_objects > 0)
        _check("city produced vertices", settings.stat_vertices > 0)

    def generate_dungeon():
        result = bpy.ops.procgen_maps.generate_dungeon()
        _check("generate_dungeon FINISHED", result == {'FINISHED'})
        dungeon_root = bpy.data.collections.get("ProcgenMaps_Dungeon")
        _check("dungeon collection created", dungeon_root is not None)
        _check("dungeon has objects", dungeon_root is not None and len(dungeon_root.objects) > 0)

    def night_mode():
        settings.night_mode = True
        _check("night mode enabled", settings.night_mode is True)
        settings.night_mode = False
        _check("night mode disabled", settings.night_mode is False)

    _run_stage("Generate terrain", generate_terrain)
    _run_stage("Generate city", generate_city)
    _run_stage("Generate dungeon", generate_dungeon)
    _run_stage("Toggle night mode", night_mode)

    with tempfile.TemporaryDirectory(prefix="procgen_maps_smoke_") as tmp_dir:
        settings.export_directory = tmp_dir

        def export_and_check(op_name, filename):
            filepath = os.path.join(tmp_dir, filename)
            op = getattr(bpy.ops.procgen_maps, op_name)
            result = op()
            _check(f"{op_name} FINISHED", result == {'FINISHED'})
            _check(f"{filename} written and non-empty",
                   os.path.isfile(filepath) and os.path.getsize(filepath) > 0)

        _run_stage("Export glTF", lambda: export_and_check("export_gltf", "procgen_maps.glb"))
        _run_stage("Export JSON", lambda: export_and_check("export_json", "procgen_maps.json"))
        _run_stage("Export SVG", lambda: export_and_check("export_svg", "procgen_maps.svg"))
        _run_optional_export("Export FBX", lambda: export_and_check("export_fbx", "procgen_maps.fbx"))
        _run_optional_export("Export USDZ", lambda: export_and_check("export_usdz", "procgen_maps.usdz"))
        _run_stage("Render showcase", lambda: export_and_check("render_showcase", "procgen_maps_showcase.png"))

    procgen_maps.unregister()
    _check("no leftover Scene.procgen_maps after unregister", not hasattr(bpy.types.Scene, "procgen_maps"))

    print("\n--- Summary ---")
    print(f"Failures: {len(_FAILURES)}")
    for label in _FAILURES:
        print(f"  FAIL: {label}")
    print(f"Skipped (optional): {len(_SKIPPED)}")
    for label in _SKIPPED:
        print(f"  SKIP: {label}")

    if _FAILURES:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

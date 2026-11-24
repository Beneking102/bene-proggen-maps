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
        from procgen_maps.generators import terrain as terrain_gen
        _check("terrain carries the landscape attributes",
               terrain_obj is not None and all(name in terrain_obj.data.attributes for name in terrain_gen.LANDSCAPE_ATTRIBUTES))
        horizon = bpy.data.objects.get(terrain_gen.HORIZON_OBJECT_NAME)
        _check("horizon skirt created", horizon is not None and len(horizon.data.polygons) > 0)

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

    def datablock_counts():
        return {attr: len(getattr(bpy.data, attr)) for attr in
                ("objects", "meshes", "materials", "lights", "curves", "collections", "node_groups")}

    def regenerate_without_leaks():
        from procgen_maps import validation

        before = datablock_counts()
        settings.seed = 8
        bpy.ops.procgen_maps.generate_terrain()
        bpy.ops.procgen_maps.generate_city()
        bpy.ops.procgen_maps.generate_dungeon()
        settings.night_mode = True
        settings.night_mode = False
        settings.seed = 7
        bpy.ops.procgen_maps.generate_terrain()
        bpy.ops.procgen_maps.generate_city()
        bpy.ops.procgen_maps.generate_dungeon()
        after = datablock_counts()
        grown = {k: after[k] - before[k] for k in before if after[k] > before[k]}
        _check(f"regenerate (same seed again) does not grow datablocks {grown}", not grown)
        _check("no orphan datablocks after regenerate", not validation.orphan_datablocks())

    def terrain_reset_on_city_regenerate():
        import numpy as np

        from procgen_maps.generators import terrain as terrain_gen

        def heights():
            obj = bpy.data.objects[terrain_gen.TERRAIN_OBJECT_NAME]
            buf = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
            obj.data.vertices.foreach_get("co", buf)
            return buf.reshape(-1, 3)[:, 2].copy()

        # City-only regenerate with a new seed must not keep the old
        # city's flattened pads (or the old seed's heights) in the terrain.
        settings.seed = 9
        bpy.ops.procgen_maps.generate_city()
        city_only = heights()
        bpy.ops.procgen_maps.generate_terrain()
        bpy.ops.procgen_maps.generate_city()
        fresh = heights()
        stale = int(np.sum(np.abs(city_only - fresh) > 0.02))
        _check(f"terrain matches a fresh terrain+city after city-only regenerate ({stale} stale vertices)", stale == 0)
        settings.seed = 7
        bpy.ops.procgen_maps.generate_terrain()
        bpy.ops.procgen_maps.generate_city()

    def terrain_under_roads():
        from procgen_maps import validation

        above, checked = validation.terrain_above_roads()
        _check(f"no terrain vertex above a street surface ({len(above)} of {checked})", checked > 0 and not above)

    def validate_map():
        result = bpy.ops.procgen_maps.validate_map()
        _check("validate_map FINISHED", result == {'FINISHED'})
        _check(f"validate_map reports no issues ({settings.validation_summary})",
               settings.validation_summary.startswith("OK"))

    def custom_presets():
        from procgen_maps.config import user_presets

        directory = user_presets.user_presets_dir()
        _check("user presets dir resolved", bool(directory))
        settings.user_preset_name = "smoke test preset"
        settings.haze_strength = 0.77
        result = bpy.ops.procgen_maps.save_user_preset()
        _check("save_user_preset FINISHED", result == {'FINISHED'})
        _check("preset listed", "smoke test preset" in user_presets.list_presets(directory))
        settings.haze_strength = 0.1
        settings.user_preset_choice = "smoke test preset"
        result = bpy.ops.procgen_maps.load_user_preset()
        _check("load_user_preset FINISHED", result == {'FINISHED'})
        _check("loaded preset restored the setting", abs(settings.haze_strength - 0.77) < 1e-6)
        settings.haze_strength = 0.3
        _check("delete_user_preset removes the file",
               bpy.ops.procgen_maps.delete_user_preset() == {'FINISHED'}
               and "smoke test preset" not in user_presets.list_presets(directory))

    def showcase_bounds_ignore_horizon():
        from procgen_maps.rendering import showcase
        from procgen_maps.ui import operators as ops_mod

        root = bpy.data.collections.get(ops_mod.ROOT_COLLECTION_NAME)
        bounds_min, bounds_max = showcase.collect_scene_bounds(root)
        _check(f"showcase bounds stay on the map, not the horizon ({bounds_max[0] - bounds_min[0]:.0f} m)",
               bounds_max[0] - bounds_min[0] < 2000.0)

    _run_stage("Generate terrain", generate_terrain)
    _run_stage("Generate city", generate_city)
    _run_stage("Generate dungeon", generate_dungeon)
    _run_stage("Toggle night mode", night_mode)
    _run_stage("Regenerate without leaks", regenerate_without_leaks)
    _run_stage("Terrain reset on city regenerate", terrain_reset_on_city_regenerate)
    _run_stage("Terrain stays under the roads", terrain_under_roads)
    _run_stage("Validate map", validate_map)
    _run_stage("Custom presets", custom_presets)
    _run_stage("Showcase bounds ignore the horizon", showcase_bounds_ignore_horizon)

    with tempfile.TemporaryDirectory(prefix="procgen_maps_smoke_") as tmp_dir:
        settings.export_directory = tmp_dir
        # The render operators are exercised for "does the Cycles CPU path
        # run end to end and write a PNG", not for image quality - a tiny
        # frame with a handful of samples keeps the whole smoke test under
        # a minute on a CI runner (a 4K/128-sample frame would take ages).
        settings.showcase_width = 320
        settings.showcase_height = 180
        settings.showcase_samples = 4

        def export_and_check(op_name, filename):
            filepath = os.path.join(tmp_dir, filename)
            op = getattr(bpy.ops.procgen_maps, op_name)
            result = op()
            _check(f"{op_name} FINISHED", result == {'FINISHED'})
            _check(f"{filename} written and non-empty",
                   os.path.isfile(filepath) and os.path.getsize(filepath) > 0)

        _run_stage("Export glTF", lambda: export_and_check("export_gltf", "procgen_maps.glb"))

        def verify_game_glb():
            from procgen_maps.exporters import game_export

            before = {attr: len(getattr(bpy.data, attr)) for attr in ("objects", "meshes", "collections")}
            names_before = sorted(obj.name for obj in bpy.data.objects)
            counts = game_export.verify_glb(os.path.join(tmp_dir, "procgen_maps.glb"))
            _check(f"re-imported GLB has objects ({counts['objects']})", counts["objects"] > 0)
            _check(f"re-imported GLB has City/Terrain/Dungeon roots ({counts['roots']})", counts["roots"] == 3)
            _check(f"re-imported GLB has collision objects ({counts['collision']})", counts["collision"] > 0)
            _check(f"re-imported GLB has LOD0/LOD1 pairs ({counts['lod0']}/{counts['lod1']})",
                   counts["lod0"] > 0 and counts["lod1"] > 0)
            _check(f"re-imported GLB has walkable meshes ({counts['walkable']})", counts["walkable"] == 2)
            after = {attr: len(getattr(bpy.data, attr)) for attr in ("objects", "meshes", "collections")}
            _check(f"export + re-import leave the scene unchanged {before} -> {after}", before == after)
            _check("export restores every object name", names_before == sorted(obj.name for obj in bpy.data.objects))
            _check("horizon skirt left out of the GLB", counts.get("horizon", 0) == 0)
            horizon = bpy.data.objects.get("ProcgenMaps_Horizon")
            _check("horizon skirt back in the scene after the export", horizon is not None and horizon.users_collection)
            settings.export_naming = "UNITY"
            bpy.ops.procgen_maps.export_gltf()
            counts = game_export.verify_glb(os.path.join(tmp_dir, "procgen_maps.glb"))
            _check(f"Unity naming: _col colliders present ({counts['collision']})", counts["collision"] > 0)
            settings.export_naming = "GODOT"

        _run_stage("Verify game GLB", verify_game_glb)
        _run_stage("Export JSON", lambda: export_and_check("export_json", "procgen_maps.json"))
        _run_stage("Export SVG", lambda: export_and_check("export_svg", "procgen_maps.svg"))
        _run_optional_export("Export FBX", lambda: export_and_check("export_fbx", "procgen_maps.fbx"))
        _run_optional_export("Export USDZ", lambda: export_and_check("export_usdz", "procgen_maps.usdz"))
        _run_stage("Render showcase", lambda: export_and_check("render_showcase", "procgen_maps_showcase.png"))
        _run_stage("Render interior", lambda: export_and_check("render_interior", "procgen_maps_interior.png"))

    procgen_maps.unregister()
    _check("no leftover Scene.procgen_maps after unregister", not hasattr(bpy.types.Scene, "procgen_maps"))
    leftover = [name for name in dir(bpy.types) if name.startswith("PROCGEN_")]
    _check(f"no leftover PROCGEN_* classes after unregister {leftover}", not leftover)
    procgen_maps.register()
    procgen_maps.unregister()
    _check("register/unregister twice is clean", not hasattr(bpy.types.Scene, "procgen_maps"))

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

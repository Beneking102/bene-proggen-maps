# Deployment

## Running headless

Procgen Maps registers/generates like any other Blender addon, so it works
the same way in `blender --background` as it does in the interactive
Editor - there is no interactive-only code path.

```
blender --background --factory-startup --python your_script.py
```

`--factory-startup` avoids interference from other add-ons or user
preferences; it's not required, but recommended for reproducible batch
runs. `your_script.py`:

```python
import bpy
import procgen_maps
from procgen_maps.config import presets

procgen_maps.register()

scene = bpy.context.scene
scene.procgen_maps.preset = "METROPOLE"
scene.procgen_maps.seed = 42

bpy.ops.procgen_maps.generate_terrain()
bpy.ops.procgen_maps.generate_city()
scene.procgen_maps.dungeon_theme = "castle"     # crypt | cave | castle
scene.procgen_maps.dungeon_levels = 2
bpy.ops.procgen_maps.generate_dungeon()

scene.procgen_maps.export_directory = "//export/"
scene.procgen_maps.export_naming = "GODOT"        # or "UNITY"
bpy.ops.procgen_maps.export_gltf()
bpy.ops.procgen_maps.export_json()

procgen_maps.unregister()
```

Notes:

- Every operator (`procgen_maps.generate_terrain`, `.generate_city`,
  `.generate_dungeon`, `.export_gltf`, `.export_fbx`, `.export_usdz`,
  `.export_svg`, `.export_json`) is a normal `bpy.types.Operator`, callable
  exactly as shown above from any headless script once `register()` has
  run.
- `scene.procgen_maps` is the addon's `PropertyGroup` (defined in
  `ui/__init__.py`) - set its fields before calling an operator to control
  preset/seed/toggles/export directory instead of relying on UI defaults.
- This is exactly the pattern `procgen_maps/tests/blender_integration/
  run_smoke_tests.py` uses to exercise the bpy-dependent build/export code
  paths that plain pytest can't reach (see `ARCHITECTURE.md`'s Testing
  section) - run it the same way, via a real `blender --background`
  invocation, not `python run_smoke_tests.py`. Pass `--python-exit-code 1`
  so an uncaught exception fails the process (Blender otherwise exits 0).
- `bpy.ops.procgen_maps.render_showcase()` / `.render_interior()` render
  with Cycles on the CPU (`scene.cycles.device = 'CPU'`, denoised), so they
  work on a machine without a GPU or display - the GitHub Actions
  `blender-smoke` job renders a 320x180 frame this way. Resolution and
  sample count come from `scene.procgen_maps.showcase_width/height/samples`.
  The previous EEVEE showcase needed an OpenGL context and aborted
  (SIGABRT, exit 134) on headless runners.
- `scene.procgen_maps.lighting_preset` (`AFTERNOON`, `MIDDAY`,
  `GOLDEN_HOUR`, `OVERCAST_SOFT`) sets sun, sky strength and film exposure
  together; `haze_strength` tunes the aerial perspective.

## Performance tuning knobs

All in `procgen_maps/config/settings.py`, as plain module-level constants
(there's no UI exposure for these yet - edit the file directly, or read
them from your own headless script before calling `bpy.ops.procgen_maps.*`
if you need per-run overrides):

| Constant | Default | Effect |
|---|---|---|
| `TERRAIN_DEFAULT_RESOLUTION` | 256 | Heightmap grid resolution (cells per side). Vertex/face count scales with the square of this - 256 means a 256x256 grid, i.e. ~65k verts. Lower for faster iteration, raise for smoother terrain silhouettes. |
| `TERRAIN_DEFAULT_WORLD_SIZE` | 1000.0 | World-space size (meters) the heightmap covers; `ui/operators.py` already grows this to `max(default, preset.radius * 2.5)` so large presets aren't clipped. |
| `TERRAIN_DEFAULT_SCALE` / `_OCTAVES` / `_PERSISTENCE` / `_LACUNARITY` | 120.0 / 5 / 0.5 / 2.0 | FBM noise shape. More octaves = more detail per generation, at roughly linear extra noise-eval cost. |
| `MAX_INSTANCES_BEFORE_LOD` | 200 | Prop count threshold above which distance-based LOD is expected to matter; informs when to bother calling `assets.factory.update_lod` at all in your own scripts/pipelines. |
| `LOD_DISTANCE_HIGH` | 40.0 | Meters from camera within which props stay at `"high"` detail. |
| `LOD_DISTANCE_MEDIUM` | 120.0 | Meters from camera within which props drop to `"medium"` detail; beyond this, `"low"`. |
| `DEFAULT_SPATIAL_CELL_SIZE` | 5.0 | `SpatialHashGrid` cell size (meters) used for prop-placement collision checks; too coarse under-rejects overlaps, too fine adds lookup overhead for no benefit. |

City-scale tuning (blocks, streets, density, zone mix) instead lives per
archetype in `procgen_maps/config/presets.py`'s `CityPreset` dataclass
entries (`PRESETS["METROPOLE"]`, etc.) - copy/adjust an existing preset
rather than fighting the global settings for city-shape changes.

## Export caveats

- **glTF/GLB**: wraps Blender's built-in glTF exporter
  (`bpy.ops.export_scene.gltf`), which ships with every Blender 4.2+
  install - no extra setup needed. Since v0.14 it is a game-engine export
  (`exporters/game_export.py`): set `scene.procgen_maps.export_naming`
  (`GODOT` / `UNITY`), `export_collision`, `export_lods`,
  `export_walkable` before calling `bpy.ops.procgen_maps.export_gltf()`.
  The GLB then holds `City` / `Terrain` / `Dungeon` root nodes,
  colliders (Godot: `Ground-col`, `Streets-col`, `<room>-col` trimesh
  colliders and `<building>-colonly` / `<prop>-colonly` boxes, which
  Godot's scene importer turns into StaticBody3D collision shapes;
  Unity: `_col` boxes under a `Collision` group - add BoxColliders with
  a one-line editor script, large meshes get MeshColliders by hand),
  `_LOD0`/`_LOD1` siblings under every building and prop instance (Unity
  builds LODGroups from that automatically; Godot users can key visibility
  ranges off the names), and `Ground_Walkable` / `Dungeon_Walkable`
  (`-navmesh` in Godot = a NavigationRegion3D; `_navmesh` in Unity = bake
  a NavMesh from it). Custom properties travel as glTF `extras`, lights
  as `KHR_lights_punctual`. Expect ~40 MB and ~5 s for a Dorf with all
  options on; the scene in Blender is unchanged after the export.
  `game_export.verify_glb(path)` re-imports a GLB headlessly and returns
  the counts (objects, collision, lod0/lod1, walkable, roots).
- **FBX**: wraps `bpy.ops.export_scene.fbx`, part of Blender's bundled
  `io_scene_fbx` add-on, which must be *enabled* (Preferences > Add-ons)
  even though it ships with Blender. `exporters/fbx_export.py`'s
  `is_fbx_available()` checks `hasattr(bpy.ops.export_scene, "fbx")`
  before exporting; the `Export FBX` operator reports an `ERROR` and
  cancels cleanly (rather than raising) if it's disabled.
- **USDZ**: wraps `bpy.ops.wm.usd_export`, Blender's native USD exporter
  (packaged since Blender 3.5), pointed at a `.usdz` path so Blender zips
  the USD payload and textures itself - no external Apple tooling needed
  to produce the file. This is explicitly a **best-effort** export:
  UDIM-tiled textures aren't supported inside a USDZ package, and passing
  Apple's own AR Quick Look validation on a real iOS device is stricter
  than anything Blender's exporter checks. Treat USDZ output as
  "well-formed, not device-verified" - spot-check it manually on real
  hardware/Quick Look before shipping, don't wire it into an automated CI
  gate as a pass/fail signal.
- **SVG**: pure Python, no external dependency; reduces each mesh to its
  world-space axis-aligned footprint rectangle before projecting top-down,
  so it's meant as a readable map overview, not a precise 2D trace.
- **JSON**: pure Python `json.dump` of the last-generate stats
  (preset/seed/object/vertex/face counts, generate time) - no caveats.

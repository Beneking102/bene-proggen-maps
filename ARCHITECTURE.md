# Architecture

## Tech stack

- Blender Python API: `bpy` (operators, panels, data blocks), `bmesh`
  (mesh building/extrusion), `mathutils` (vectors, matrices, and the
  `mathutils.kdtree.KDTree` fallback used by `utils/spatial.py`).
- `numpy` for heightmap/noise math and the raster city-block partition.
  Bundled with Blender's Python; pip-installable for standalone testing.
- No `scipy` anywhere, on purpose - it isn't bundled with Blender's Python.
  Anywhere a typical implementation would reach for `scipy.spatial.Voronoi`
  or a KD-tree, this codebase instead uses a numpy-vectorized nearest-seed
  raster scan (`generators/city/layout.py`) or `utils/spatial.py`'s
  `SpatialHashGrid` / `mathutils.kdtree` wrapper.

## Registration architecture

Every subpackage (`utils`, `config`, `assets`, `materials`, `generators`,
`exporters`, `ui`) exposes a module-level `register()` / `unregister()`
pair. Subpackages that define no `bpy.types.Operator`/`Panel` classes
(`assets`, `config`, `materials`, `exporters`, `generators/city`) still
implement these as no-ops, purely to satisfy the calling contract below -
it lets the root package treat every subpackage identically.

`procgen_maps/__init__.py` does **not** import `bpy` at module level, and
does not import its bpy-touching subpackages (`ui`, transitively
`generators`) at module level either. Instead, `_submodules()` performs
those imports lazily, inside `register()`/`unregister()` themselves:

```python
def _submodules():
    from . import assets, config, exporters, generators, materials, ui, utils
    return (utils, config, assets, materials, generators, exporters, ui)

def register():
    for module in _submodules():
        module.register()
```

This is why plain `import procgen_maps` never requires `bpy` to exist: the
pytest suite can import the package and any pure-Python submodule in a
normal virtualenv, and only actually invoking `register()` (which only
happens inside real Blender) touches `bpy`. The tuple order is a real
dependency order, not arbitrary: `utils`/`config` define no `bpy.types`
classes and go first; `assets`/`materials` are things generators build on
top of; `generators` must be registered before `ui` since `ui/operators.py`
calls into generator functions; `exporters` register last since
`ui/operators.py` only imports each exporter module lazily, per-export
button press, not at panel-registration time.

## The PP/BPY split convention

Every module that touches `bpy`/`bmesh`/`mathutils` keeps that import
local to the function that needs it, never at module level. This is what
makes the pure-Python "compute" half of each generator importable (and
pytest-testable) outside Blender entirely, while the "build" half stays a
thin, mechanical translation of already-validated plain data into Blender
meshes/objects.

Concrete example, `generators/terrain.py`:

```python
def generate_heightmap(params: TerrainParams):
    height01 = _noise.fbm_heightmap(params.resolution, params.world_size, ...)
    return height01 * params.max_height

def build_terrain_mesh(params: TerrainParams, collection):
    import bpy
    heights = generate_heightmap(params)
    ...
    mesh = bpy.data.meshes.new(TERRAIN_OBJECT_NAME)
    ...
```

`generate_heightmap` and `sample_world_height` are plain numpy functions
with no `bpy` import anywhere in the file's top level - the pytest suite
calls them directly. `build_terrain_mesh` is the only function in the file
that imports `bpy`, and it imports it as its first line, not at the top of
the module. The same split shows up in every generator: `layout.py`
(`generate_layout` vs. nothing - it's pure data, meshes come from
`streets.py`/`buildings.py`), `zones.py` (`classify_zones`, fully pure),
`streets.py` (`build_street_graph` pure vs. `build_street_meshes` +
`_build_road_strip`/`_build_intersection_fan` local-importing `bpy`/
`mathutils`), `buildings.py` (`plan_buildings` pure vs.
`build_building_meshes`/`_build_single_building` local-importing `bpy`/
`bmesh`), and `assets/factory.py` (`spawn`/`update_lod`/
`_get_or_build_instance_source`/`_build_primitive_mesh` all local-import
`bpy`/`bmesh`/`mathutils`, while `assets/library.py`'s asset table is pure
data with callables that return plain dicts).

The dungeon generator (`generators/dungeon.py`) follows the identical
pattern: `DungeonParams` (a frozen dataclass, `seed` plus BSP tuning
fields) and `generate_dungeon(params) -> DungeonLayout` are pure Python;
`build_dungeon_mesh(layout, collection)` and `clear_dungeon()` are the bpy
build/teardown pair, called from `ui/operators.py`'s
`PROCGEN_OT_generate_dungeon.execute()`.

## Generation dataflow

**Terrain** (`generators/terrain.py`) is standalone: `TerrainParams` in,
`generate_heightmap` produces a numpy heightmap, `build_terrain_mesh`
turns it into the `ProcgenMaps_Terrain` object via a single
`mesh.from_pydata` call (not a per-vertex bmesh loop, for speed).
`sample_world_height(x, y, params)` re-derives the same noise field
per-point, so other generators can query terrain height without needing
the mesh to exist yet or ray-casting against it.

**City** (`generators/city/__init__.py`'s `generate_city`) runs a fixed
pipeline, each stage consuming the previous stage's plain-data output:

```
CityPreset
  -> layout.generate_layout        (Block[], StreetSegment[])       [grid|raster]
  -> zones.classify_zones          ({block_id: zone_name})
  -> streets.build_street_graph    (StreetGraph: nodes/edges/degree)
  -> buildings.plan_buildings      (BuildingPlan[])
  -> props.plan_props              (PropPlacement[])
  -> streets.build_street_meshes   -> street mesh objects
  -> buildings.build_building_meshes -> building mesh objects
  -> props.build_props             -> prop Empty objects
```

If `terrain_params` is passed in, `build_street_meshes` and
`build_building_meshes` call `terrain.sample_world_height` per point/
footprint so streets and buildings sit on the terrain surface.

**Dungeon** (`generators/dungeon.py`, wired from
`PROCGEN_OT_generate_dungeon`) is a fully independent path: BSP-partition
a bounding area into rooms, connect them with corridors, and build the
result straight to mesh - it shares no data or collections with the
terrain/city pipeline.

## Object and collection naming conventions

Collection hierarchy actually produced at runtime:

```
Scene Collection
├─ ProcgenMaps                      (root; created by ui/operators.py's
│                                     _root_collection(); holds the
│                                     terrain object and, when generated,
│                                     the dungeon mesh objects/collection)
│   ├─ ProcgenMaps_Terrain           (terrain mesh object)
│   └─ ProcgenMaps_Dungeon           (dungeon mesh objects, once generated)
└─ ProcgenMaps_City                  (city root; created directly under
                                       the Scene Collection by
                                       generators/city/__init__.py's
                                       generate_city, as its own top-level
                                       sibling of ProcgenMaps rather than
                                       nested inside it)
    ├─ ProcgenMaps_City_Streets
    ├─ ProcgenMaps_City_Buildings
    └─ ProcgenMaps_City_Props
```

Object name prefixes:

- `ProcgenMaps_Terrain` - the single terrain mesh object
  (`generators/terrain.py`'s `TERRAIN_OBJECT_NAME`).
- `ProcgenMaps_Building_<index>` - one per generated building
  (`buildings.py`'s `BUILDINGS_PREFIX`).
- `ProcgenMaps_Street_Seg<index>` / `ProcgenMaps_Street_Node<index>` - one
  road-strip object per street-graph edge, one intersection fan per
  3+-degree node (`streets.py`'s `STREETS_PREFIX`).
- `<asset_id>_inst` - one Empty per placed prop, e.g. `tree_03_inst`,
  `street_lamp_inst`, `parked_car_inst` (`assets/factory.py`'s `spawn`).
- `ProcgenMaps_AssetSrc_<asset_id>_<detail>` /
  `ProcgenMaps_AssetMaster_<asset_id>_<detail>` - the never-linked source
  collection and master mesh object each (asset_id, LOD detail) pair
  builds exactly once (`assets/factory.py`'s
  `_get_or_build_instance_source`).
- `<street_lamp_object_name>_light` - the real `POINT` light Blender
  object spawned next to a street lamp when Night Mode is enabled
  (`ui/operators.py`'s `_LAMP_LIGHT_SUFFIX`).

Custom object properties (all plain Python values stored via `obj["key"]`,
read back with `obj.get("key")`):

| Property | Set on | Purpose |
|---|---|---|
| `procgen_maps_seed`, `procgen_maps_world_size`, `procgen_maps_max_height` | terrain object | recorded generation parameters |
| `procgen_maps_facade_type` | building objects | facade archetype key (e.g. `"glass_tower"`), read by tooling/inspection |
| `procgen_maps_material_index` | building objects | index into `city_mat.py`'s facade color ramp, read live by the shared material's Attribute node |
| `procgen_maps_tint` | building objects | per-building random 0..1 value, mixed into the facade color as a +-15% brightness variation so same-facade buildings don't look identical |
| `procgen_maps_block_id` | building objects | originating `Block.id`, for traceability back to the layout |
| `procgen_maps_street_class` | street segment objects | `"arterial"` or `"local"`, used for width and prop offset |
| `procgen_maps_asset_id` | prop Empties | which `assets/library.py` entry this instance is, e.g. `"street_lamp"` - also used to find lamps for Night Mode's point lights |
| `procgen_maps_lod_detail` | prop Empties | current LOD tier (`"high"`/`"medium"`/`"low"`), compared against in `update_lod` to skip no-op re-assignments |

## Testing

- `procgen_maps/tests/*.py`: a plain pytest suite covering every
  pure-Python "compute" half described above (`generate_heightmap`,
  `generate_layout`, `classify_zones`, `build_street_graph`,
  `plan_buildings`, `plan_props`, `SpatialHashGrid`, `pack_rects`,
  `build_svg_document`, preset lookups, and so on) - runs in any Python
  3.11 virtualenv with `numpy`/`pytest` installed, no Blender required.
- `procgen_maps/tests/blender_integration/run_smoke_tests.py`: a separate
  headless-Blender script that exercises the bpy-dependent "build" half
  (mesh building, material node setup, collection linking, the exporters)
  by actually running inside `blender --background`, since that half is
  by design not importable from plain pytest. See `DEPLOYMENT.md` for how
  to invoke Blender headlessly.

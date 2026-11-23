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

**City** (`generators/city/__init__.py`) is split into `plan_city` - every
pure planning stage, returning a `CityPlan` dataclass with no bpy anywhere
- and `generate_city`, which calls it and then builds the meshes. The
pipeline, each stage consuming the previous stage's plain-data output:

```
CityPreset
  -> layout.generate_layout        (Block[], StreetSegment[])       [grid|raster]
  -> zones.classify_zones          ({block_id: zone_name})
  -> streets.build_street_graph    (StreetGraph: nodes/edges/degree)
  -> special_buildings.plan_special_buildings / buildings.plan_buildings
                                   (BuildingPlan[]; both take the StreetGraph and
                                    pull footprints back from the roads via setback.py)
  -> parks.plan_parks              (ParkSpec[]: park with a path cross, or plaza)
  -> props.plan_props              (PropPlacement[]; + forest ring outside layout.radius;
                                    keeps off the river/banks and the park paths)
  -> signage.plan_signage          (SignPlacement[]; none on the river/banks)
  -> streets.build_street_meshes   -> ONE street-network object (asphalt + sidewalk/curb
                                      + skirt + fans + corner pieces, 2 UV maps, per-face attrs)
  -> water.build_water_mesh / build_bridges -> water strip + one bridges object
  -> parks.build_park_surfaces     -> one paths/plazas object (sidewalk paving material)
  -> buildings.build_building_meshes -> building mesh objects (5 material slots)
  -> props.build_props             -> prop Empty objects
```

`layout.generate_layout` also plans the **river** (`plan_river` ->
`RiverSpec`): the widest column of the middle third of the x street lines
gets a meandering centerline, its cells get no lots but keep their
streets, and only every second cross street inside the column is kept -
those are the bridges (`water.bridge_edges`), so the street graph stays
one component. Water plus bank keep clear of the two bounding streets by
construction. `preset.water` ("river"/"none"; the panel's River toggle
sets "none") turns it off.

Buildings' night lighting is planned per floor and per face in
`buildings.plan_floor_lighting` / `window_lighting` (pure Python): a
floor is dark, a lit band (offices) or a mix of lit/dark apartments
(runs of 2-3 bays), warm or cool. `_carve_floor_windows` writes the
result into two per-face float layers that end up as mesh attributes
(`procgen_maps_window_lit` / `_warm`); the shared window material reads
them with Attribute nodes in GEOMETRY mode. Roof shape
(`pick_roof_shape`: flat / gable / hip / sawtooth), balcony ribbons and
shop awnings are drawn from the same per-building `detail_rng` streams.

`layout.py`'s "grid" mode is a jittered regular lattice; "raster" mode
(Metropole, Industrial) is an irregular grid - uneven line spacing,
clipped to a circle, with some lots merged into bigger blocks - whose
street graph is guaranteed to be one connected component with real
intersections (the earlier Voronoi-spoke network had none; see the
function's docstring). `setback.py` is pure rectangle-vs-road geometry
(`rect_segment_distance`, `shrink_footprint_for_setback`) that keeps a
sidewalk's width of clearance between every footprint and the road edge.

If `terrain_params` is passed in, `build_street_meshes` and
`build_building_meshes` call `terrain.sample_world_height` per point/
footprint so streets and buildings sit on the terrain surface, and the
terrain mesh itself is reshaped to agree with them: flattened under
every building footprint, sunk under every street corridor (0.7 m under
the asphalt, rising to 0.2 m at the sidewalk's outer edge - see
`terrain.corridor_profile`; the sidewalk's own skirt hides the step) and
sunk 2.6 m under the river with the bank as the blend
(`terrain.flatten_terrain_for_footprints`, `lower_terrain_under_corridors`).

**Materials** are all shared, parametrized node setups created once by a
`get_or_create_*` function and keyed off per-object custom properties or
per-mesh UV data rather than duplicated per object: `city_mat.py`
(facade + window glass + roof), `road_mat.py` (asphalt with markings,
sidewalk concrete; driven by the `ProcgenMaps_Road`/`ProcgenMaps_RoadEnds`
UV maps and `procgen_maps_road_*` properties), `terrain_mat.py`,
`prop_mat.py` (per-instance variation through Object Info > Random),
`dungeon_mat.py`. Every outdoor material's surface shader is routed
through `atmosphere.py`'s single `ProcgenMaps_AerialPerspective` node
group, so the Lighting panel's haze slider and sun presets retune the
whole scene by editing two nodes.

**Validation** (`validation.py`) follows the same split:
`validate_city_plan(plan, preset)` checks a `CityPlan` for overlaps
(building/building, building/road, prop/building, prop/road, prop/prop,
sign/building, sign/road, parking/road, parking/building), disconnected
street components and degenerate edges - pure Python, run by
`tests/test_validation.py` over many seeds and every preset.
`validate_scene` adds the bpy-only checks (object z against
`sample_world_height`, terrain vertices above a street surface via
`terrain_above_roads`' BVH ray casts, orphan datablocks). `ui/operators.py`'s
`PROCGEN_OT_validate_map` runs both and writes the summary to
`settings.validation_summary`.

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
    ├─ ProcgenMaps_City_Props         (props + the ProcgenMaps_Parks paths/plazas object)
    ├─ ProcgenMaps_City_Signage
    ├─ ProcgenMaps_City_Parking
    └─ ProcgenMaps_City_Water         (ProcgenMaps_Water, ProcgenMaps_Bridges)
```

Object name prefixes:

- `ProcgenMaps_Terrain` - the single terrain mesh object
  (`generators/terrain.py`'s `TERRAIN_OBJECT_NAME`).
- `ProcgenMaps_Building_<index>` - one per generated building
  (`buildings.py`'s `BUILDINGS_PREFIX`).
- `ProcgenMaps_Street_Network` - the whole street network as one mesh
  object (`streets.py`'s `STREET_NETWORK_OBJECT_NAME`): per edge an
  asphalt strip (material slot 0) plus both sidewalks + curbs (slot 1),
  per node an asphalt fan sized by that node's widest street and the
  sidewalk corner pieces (`corner_sidewalk_paths`). Before v0.12.0 this
  was one object per edge and per 3+-degree node.
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
| `procgen_maps_base_z`, `procgen_maps_floor_height` | building objects | ground height and floor pitch, read by the facade shader's grime band and floor-slab lines |
| `procgen_maps_roof_index` | building objects | 0 = flat gravel roof, 1-3 = tile colors for gabled roofs (`city_mat.py`'s roof ramp) |
| `procgen_maps_center_x`, `procgen_maps_center_y` | building objects | where `base_z` was sampled (the footprint centroid; balconies/awnings shift the bounding box), read by `validate_scene` |
| `procgen_maps_window_lit`, `procgen_maps_window_warm` | per-face float *mesh attributes* on building meshes | night glow 0..1 and warm/cool 0..1 per window pane (0 on every non-window face), read by the window material's Attribute nodes |
| `procgen_maps_water`, `procgen_maps_bridges`, `procgen_maps_parks` | the water / bridges / parks objects | marker properties (kind, bridge count, park count) |
| `procgen_maps_street_class` | the street-network object | `"network"` (marks it for validation's ground-contact check); `procgen_maps_street_edges` / `_nodes` carry the graph size |
| `procgen_maps_road_width`, `procgen_maps_road_lanes` | per-face float *mesh attributes* on the street-network object | meters / 1 (local) or 2 (arterial), 0 on sidewalks and fans; drive the asphalt shader's marking layout (Attribute node, GEOMETRY mode) |
| `procgen_maps_asset_id` | prop Empties | which `assets/library.py` entry this instance is, e.g. `"street_lamp"` - also used to find lamps for Night Mode's point lights |
| `procgen_maps_lod_detail` | prop Empties | current LOD tier (`"high"`/`"medium"`/`"low"`), compared against in `update_lod` to skip no-op re-assignments |

## Testing

- `procgen_maps/tests/*.py`: a plain pytest suite covering every
  pure-Python "compute" half described above (`generate_heightmap`,
  `generate_layout`, `classify_zones`, `build_street_graph`,
  `plan_buildings`, `plan_props`, `SpatialHashGrid`, `pack_rects`,
  `build_svg_document`, preset lookups, and so on) - runs in any Python
  3.11 virtualenv with `numpy`/`pytest` installed, no Blender required.
  `test_validation.py` is the fuzz layer: `plan_city` + `validate_city_plan`
  over every preset and a dozen seeds (0 and 2^31-1 included), plus
  determinism and the regression tests behind each validator rule.
  `test_city_visuals.py` covers the v0.13 planning: night-window runs,
  roof shapes, parks/plazas, the river (bridges, connectivity, no lots in
  water, the validator rules) and the road-bed profile.
- `procgen_maps/tests/blender_integration/run_smoke_tests.py`: a separate
  headless-Blender script that exercises the bpy-dependent "build" half
  (mesh building, material node setup, collection linking, the exporters)
  by actually running inside `blender --background`, since that half is
  by design not importable from plain pytest. See `DEPLOYMENT.md` for how
  to invoke Blender headlessly.

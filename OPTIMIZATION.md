# Optimization

## v0.13.0 profile (headless Blender 5.2, idle priority, 8 threads, seed 42)

Same method and machine as the v0.12.0 table below, second of two runs.
v0.13 is a visuals release and adds geometry everywhere (river + bridges,
park paths/plazas, balconies, awnings, rooftop gear, sidewalk skirts, 3 m
instead of 6 m road chunks), so the point of this table is to show what
that cost, not a speed-up:

| Preset | v0.12.0 total | v0.13.0 total | city only | objects | verts |
|---|---|---|---|---|---|
| Metropole | 4.98 s | **5.58 s** | 4.22 -> 4.80 s | 6,655 -> 6,993 | 1.09 M -> 1.12 M |
| Kleinstadt | 1.65 s | **1.86 s** | 1.29 -> 1.50 s | 5,063 -> 5,021 | 331 k -> 352 k |
| Dorf | 0.80 s | 0.87 s | 0.55 -> 0.62 s | 2,249 -> 2,206 | 288 k -> 295 k |
| Industrial | 0.94 s | 1.08 s | 0.65 -> 0.80 s | 2,148 -> 2,015 | 345 k -> 354 k |

The ~0.6 s the Metropole gained is spread over the finer road chunks and
skirts (~0.2 s, pure-Python vertex lists), the balcony/awning boxes and
roof shapes inside `_build_single_building` (~0.2 s) and the river, bridge
and park meshes plus the second terrain-lowering pass (~0.1 s). The
window-lighting plan and the per-face attribute layers are free: the
lighting is drawn once per floor and written into the already-existing
bmesh faces. The sub-4 s Metropole still needs the per-block building
builder described under v0.12.0; nothing in this release changed where
the time goes (~80% `_build_single_building`).

## v0.12.0 profile (headless Blender 5.2, idle priority, 8 threads, seed 42)

Same method as the v0.10.0 table below (`generate_terrain` +
`generate_city` + `generate_dungeon`, no profiler attached, second run);
"before" is the v0.11.0 commit measured the same night on the same
machine. Object counts are everything in `bpy.data.objects` after the
three generates (props are Empties instancing shared collections).

| Preset | v0.11.0 total | v0.12.0 total | city only | objects before -> after | verts after |
|---|---|---|---|---|---|
| Metropole | 5.14 s | **4.98 s** | 4.26 -> 4.22 s | 7,369 -> **6,655** | 1.09 M |
| Kleinstadt | 1.68 s | **1.65 s** | 1.29 -> 1.29 s | 5,703 -> **5,063** | 331 k |
| Dorf | 0.78 s | 0.80 s | 0.48 -> 0.55 s | 2,524 -> **2,249** | 288 k |
| Industrial | 1.14 s | 0.94 s | 0.78 -> 0.65 s | 2,348 -> **2,148** | 345 k |

(The v0.11.0 Metropole row was measured while a render ran at idle
priority on the same machine, so treat +-10% as noise.)

What changed and what it bought:

- **One street-network object** instead of one per edge and per
  intersection: -714 objects (Metropole), -640 (Kleinstadt). Road width
  and lane count became per-face float attributes so the asphalt shader
  (Attribute node, GEOMETRY mode) needs no per-object properties. Mesh
  building itself was never the cost (~0.2 s), the win is in the
  outliner, selection, depsgraph and export object counts.
- **Terrain under roads** (`lower_terrain_under_corridors`): one
  numpy pass over the corridor windows, ~0.05 s for a Metropole; the
  6 m road chunks add ~10k road vertices, invisible in the timings.
- **Progress cursor**: `generate_city(progress=...)` reports between
  stages; the operator feeds it to `wm.progress_update` (a no-op
  headless).
- **Materials** were checked for duplication: every material in the
  scene already comes from a `get_or_create_*` singleton (facade,
  window, roof, road, sidewalk, terrain, prop, signage, special-building
  signs), the fingerprint shows no `.001` copies after a regenerate, so
  there was nothing to dedupe.

**What is still left, and why the Metropole is not under 4 s:** the
`generate_city` time is ~80% `_build_single_building` (bmesh
extrude/translate/sort per floor, one `inset_individual` per floor and
thickness for the windows) - ~3.4 s of 4.2 s. Streets, props, signage
and the terrain passes together are under 0.7 s. A per-block building
builder that assembles all floors of a block in one numpy-generated
mesh (and reads facade/tint/roof from per-face attributes the way the
roads now do) is the next step; memory per Metropole scene is dominated
by the 1 M-vertex terrain (1024 x 1024 grid) rather than the city.


## v0.10.0 generation profile (headless Blender 5.2, idle priority, 8 threads)

Measured with `cProfile` around `generate_terrain` + `generate_city` +
`generate_dungeon` (seed 42, terrain on) and an order-insensitive
fingerprint of every generated vertex/face/object/property, so each
change could be classified as "same output, faster" or "different output
by design". Wall-clock totals, second run of two (no profiler attached):

| Preset | v0.9.0 | pure optimizations | + window-column fix | Night-mode toggle |
|---|---|---|---|---|
| Metropole | 26.9 s | 26.3 s | **7.7 s** | 1.30 s -> 0.41 s |
| Kleinstadt | 2.50 s | 1.65 s | **1.57 s** | 0.42 s -> 0.13 s |
| Dorf | 1.23 s | 0.78 s | **0.96 s** * | 0.09 s -> 0.04 s |
| Industrial | 1.12 s | 0.79 s | **0.96 s** * | 0.11 s -> 0.04 s |

\* Dorf/Industrial grew slightly in the last column because that commit
also changed *what* is generated (sidewalks, connected raster streets with
intersections, the forest ring), not because the fix is slower.

**Where the time went (Metropole, v0.9.0, 32 s under cProfile):**

- 24.1 s - `_add_window_row`: one `bmesh.ops.inset_individual` per window
  bay, 114k calls. Every `bmesh.ops` call pays a setup cost proportional
  to the mesh's current element count, so towers were quadratic-ish.
  Profiling also exposed that the column subdivision never worked (see
  `generators/city/buildings.py:_split_face_into_columns`): the "columns"
  were the base face, the roof cap and the unsplit side face. Fixed with
  `bmesh.utils.edge_split`/`face_split` (no operator overhead) plus one
  inset call per floor and thickness.
- 3.6 s - `set(bm.faces) - faces_before` per floor to find the extruded
  faces; replaced by the new verts' `link_faces`.
- 2.1 s - `_update_lamp_lights` (night mode): `bpy.data.objects.get()`
  and the Python-implemented `obj.users_collection` per street lamp;
  replaced by one name->object dict and one name->collection dict.
- 1.3-2.3 s - `noise.fbm` called with scalars: 0-d numpy arrays through
  four octaves of ufunc hash chains cost 150-300 us *per point*, and every
  street vertex, prop, sign and building samples terrain height one point
  at a time. A plain-Python scalar path (`_fbm_scalar`) is bit-identical
  to the array path (pinned by a test over 1200 points) and ~15x faster.
- 0.5-1.0 s - `build_terrain_mesh`: 262k Python tuples through
  `from_pydata`; now numpy buffers + `foreach_set` (0.25 s).
- 0.2 s - `flatten_terrain_for_footprints`: per-vertex `v.co` RNA access;
  now one `foreach_get`/`foreach_set` round trip with numpy windows.

**What is left (Metropole, 7.7 s):** ~2.8 s in `_build_single_building`
(the per-floor extrude/translate/sort), ~1.1 s batched window insets,
~0.7 s night-mode lamp lights, ~0.6 s `SpatialHashGrid.has_collision`,
~0.3 s `from_pydata` for ~1.7k small meshes. A single-mesh-per-block
builder or a numpy facade generator would be the next step; none of the
remaining items is a hot spot on its own.

## Instance Collections for prop memory efficiency

`procgen_maps/assets/factory.py` never creates a new mesh datablock per
prop placement. Instead, `_get_or_build_instance_source(asset_id, detail)`
lazily builds each `(asset_id, LOD detail)` combination's geometry exactly
once, into a Collection that is deliberately **never linked into the
scene** - it exists only to be referenced by Blender's Instance Collection
mechanism:

```python
source_collection = bpy.data.collections.new(f"ProcgenMaps_AssetSrc_{asset_id}_{detail}")
source_collection.objects.link(master_obj)
```

`spawn()` then creates one lightweight Empty per placement and points its
`instance_collection` at that shared source:

```python
empty.instance_type = 'COLLECTION'
empty.instance_collection = source_collection
```

Result: placing thousands of trees/lamps/benches/cars costs thousands of
Empty objects (cheap - no mesh data) plus at most
`len(ASSET_DEFS) * len(LOD_LEVELS)` real mesh datablocks total, built once
and cached in `_source_collection_cache`, no matter how many instances
reference them.

## SpatialHashGrid for placement collision avoidance

`procgen_maps/utils/spatial.py`'s `SpatialHashGrid` buckets placed items
into `cell_size`-sized cells (`DEFAULT_SPATIAL_CELL_SIZE` in
`config/settings.py`) keyed by `floor(x/cell_size), floor(y/cell_size)`.
`generators/city/props.py`'s `plan_props` calls `has_collision(x, y,
radius)` before accepting a candidate street lamp/tree/car/bench/sign
position, and `insert()` after accepting it, so a footprint check only
has to scan the handful of items in nearby cells rather than every
already-placed prop - no scipy KD-tree needed. For exact nearest-neighbor
queries elsewhere, `utils/spatial.py` also offers `build_kdtree`, a thin
wrapper over Blender's own `mathutils.kdtree.KDTree` - still no scipy
either way.

## Distance-based LOD

`assets/factory.py`'s `update_lod(empties, camera_location, high_distance,
medium_distance)` re-points each already-spawned Empty's
`instance_collection` between the `"high"`/`"medium"`/`"low"` detail
source collections based on straight-line distance from the camera:

```python
distance = (empty.location - cam_co).length
detail = "high" if distance <= high_distance else "medium" if distance <= medium_distance else "low"
empty.instance_collection = _get_or_build_instance_source(asset_id, detail)
```

Because every detail level's geometry was already built (and cached) the
first time any prop needed it, switching an Empty between levels is just
reassigning a Collection reference - it never rebuilds or re-meshes
anything. `update_lod` also skips the reassignment entirely when an
Empty's `procgen_maps_lod_detail` custom property already matches the
target detail, so a steady-state camera costs nothing per call. Tune the
distance breakpoints via `config/settings.py`'s `LOD_DISTANCE_HIGH` /
`LOD_DISTANCE_MEDIUM`.

## Not-yet-built follow-ups

These are documented, intentional gaps - not oversights - called out
directly in the relevant modules' docstrings:

- **True pixel texture-atlas baking.** `materials/atlas.py`'s
  `pack_rects()` is a complete, unit-tested deterministic shelf-packing
  algorithm - the exact piece a real pixel atlas needs to lay out tiles on
  a sheet. It is not yet wired into the material pipeline: `city_mat.py`
  and `terrain_mat.py` currently use a shared parametrized node-group
  material instead (an Attribute node keyed off
  `procgen_maps_material_index` driving a ColorRamp, for city facades; a
  height-driven ColorRamp for terrain), which already gets most of the
  "few materials, good batching" win without needing baked images.
  `atlas.py`'s `bake_atlas_image()` is a stub that raises
  `NotImplementedError` and documents what the real implementation would
  do (blit each source image's pixels into one shared `bpy.data.images`
  buffer at its packed rect, remap UVs to match) - a real follow-up for
  export targets that specifically benefit from one packed texture rather
  than a procedural node shader.
- **Geometry-Nodes-driven LOD switching.** The current `update_lod()` is a
  Python-side function that must be called explicitly (e.g. once per
  frame-change handler, or once per render) and does a plain per-Empty
  distance check. A Geometry Nodes setup (e.g. a Switch node keyed off
  distance-to-camera, or Blender's built-in LOD-adjacent nodes) would move
  this decision into the depsgraph itself, updating automatically without
  any Python callback ticking at all - lower overhead, and no dependency
  on a driver/handler staying registered.

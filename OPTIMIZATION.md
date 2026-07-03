# Optimization

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

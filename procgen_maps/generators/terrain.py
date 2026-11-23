"""Terrain heightmap generation: FBM noise -> mesh.

Split per the project's PP/BPY convention (see ARCHITECTURE.md):
`generate_heightmap` / `sample_world_height` are pure Python+numpy and safe to
unit test outside Blender; `build_terrain_mesh` is the thin bpy/bmesh build
step. `sample_world_height` uses the exact same noise field as
`generate_heightmap`, so streets and buildings can query terrain height
without ray-casting against the generated mesh.
"""
from dataclasses import dataclass

from ..utils import noise as _noise

TERRAIN_OBJECT_NAME = "ProcgenMaps_Terrain"


@dataclass(frozen=True)
class TerrainParams:
    resolution: int = 256
    world_size: float = 1000.0
    scale: float = 120.0
    octaves: int = 5
    persistence: float = 0.5
    lacunarity: float = 2.0
    max_height: float = 40.0
    seed: int = 0


def generate_heightmap(params: TerrainParams):
    """Return a (resolution, resolution) numpy array of world-space heights
    in meters, covering a world_size x world_size square centered on the origin."""
    height01 = _noise.fbm_heightmap(
        params.resolution,
        params.world_size,
        octaves=params.octaves,
        persistence=params.persistence,
        lacunarity=params.lacunarity,
        scale=params.scale,
        seed=params.seed,
    )
    return height01 * params.max_height


def sample_world_height(x, y, params: TerrainParams):
    """Sample terrain height in meters at an arbitrary world-space (x, y),
    using the identical noise field as `generate_heightmap`."""
    height01 = _noise.fbm(
        x, y,
        scale=params.scale,
        octaves=params.octaves,
        persistence=params.persistence,
        lacunarity=params.lacunarity,
        seed=params.seed,
    )
    return height01 * params.max_height


def build_terrain_mesh(params: TerrainParams, collection):
    """Build (or replace) the terrain mesh object inside `collection`.

    Uses mesh.from_pydata for a single fast grid build rather than looping
    bmesh.ops per vertex.
    """
    import bpy
    import numpy as np

    heights = generate_heightmap(params)
    res = params.resolution
    half = params.world_size / 2.0
    step = params.world_size / (res - 1)
    coords = -half + np.arange(res, dtype=np.float64) * step

    # Vertex order is row-major (row = y, col = x) and each quad is
    # (i00, i10, i11, i01) - the same layout the old per-vertex Python
    # loops produced, just built as flat numpy buffers and pushed through
    # foreach_set (a 512x512 grid is 262k verts / 261k quads; the tuple
    # lists took ~0.6-1.0 s, this takes ~0.1 s).
    verts = np.empty((res * res, 3), dtype=np.float64)
    verts[:, 0] = np.tile(coords, res)
    verts[:, 1] = np.repeat(coords, res)
    verts[:, 2] = heights.ravel()

    rows, cols = np.mgrid[0:res - 1, 0:res - 1]
    i00 = (rows * res + cols).ravel()
    faces = np.stack((i00, i00 + 1, i00 + res + 1, i00 + res), axis=1)
    face_count = faces.shape[0]

    existing = bpy.data.objects.get(TERRAIN_OBJECT_NAME)
    if existing is not None:
        old_mesh = existing.data
        bpy.data.objects.remove(existing, do_unlink=True)
        if old_mesh is not None and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)

    mesh = bpy.data.meshes.new(TERRAIN_OBJECT_NAME)
    mesh.vertices.add(res * res)
    mesh.vertices.foreach_set("co", verts.ravel())
    mesh.loops.add(face_count * 4)
    mesh.loops.foreach_set("vertex_index", faces.ravel())
    mesh.polygons.add(face_count)
    mesh.polygons.foreach_set("loop_start", np.arange(face_count, dtype=np.int32) * 4)
    mesh.update(calc_edges=True)
    mesh.polygons.foreach_set("use_smooth", np.ones(face_count, dtype=bool))

    obj = bpy.data.objects.new(TERRAIN_OBJECT_NAME, mesh)
    obj["procgen_maps_seed"] = params.seed
    obj["procgen_maps_world_size"] = params.world_size
    obj["procgen_maps_max_height"] = params.max_height
    collection.objects.link(obj)
    return obj


def reset_terrain_heights(terrain_obj, params: TerrainParams) -> bool:
    """Put every terrain vertex back on the raw heightmap, undoing any
    earlier flatten_terrain_for_footprints. Regenerating only the city
    (new seed, same terrain) used to leave the previous city's flattened
    building pads in the ground. Returns False (and leaves the mesh alone)
    if the mesh does not match `params`' grid."""
    import numpy as np

    mesh = terrain_obj.data
    res = params.resolution
    if len(mesh.vertices) != res * res:
        return False
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape(res, res, 3)
    coords[..., 2] = generate_heightmap(params).astype(np.float32)
    mesh.vertices.foreach_set("co", coords.ravel())
    mesh.update()
    return True


def flatten_terrain_for_footprints(terrain_obj, params: TerrainParams, footprints_with_target_z, margin=3.0):
    """Locally flatten the terrain mesh under each (footprint, target_z)
    pair so a building's flat foundation sits flush on the ground instead
    of clipping into or floating above sloped terrain.

    `target_z` must be the exact same value the caller used as the
    building's own base_z (generators/city/buildings.py samples
    `sample_world_height` at the footprint centroid) - this function only
    reshapes the *visible mesh* to agree with that already-chosen height,
    it does not change where anything is placed.

    Blends linearly to the untouched terrain height over `margin` meters
    outside the footprint's own bounding box, so there's a smooth ramp
    rather than a cliff at the edge. Only touches the small vertex range
    each footprint's expanded bounding box maps to on the terrain's
    regular grid (not a full mesh scan), so this stays cheap even for
    many buildings on a high-resolution terrain."""
    import numpy as np

    mesh = terrain_obj.data
    res = params.resolution
    half = params.world_size / 2.0
    step = params.world_size / (res - 1)

    # One foreach_get/foreach_set round trip instead of a per-vertex RNA
    # `v.co` read + write (which is what made this ~0.2 s for a Metropole).
    # The buffer stays float32 - the mesh's own precision - so a later
    # footprint overlapping an earlier one reads exactly the value the
    # old per-vertex code would have read back from the mesh.
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape(res, res, 3)

    def coord_to_index(value):
        return (value + half) / step

    for footprint, target_z in footprints_with_target_z:
        core_min_x = min(p[0] for p in footprint)
        core_max_x = max(p[0] for p in footprint)
        core_min_y = min(p[1] for p in footprint)
        core_max_y = max(p[1] for p in footprint)

        col_lo = max(0, int(coord_to_index(core_min_x - margin)) - 1)
        col_hi = min(res - 1, int(coord_to_index(core_max_x + margin)) + 1)
        row_lo = max(0, int(coord_to_index(core_min_y - margin)) - 1)
        row_hi = min(res - 1, int(coord_to_index(core_max_y + margin)) + 1)
        if col_hi < col_lo or row_hi < row_lo:
            continue

        window = coords[row_lo:row_hi + 1, col_lo:col_hi + 1]
        x = window[..., 0].astype(np.float64)
        y = window[..., 1].astype(np.float64)
        z = window[..., 2].astype(np.float64)
        dx = np.maximum(np.maximum(core_min_x - x, 0.0), x - core_max_x)
        dy = np.maximum(np.maximum(core_min_y - y, 0.0), y - core_max_y)
        dist = np.hypot(dx, dy)
        inside = dist < margin
        factor = np.ones_like(dist) if margin <= 0.0 else 1.0 - (dist / margin)
        blended = z * (1.0 - factor) + target_z * factor
        window[..., 2] = np.where(inside, blended, z).astype(np.float32)

    mesh.vertices.foreach_set("co", coords.ravel())
    mesh.update()

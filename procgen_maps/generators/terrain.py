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

    heights = generate_heightmap(params)
    res = params.resolution
    half = params.world_size / 2.0
    step = params.world_size / (res - 1)
    coords = [-half + i * step for i in range(res)]

    verts = []
    for row in range(res):
        for col in range(res):
            verts.append((coords[col], coords[row], float(heights[row][col])))

    faces = []
    for row in range(res - 1):
        for col in range(res - 1):
            i00 = row * res + col
            i10 = row * res + (col + 1)
            i01 = (row + 1) * res + col
            i11 = (row + 1) * res + (col + 1)
            faces.append((i00, i10, i11, i01))

    existing = bpy.data.objects.get(TERRAIN_OBJECT_NAME)
    if existing is not None:
        old_mesh = existing.data
        bpy.data.objects.remove(existing, do_unlink=True)
        if old_mesh is not None and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)

    mesh = bpy.data.meshes.new(TERRAIN_OBJECT_NAME)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))

    obj = bpy.data.objects.new(TERRAIN_OBJECT_NAME, mesh)
    obj["procgen_maps_seed"] = params.seed
    obj["procgen_maps_world_size"] = params.world_size
    obj["procgen_maps_max_height"] = params.max_height
    collection.objects.link(obj)
    return obj


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
    import math

    mesh = terrain_obj.data
    verts = mesh.vertices
    res = params.resolution
    half = params.world_size / 2.0
    step = params.world_size / (res - 1)

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

        for row in range(row_lo, row_hi + 1):
            base_index = row * res
            for col in range(col_lo, col_hi + 1):
                v = verts[base_index + col]
                x, y, z = v.co
                dx = max(core_min_x - x, 0.0, x - core_max_x)
                dy = max(core_min_y - y, 0.0, y - core_max_y)
                dist = math.hypot(dx, dy)
                if dist >= margin:
                    continue
                factor = 1.0 if margin <= 0.0 else 1.0 - (dist / margin)
                v.co.z = z * (1.0 - factor) + target_z * factor

    mesh.update()

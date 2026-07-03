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

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


ROAD_BED_DEPTH = 0.7        # meters the terrain mesh is sunk below the heightfield under the asphalt (3 m road chunks can sit ~0.5 m under the field at a noise peak)
ROAD_BED_EDGE_DEPTH = 0.2   # ... and at the sidewalk's outer edge (the sidewalk hides the ramp between)
ROAD_BED_MARGIN = 1.5       # meters of blend from the sidewalk edge back up to the untouched terrain
ROAD_BED_SHOULDER = 2.0     # meters inside the corridor edge over which the bed rises (the sidewalk width)


def corridor_profile(dist, half_width, depth, shallow_depth, shoulder, margin):
    """(bed depth, blend weight) for terrain vertices `dist` meters from a
    corridor's centerline (pure numpy): the bed is the full `depth` under
    the asphalt, rising linearly to `shallow_depth` across the `shoulder`
    (the sidewalk) so the ground just outside the curb is not a
    half-meter trench, and stays `shallow_depth` outside; the weight is 1
    inside the corridor and fades to 0 over `margin` outside it."""
    import numpy as np

    inner = half_width - shoulder
    if shoulder > 0.0:
        bed = depth + (shallow_depth - depth) * np.clip((dist - inner) / shoulder, 0.0, 1.0)
    else:
        bed = np.full_like(dist, depth, dtype=np.float64)
    outside = np.maximum(dist - half_width, 0.0)
    if margin > 0.0:
        fade = np.clip(1.0 - outside / margin, 0.0, 1.0)
    else:
        fade = (outside <= 0.0).astype(np.float64)
    return bed, fade


def corridor_lowering(dist, half_width, depth, shallow_depth, shoulder, margin):
    """How many meters a flat terrain sinks at `dist` from the centerline
    (corridor_profile's bed times its weight). Unit-tested."""
    bed, fade = corridor_profile(dist, half_width, depth, shallow_depth, shoulder, margin)
    return bed * fade


def lower_terrain_under_corridors(terrain_obj, params: TerrainParams, corridors, discs=(),
                                  depth=ROAD_BED_DEPTH, margin=ROAD_BED_MARGIN,
                                  shallow_depth=ROAD_BED_EDGE_DEPTH, shoulder=ROAD_BED_SHOULDER):
    """Sink the terrain mesh under every street footprint so the ground
    never pokes through the asphalt or the sidewalks.

    Streets sample the *continuous* heightfield at their own (sparse)
    vertices, while the terrain mesh interpolates linearly between grid
    vertices ~2 m apart - between a road's samples the two surfaces
    disagree by up to a few decimeters wherever the ground curves, which
    showed as green blotches through every crossing and along the roads.
    Lowering the mesh by `depth` under the corridor puts the ground
    reliably below the road surface everywhere. The bed is `depth` deep
    under the asphalt and rises to `shallow_depth` at the corridor's
    outer edge across the `shoulder` (the sidewalk - see
    corridor_lowering), then blends back to the heightfield over `margin`
    meters: v0.12 sank the whole corridor 0.5 m and left a visible trench
    along the outside of every sidewalk. A skirt on the sidewalk's outer
    edge (streets.py's SIDEWALK_SKIRT) hides what remains of the step.

    `corridors` = (ax, ay, bx, by, half_width) per road edge (asphalt plus
    sidewalks), `discs` = (cx, cy, radius) per node. Lowering is taken as
    the maximum over all shapes (never summed), so overlapping corridors
    at a crossing sink exactly once, and a vertex already below the bed
    (nothing raises terrain except building pads) is left alone. Same
    foreach_get/foreach_set round trip as flatten_terrain_for_footprints.
    Returns the number of vertices fully inside a corridor."""
    import numpy as np

    mesh = terrain_obj.data
    res = params.resolution
    half = params.world_size / 2.0
    step = params.world_size / (res - 1)
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape(res, res, 3)
    bed_depth = np.zeros((res, res), dtype=np.float64)   # bed depth below the heightfield, per vertex
    weight = np.zeros((res, res), dtype=np.float64)      # 1 inside a corridor, fading out over the margin
    inside = np.zeros((res, res), dtype=bool)

    def index_range(lo, hi):
        lo_index = max(0, int((lo + half) / step) - 1)
        hi_index = min(res - 1, int((hi + half) / step) + 1)
        return lo_index, hi_index

    def apply(row_lo, row_hi, col_lo, col_hi, dist, half_width, shoulder_width):
        bed, fade = corridor_profile(dist, half_width, depth, shallow_depth, min(shoulder_width, half_width), margin)
        window = bed_depth[row_lo:row_hi + 1, col_lo:col_hi + 1]
        np.maximum(window, np.where(fade > 0.0, bed, 0.0), out=window)
        window = weight[row_lo:row_hi + 1, col_lo:col_hi + 1]
        np.maximum(window, fade, out=window)
        inside[row_lo:row_hi + 1, col_lo:col_hi + 1] |= dist <= half_width

    for (ax, ay, bx, by, half_width) in corridors:
        reach = half_width + margin
        col_lo, col_hi = index_range(min(ax, bx) - reach, max(ax, bx) + reach)
        row_lo, row_hi = index_range(min(ay, by) - reach, max(ay, by) + reach)
        if col_hi < col_lo or row_hi < row_lo:
            continue
        window = coords[row_lo:row_hi + 1, col_lo:col_hi + 1]
        x = window[..., 0].astype(np.float64)
        y = window[..., 1].astype(np.float64)
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            t = np.zeros_like(x)
        else:
            t = np.clip(((x - ax) * dx + (y - ay) * dy) / length_sq, 0.0, 1.0)
        dist = np.hypot(x - (ax + t * dx), y - (ay + t * dy))
        apply(row_lo, row_hi, col_lo, col_hi, dist, half_width, shoulder)

    for (cx, cy, radius) in discs:
        reach = radius + margin
        col_lo, col_hi = index_range(cx - reach, cx + reach)
        row_lo, row_hi = index_range(cy - reach, cy + reach)
        if col_hi < col_lo or row_hi < row_lo:
            continue
        window = coords[row_lo:row_hi + 1, col_lo:col_hi + 1]
        dist = np.hypot(window[..., 0].astype(np.float64) - cx, window[..., 1].astype(np.float64) - cy)
        # No shoulder under a node disc: its rim is still under the asphalt
        # fan / sidewalk corners, which are planar over a wider span than a
        # road chunk and need the full bed depth right to the edge.
        apply(row_lo, row_hi, col_lo, col_hi, dist, radius, 0.0)

    # The bed is `bed_depth` under the raw heightfield; vertices a building
    # pad has raised above it (flatten_terrain_for_footprints runs first,
    # and its blend reaches under the sidewalk) are pulled down too, so
    # the bed wins everywhere inside a corridor. Outside, the pull fades
    # with the weight so the margin stays continuous with a pad.
    bed = generate_heightmap(params) - bed_depth
    z = coords[..., 2].astype(np.float64)
    coords[..., 2] = (z - weight * np.maximum(z - bed, 0.0)).astype(np.float32)
    mesh.vertices.foreach_set("co", coords.ravel())
    mesh.update()
    return int(np.count_nonzero(inside))

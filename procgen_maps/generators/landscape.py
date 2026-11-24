"""Countryside landscape masks: field patchwork, hedgerows and forest clumps.

Everything here is pure numpy / pure Python (no bpy), shared by three
consumers so they agree with each other exactly:

- `terrain.write_landscape_attributes` samples `landscape_masks` on the
  terrain grid and stores the result as per-vertex float attributes
  (`procgen_maps_field`, `procgen_maps_field_tone`, `procgen_maps_hedge`,
  `procgen_maps_forest`) that the terrain shader reads - so the dark
  forest floor lies exactly under the tree clumps and the hedge lines
  exactly under the hedgerow trees.
- `generators/city/props.py` keeps a forest-ring candidate with the
  probability `forest_density` gives (the clumps) and plants hedgerow
  trees on `hedgerow_points`.
- the tests, which check determinism and the band limits.

Fields are a jittered-grid Voronoi: every `FIELD_CELL` x `FIELD_CELL`
cell of the plane owns one seed point; a ground point belongs to the
nearest seed. Each cell has a random tone (the shader maps it to a meadow
/ crop / stubble / ploughed colour) and the border between two cells
carries a hedgerow with probability `HEDGE_CHANCE`. The forest is the
v0.10 thinning ring, multiplied by a clump mask from a low-frequency fbm
so the trees stand in woods with meadows between them instead of an even
sprinkle.
"""
import math

import numpy as np

from ..utils import noise as _noise

FIELD_CELL = 85.0          # meters, mean field size
FIELD_JITTER = 0.42        # seed jitter as a fraction of the cell (keeps every seed inside its own cell)
HEDGE_WIDTH = 5.0          # meters, full width of a hedgerow's dark ground band
HEDGE_CHANCE = 0.62        # share of field borders that carry a hedgerow
FIELD_START = 1.0          # x city radius: fields start right at the city's edge ...
FIELD_FULL = 1.14          # ... and are fully there from here on
FOREST_START = 1.05        # x city radius: inner edge of the forest ring
FOREST_FULL = 1.18
FOREST_THIN_FROM = 1.55    # the ring thins out from here ...
FOREST_END = 1.95          # ... to nothing here
FOREST_CLUMP_SCALE = 0.38  # x city radius: feature size of the clump noise
FOREST_CLUMP_LOW = 0.44    # fbm value where a clump starts ...
FOREST_CLUMP_HIGH = 0.56   # ... and is fully dense
HEDGE_TREE_STEP = 3.0      # meters between hedgerow tree candidates along a border
HEDGE_TREE_KEEP = 0.45     # share of candidates that become trees

_SEED_JITTER = 11
_SEED_TONE = 23
_SEED_HEDGE = 37
_SEED_FOREST = 77


def _smoothstep(x, lo, hi):
    t = np.clip((np.asarray(x, dtype=np.float64) - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _cell_seed_xy(ix, iy, seed):
    """World position of the seed point owned by field cell (ix, iy)."""
    jx = _noise._hash_grid(ix, iy, seed + _SEED_JITTER)
    jy = _noise._hash_grid(ix, iy, seed + _SEED_JITTER + 1)
    sx = (ix + 0.5 + (jx - 0.5) * 2.0 * FIELD_JITTER) * FIELD_CELL
    sy = (iy + 0.5 + (jy - 0.5) * 2.0 * FIELD_JITTER) * FIELD_CELL
    return sx, sy


def field_cells(xs, ys, seed):
    """For every (x, y): (ix1, iy1, d1, ix2, iy2, d2) - the nearest and
    second-nearest field seeds and the distances to them. Vectorized over
    the 3x3 neighbourhood of the point's own cell, which is enough because
    the jitter keeps every seed inside its cell."""
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    cx = np.floor(xs / FIELD_CELL).astype(np.int64)
    cy = np.floor(ys / FIELD_CELL).astype(np.int64)
    d1 = np.full(xs.shape, np.inf)
    d2 = np.full(xs.shape, np.inf)
    ix1 = np.zeros(xs.shape, dtype=np.int64)
    iy1 = np.zeros(xs.shape, dtype=np.int64)
    ix2 = np.zeros(xs.shape, dtype=np.int64)
    iy2 = np.zeros(xs.shape, dtype=np.int64)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            ix, iy = cx + dx, cy + dy
            sx, sy = _cell_seed_xy(ix, iy, seed)
            d = np.hypot(xs - sx, ys - sy)
            closer = d < d1
            # the old nearest becomes the second nearest where a closer one appears
            d2 = np.where(closer, d1, np.minimum(d2, d))
            ix2 = np.where(closer, ix1, np.where(d < d2, ix, ix2))
            iy2 = np.where(closer, iy1, np.where(d < d2, iy, iy2))
            d1 = np.where(closer, d, d1)
            ix1 = np.where(closer, ix, ix1)
            iy1 = np.where(closer, iy, iy1)
    return ix1, iy1, d1, ix2, iy2, d2


def _hedge_present(ix1, iy1, ix2, iy2, seed):
    """Whether the border between two cells carries a hedge: one hash of
    the (order-independent) cell pair."""
    lo_x = np.minimum(ix1, ix2)
    hi_x = np.maximum(ix1, ix2)
    first = (ix1 < ix2) | ((ix1 == ix2) & (iy1 <= iy2))
    lo_y = np.where(first, iy1, iy2)
    hi_y = np.where(first, iy2, iy1)
    key_x = lo_x * 7919 + lo_y
    key_y = hi_x * 7919 + hi_y
    return _noise._hash_grid(key_x, key_y, seed + _SEED_HEDGE) < HEDGE_CHANCE


def forest_density(x, y, city_radius, seed):
    """0..1 forest density at a world point (scalar or numpy): the thinning
    ring outside the city times the clump mask. What plan_forest_ring
    uses as the keep probability and what the terrain shader reads as
    the forest-floor weight."""
    r = np.hypot(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)) / max(city_radius, 1.0)
    ring = _smoothstep(r, FOREST_START, FOREST_FULL) * (1.0 - _smoothstep(r, FOREST_THIN_FROM, FOREST_END))
    clump_value = _noise.fbm(x, y, scale=city_radius * FOREST_CLUMP_SCALE, octaves=3, persistence=0.5,
                             lacunarity=2.0, seed=seed + _SEED_FOREST)
    # fbm over three octaves sits in ~0.3..0.7, so this threshold band makes
    # roughly half of the ring woodland and leaves fields between the woods.
    clump = _smoothstep(clump_value, FOREST_CLUMP_LOW, FOREST_CLUMP_HIGH)
    result = ring * clump
    return float(result) if np.ndim(result) == 0 else result


def landscape_masks(xs, ys, city_radius, seed):
    """Per point: (field, field_tone, hedge, forest), each 0..1 (numpy).
    `field` fades in from the city edge, `field_tone` is the owning
    cell's random tone, `hedge` is 1 on a hedgerow border and 0 away from
    it, `forest` is forest_density."""
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    r = np.hypot(xs, ys) / max(city_radius, 1.0)
    field = _smoothstep(r, FIELD_START, FIELD_FULL)
    ix1, iy1, d1, ix2, iy2, d2 = field_cells(xs, ys, seed)
    tone = _noise._hash_grid(ix1, iy1, seed + _SEED_TONE)
    edge_distance = (d2 - d1) * 0.5
    hedge = (1.0 - _smoothstep(edge_distance, HEDGE_WIDTH * 0.35, HEDGE_WIDTH * 0.5)) \
        * _hedge_present(ix1, iy1, ix2, iy2, seed) * field
    forest = forest_density(xs, ys, city_radius, seed)
    return field, tone, hedge, forest


def hedgerow_points(city_radius, world_half, seed, rng, step=HEDGE_TREE_STEP, keep=HEDGE_TREE_KEEP):
    """(x, y, scale, rotation_z) tree candidates along the hedgerow
    borders between `FIELD_FULL` x the city radius and the map edge
    (minus a margin), thinned to `keep` of the border points so the line
    reads as a hedge with trees in it, not a wall. `rng` is the caller's
    random.Random (consumed in a fixed order, so the result is
    deterministic per seed)."""
    limit = world_half - 12.0
    if limit <= city_radius * FIELD_FULL:
        return []
    coords = np.arange(-limit, limit + step * 0.5, step)
    xs, ys = np.meshgrid(coords, coords)
    xs = xs.ravel()
    ys = ys.ravel()
    r = np.hypot(xs, ys)
    inside = (r >= city_radius * FIELD_FULL) & (np.abs(xs) <= limit) & (np.abs(ys) <= limit)
    xs, ys = xs[inside], ys[inside]
    if xs.size == 0:
        return []
    ix1, iy1, d1, ix2, iy2, d2 = field_cells(xs, ys, seed)
    on_border = ((d2 - d1) * 0.5 < HEDGE_WIDTH * 0.45) & _hedge_present(ix1, iy1, ix2, iy2, seed)
    # Hedgerows stop where the woods begin: inside a clump the forest trees take over.
    in_forest = forest_density(xs, ys, city_radius, seed) > 0.5
    chosen = np.flatnonzero(on_border & ~in_forest)
    points = []
    for index in chosen.tolist():
        if rng.random() > keep:
            continue
        points.append((float(xs[index]) + rng.uniform(-0.6, 0.6), float(ys[index]) + rng.uniform(-0.6, 0.6),
                       rng.uniform(0.8, 1.25), rng.uniform(0.0, math.tau)))
    return points

"""City block + street layout generation.

Two modes selected by `CityPreset.layout_mode`:

- 'grid': a regular lattice of rectangular blocks with light jitter
  (Kleinstadt, Dorf presets - regular, orderly towns).
- 'raster': seed points scattered and partitioned via a numpy-vectorized
  nearest-seed raster scan (Voronoi-like; no scipy dependency - see
  ARCHITECTURE.md). Each block's footprint is approximated as an
  axis-aligned rectangle around its assigned cells' bounding extent rather
  than an exact cell polygon; this keeps buildings.py's per-block extrusion
  simple while still producing organically varied block sizes.

Pure Python/numpy only - no bpy here. generators/city/streets.py and
buildings.py turn this data into meshes.
"""
import math
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

_FILL_FACTOR = 0.78       # fraction of available extent actually built on; remainder is street gap
_ARTERIAL_EVERY_N = 3     # every Nth grid line / every Nth block is an arterial street


@dataclass
class Block:
    id: int
    center: Tuple[float, float]
    half_size: Tuple[float, float]  # (half_width, half_height), world units

    @property
    def polygon(self) -> List[Tuple[float, float]]:
        cx, cy = self.center
        hw, hh = self.half_size
        return [
            (cx - hw, cy - hh),
            (cx + hw, cy - hh),
            (cx + hw, cy + hh),
            (cx - hw, cy + hh),
        ]

    @property
    def area(self) -> float:
        return (2 * self.half_size[0]) * (2 * self.half_size[1])


@dataclass
class StreetSegment:
    id: int
    start: Tuple[float, float]
    end: Tuple[float, float]
    street_class: str  # 'arterial' | 'local'


@dataclass
class CityLayout:
    blocks: List[Block] = field(default_factory=list)
    streets: List[StreetSegment] = field(default_factory=list)
    radius: float = 0.0


def generate_layout(preset, seed=None) -> CityLayout:
    """Dispatch to grid or raster layout based on `preset.layout_mode`."""
    rng_seed = preset.seed if seed is None else seed
    if preset.layout_mode == "grid":
        return _generate_grid_layout(preset, rng_seed)
    return _generate_raster_layout(preset, rng_seed)


def _generate_grid_layout(preset, seed) -> CityLayout:
    rng = np.random.default_rng(seed)
    n = max(2, int((preset.radius * 2) / preset.block_size))
    half_extent = (n * preset.block_size) / 2.0
    jitter = preset.block_size * 0.08

    centers_axis = [(-half_extent + preset.block_size * (i + 0.5)) for i in range(n)]

    blocks: List[Block] = []
    block_id = 0
    for cy in centers_axis:
        for cx in centers_axis:
            jx = cx + rng.uniform(-jitter, jitter)
            jy = cy + rng.uniform(-jitter, jitter)
            half = (preset.block_size * _FILL_FACTOR) / 2.0
            blocks.append(Block(id=block_id, center=(jx, jy), half_size=(half, half)))
            block_id += 1

    streets = _grid_street_segments(n, half_extent, preset)
    return CityLayout(blocks=blocks, streets=streets, radius=preset.radius)


def _grid_street_segments(n, half_extent, preset) -> List[StreetSegment]:
    """Build the full grid of streets, split at every crossing point so each
    interior intersection becomes a real shared graph node (degree 3/4) -
    streets.build_street_meshes uses node degree to place intersection fans."""
    streets: List[StreetSegment] = []
    seg_id = 0
    line_positions = [-half_extent + preset.block_size * k for k in range(n + 1)]

    for k, x in enumerate(line_positions):
        street_class = "arterial" if k % _ARTERIAL_EVERY_N == 0 else "local"
        for j in range(n):
            streets.append(StreetSegment(seg_id, (x, line_positions[j]), (x, line_positions[j + 1]), street_class))
            seg_id += 1

    for k, y in enumerate(line_positions):
        street_class = "arterial" if k % _ARTERIAL_EVERY_N == 0 else "local"
        for i in range(n):
            streets.append(StreetSegment(seg_id, (line_positions[i], y), (line_positions[i + 1], y), street_class))
            seg_id += 1

    return streets


def _generate_raster_layout(preset, seed) -> CityLayout:
    rng = np.random.default_rng(seed)
    radius = preset.radius
    n_seeds = max(4, int((3.14159 * radius * radius) / (preset.block_size ** 2)))
    seed_points = rng.uniform(-radius, radius, size=(n_seeds, 2))

    cell_size = preset.block_size / 4.0
    grid_n = max(8, int((radius * 2) / cell_size))
    coords = np.linspace(-radius, radius, grid_n)
    gx, gy = np.meshgrid(coords, coords)
    flat_gx = gx.ravel()
    flat_gy = gy.ravel()

    dx = flat_gx[:, None] - seed_points[None, :, 0]
    dy = flat_gy[:, None] - seed_points[None, :, 1]
    nearest_flat = np.argmin(dx * dx + dy * dy, axis=1)
    nearest = nearest_flat.reshape(grid_n, grid_n)

    blocks: List[Block] = []
    for seed_index in range(n_seeds):
        mask = nearest_flat == seed_index
        if not np.any(mask):
            continue
        cell_xs = flat_gx[mask]
        cell_ys = flat_gy[mask]
        min_x, max_x = cell_xs.min(), cell_xs.max()
        min_y, max_y = cell_ys.min(), cell_ys.max()
        hw = ((max_x - min_x) / 2.0) * _FILL_FACTOR
        hh = ((max_y - min_y) / 2.0) * _FILL_FACTOR
        if hw <= 0.5 or hh <= 0.5:
            continue
        blocks.append(Block(id=seed_index, center=((min_x + max_x) / 2.0, (min_y + max_y) / 2.0),
                             half_size=(hw, hh)))

    streets = _raster_street_segments(nearest, blocks, preset)
    return CityLayout(blocks=blocks, streets=streets, radius=radius)


def _rect_exit_point(center, half_size, direction):
    """Point where a ray from `center` (in a normalized `direction`) exits the
    axis-aligned rectangle described by `half_size`."""
    cx, cy = center
    hw, hh = half_size
    dx, dy = direction
    candidates = []
    if abs(dx) > 1e-9:
        candidates.append(hw / abs(dx))
    if abs(dy) > 1e-9:
        candidates.append(hh / abs(dy))
    t = min(candidates) if candidates else 0.0
    return (cx + dx * t, cy + dy * t)


def _raster_street_segments(nearest, blocks: List[Block], preset) -> List[StreetSegment]:
    """Derive street segments from the true seed-adjacency of the raster
    partition (which cells of different seeds touch each other) - unlike
    independently expanding each block's own perimeter, this reuses the
    partition's actual neighbor relationships, so the resulting network is
    guaranteed connected rather than a set of isolated rectangles. Each
    segment is clipped to the gap between the two blocks' rectangle
    boundaries (not their centers), so streets run between buildings rather
    than through them."""
    kept_ids = {block.id for block in blocks}
    block_by_id = {block.id: block for block in blocks}

    adjacency = set()
    for left, right in ((nearest[:, :-1], nearest[:, 1:]), (nearest[:-1, :], nearest[1:, :])):
        differing = left != right
        for a_raw, b_raw in zip(left[differing], right[differing]):
            a, b = int(a_raw), int(b_raw)
            if a in kept_ids and b in kept_ids:
                adjacency.add((min(a, b), max(a, b)))

    streets: List[StreetSegment] = []
    for seg_id, (a, b) in enumerate(sorted(adjacency)):
        block_a, block_b = block_by_id[a], block_by_id[b]
        dx = block_b.center[0] - block_a.center[0]
        dy = block_b.center[1] - block_a.center[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        direction = (dx / length, dy / length)
        start = _rect_exit_point(block_a.center, block_a.half_size, direction)
        end = _rect_exit_point(block_b.center, block_b.half_size, (-direction[0], -direction[1]))
        street_class = "arterial" if seg_id % _ARTERIAL_EVERY_N == 0 else "local"
        streets.append(StreetSegment(seg_id, start, end, street_class))
    return streets

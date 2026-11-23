"""City block + street layout generation.

Two modes selected by `CityPreset.layout_mode`:

- 'grid': a regular lattice of rectangular blocks with light jitter
  (Kleinstadt, Dorf presets - regular, orderly towns).
- 'raster': an irregular street grid - uneven line spacing in both
  directions, clipped to a circle, with a share of neighbouring lots merged
  into larger blocks (Metropole, Industrial presets - varied block sizes
  on a street network that is guaranteed connected; see
  _generate_raster_layout for why the earlier Voronoi-like spoke network
  was replaced).

Pure Python/numpy only - no bpy here. generators/city/streets.py and
buildings.py turn this data into meshes.
"""
import math
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

_FILL_FACTOR = 0.78       # fraction of available extent actually built on; remainder is street gap
_ARTERIAL_EVERY_N = 3     # every Nth grid line / every Nth block is an arterial street
_MIN_BLOCK_HALF_SIZE = 2.0  # meters; raster lots left smaller than this after street insets are dropped
_IRREGULAR_STEP_RANGE = (0.7, 1.45)  # raster mode: successive street spacings, as multiples of block_size
_MERGE_CHANCE = 0.22                 # raster mode: chance a lot merges with its right (or else upper) neighbour


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
    """'raster' mode: an irregular street grid (uneven spacing both ways)
    clipped to a circle, with a share of neighbouring lots merged into
    larger blocks - the organic big/small block mix the old Voronoi-like
    raster partition aimed for, on top of a street network that is
    actually connected.

    The previous implementation turned the nearest-seed raster into
    straight "spoke" segments between adjacent blocks' rectangle edges.
    Measured over several seeds, every one of those ~800 Metropole
    segments ended at its own unique pair of points: the street graph had
    one two-node component per segment, so a raster city had zero
    intersections (no fans, crosswalks, stop signs or traffic lights),
    roads that stopped in mid-air, and ~3% of segments cutting straight
    through a third block's building. Grid lines fix all of that by
    construction: every crossing is a shared node, and blocks are inset
    from each line by that street's half width plus the sidewalk, so no
    road ever touches a building."""
    from .setback import SETBACK_MARGIN, SIDEWALK_WIDTH

    rng = np.random.default_rng(seed)
    radius = preset.radius
    xs = _irregular_lines(rng, radius, preset.block_size)
    ys = _irregular_lines(rng, radius, preset.block_size)
    n_x, n_y = len(xs) - 1, len(ys) - 1

    def line_class(index):
        return "arterial" if index % _ARTERIAL_EVERY_N == 0 else "local"

    def line_inset(index):
        width = preset.street_width_arterial if line_class(index) == "arterial" else preset.street_width_local
        return width / 2.0 + SIDEWALK_WIDTH + SETBACK_MARGIN

    kept = set()
    for i in range(n_x):
        for j in range(n_y):
            cx = (xs[i] + xs[i + 1]) / 2.0
            cy = (ys[j] + ys[j + 1]) / 2.0
            if math.hypot(cx, cy) <= radius:
                kept.add((i, j))

    # Merge some lots with a kept, still-free right-hand or upper neighbour.
    owner = {}
    groups: List[List[Tuple[int, int]]] = []
    for j in range(n_y):
        for i in range(n_x):
            if (i, j) not in kept or (i, j) in owner:
                continue
            cells = [(i, j)]
            roll = rng.random()
            if roll < _MERGE_CHANCE and (i + 1, j) in kept and (i + 1, j) not in owner:
                cells.append((i + 1, j))
            elif roll < 2 * _MERGE_CHANCE and (i, j + 1) in kept and (i, j + 1) not in owner:
                cells.append((i, j + 1))
            for cell in cells:
                owner[cell] = len(groups)
            groups.append(cells)

    blocks: List[Block] = []
    for group in groups:
        i_lo = min(i for i, _ in group)
        i_hi = max(i for i, _ in group) + 1
        j_lo = min(j for _, j in group)
        j_hi = max(j for _, j in group) + 1
        x0, x1 = xs[i_lo] + line_inset(i_lo), xs[i_hi] - line_inset(i_hi)
        y0, y1 = ys[j_lo] + line_inset(j_lo), ys[j_hi] - line_inset(j_hi)
        hw, hh = (x1 - x0) / 2.0, (y1 - y0) / 2.0
        if hw < _MIN_BLOCK_HALF_SIZE or hh < _MIN_BLOCK_HALF_SIZE:
            continue
        blocks.append(Block(id=len(blocks), center=((x0 + x1) / 2.0, (y0 + y1) / 2.0), half_size=(hw, hh)))

    streets: List[StreetSegment] = []

    def wanted(cell_a, cell_b):
        """A grid-line piece exists where at least one side is a kept lot,
        and is removed where both sides belong to one merged block."""
        in_a, in_b = cell_a in owner, cell_b in owner
        if not (in_a or in_b):
            return False
        return not (in_a and in_b and owner[cell_a] == owner[cell_b])

    for i in range(n_x + 1):
        for j in range(n_y):
            if wanted((i - 1, j), (i, j)):
                streets.append(StreetSegment(len(streets), (xs[i], ys[j]), (xs[i], ys[j + 1]), line_class(i)))
    for j in range(n_y + 1):
        for i in range(n_x):
            if wanted((i, j - 1), (i, j)):
                streets.append(StreetSegment(len(streets), (xs[i], ys[j]), (xs[i + 1], ys[j]), line_class(j)))

    return CityLayout(blocks=blocks, streets=streets, radius=radius)


def _irregular_lines(rng, radius: float, block_size: float) -> List[float]:
    """Street-line positions from -radius to just past +radius with
    uneven spacing (_IRREGULAR_STEP_RANGE x block_size), so blocks come in
    visibly different sizes instead of a uniform lattice."""
    # Positions are rounded to streets.py's 0.5 m node-snap grid so the
    # street graph's nodes land exactly on the lines the blocks were inset
    # from (otherwise snapping could move a road up to 0.25 m into a lot).
    lines = [-radius]
    while lines[-1] < radius:
        step = block_size * float(rng.uniform(*_IRREGULAR_STEP_RANGE))
        lines.append(round((lines[-1] + step) * 2.0) / 2.0)
    return lines

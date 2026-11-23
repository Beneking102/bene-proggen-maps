"""Parks and plazas on the 'park' blocks.

`plan_parks` is pure Python: every park block becomes either a lawn park
with a cross of paved paths through its centre (benches along the paths,
trees on the lawn - props.py reads the spec) or, for a share of the
smaller blocks, a paved plaza. `build_park_surfaces` is the bpy step: one
mesh object for all paths/plazas, in the sidewalk's paving material
(materials/road_mat.py reads the same meter-scaled UV map, so the paving
joints line up with the sidewalks). Plazas are flat pads (the terrain is
flattened under them like under a building); paths follow the ground in
short chunks.
"""
import math
import random
from dataclasses import dataclass, field
from typing import List, Tuple

from . import setback
from .layout import Block

PARKS_OBJECT_NAME = "ProcgenMaps_Parks"
PATH_WIDTH = 2.4
PATH_LIFT = 0.12            # meters above the heightfield, like a road strip
PLAZA_LIFT = 0.12
_PLAZA_CHANCE = 0.3
_PLAZA_MAX_AREA = 1600.0    # sq m; bigger park blocks always stay green
_PATH_CHUNK = 4.0


@dataclass
class ParkSpec:
    block_id: int
    kind: str                       # 'park' | 'plaza'
    center: Tuple[float, float]
    half_size: Tuple[float, float]  # road-cleared rectangle (setback applied)
    paths: List[Tuple[float, float, float, float]] = field(default_factory=list)   # (x0, y0, x1, y1) paved rects

    @property
    def rect(self):
        return (self.center[0] - self.half_size[0], self.center[1] - self.half_size[1],
                self.center[0] + self.half_size[0], self.center[1] + self.half_size[1])

    def on_paving(self, x: float, y: float, extra: float = 0.0) -> bool:
        """Whether (x, y) lies on the plaza slab or a path (plus `extra`)."""
        if self.kind == "plaza":
            x0, y0, x1, y1 = self.rect
            return x0 - extra < x < x1 + extra and y0 - extra < y < y1 + extra
        return any(x0 - extra < x < x1 + extra and y0 - extra < y < y1 + extra for (x0, y0, x1, y1) in self.paths)


def plan_parks(blocks: List[Block], zone_by_block, preset, seed=None, roads=None) -> List[ParkSpec]:
    """One ParkSpec per park block. Pure Python, deterministic per seed."""
    rng = random.Random(f"procgen_maps:parks:{preset.seed if seed is None else seed}")
    specs = []
    for block in blocks:
        if zone_by_block.get(block.id) != "park":
            continue
        hw, hh = block.half_size
        if roads:
            hw, hh = setback.shrink_footprint_for_setback(
                block.center, block.half_size, setback.roads_near(block.center, block.half_size, roads),
                min_half_size=1.0)
        if hw < 3.0 or hh < 3.0:
            continue
        cx, cy = block.center
        plaza = block.area <= _PLAZA_MAX_AREA and rng.random() < _PLAZA_CHANCE
        if plaza:
            specs.append(ParkSpec(block.id, "plaza", (cx, cy), (hw, hh)))
            continue
        half_path = PATH_WIDTH / 2.0
        paths = [(cx - hw, cy - half_path, cx + hw, cy + half_path),
                 (cx - half_path, cy - hh, cx + half_path, cy + hh)]
        specs.append(ParkSpec(block.id, "park", (cx, cy), (hw, hh), paths))
    return specs


def bench_spots(spec: ParkSpec, rng: random.Random, count: int) -> List[Tuple[float, float, float]]:
    """(x, y, rotation_z) for benches beside the paths (facing them) or
    around a plaza's edge. Pure Python."""
    spots = []
    cx, cy = spec.center
    hw, hh = spec.half_size
    if spec.kind == "plaza":
        for _ in range(count):
            if rng.random() < 0.5:
                side = rng.choice((-1.0, 1.0))
                spots.append((cx + side * (hw - 1.0), cy + rng.uniform(-hh * 0.7, hh * 0.7), math.pi / 2.0 * side))
            else:
                side = rng.choice((-1.0, 1.0))
                spots.append((cx + rng.uniform(-hw * 0.7, hw * 0.7), cy + side * (hh - 1.0), 0.0 if side < 0 else math.pi))
        return spots
    offset = PATH_WIDTH / 2.0 + 0.7
    for _ in range(count):
        if rng.random() < 0.5:
            side = rng.choice((-1.0, 1.0))
            x = cx + rng.uniform(-hw * 0.8, hw * 0.8)
            if abs(x - cx) < offset + 1.0:
                continue
            spots.append((x, cy + side * offset, 0.0 if side > 0 else math.pi))
        else:
            side = rng.choice((-1.0, 1.0))
            y = cy + rng.uniform(-hh * 0.8, hh * 0.8)
            if abs(y - cy) < offset + 1.0:
                continue
            spots.append((cx + side * offset, y, math.pi / 2.0 if side > 0 else -math.pi / 2.0))
    return spots


def build_park_surfaces(specs: List[ParkSpec], terrain_params=None, collection=None):
    """All plaza slabs and park paths as one mesh object in the sidewalk
    paving material. Returns the created objects (one, or none)."""
    import bpy

    from .. import terrain as terrain_gen
    from ...materials import road_mat
    from .streets import _RoadMeshData

    if not specs:
        return []

    def height(x, y):
        return terrain_gen.sample_world_height(x, y, terrain_params) if terrain_params is not None else 0.0

    data = _RoadMeshData()
    no_ends = (road_mat.ROAD_END_SENTINEL, road_mat.ROAD_END_SENTINEL)
    for spec in specs:
        if spec.kind == "plaza":
            # A grid of quads that follows the ground (like the paths), so
            # the benches and rim trees at field height sit on the slab.
            x0, y0, x1, y1 = spec.rect
            nx = max(1, int(math.ceil((x1 - x0) / _PATH_CHUNK)))
            ny = max(1, int(math.ceil((y1 - y0) / _PATH_CHUNK)))
            grid = []
            for j in range(ny + 1):
                y = y0 + (y1 - y0) * j / ny
                row = []
                for i in range(nx + 1):
                    x = x0 + (x1 - x0) * i / nx
                    row.append(data.add_vert((x, y, height(x, y) + PLAZA_LIFT), (x, y), no_ends))
                grid.append(row)
            for j in range(ny):
                for i in range(nx):
                    data.add_face((grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]), 1)
            continue
        for (x0, y0, x1, y1) in spec.paths:
            along_x = (x1 - x0) >= (y1 - y0)
            length = (x1 - x0) if along_x else (y1 - y0)
            chunks = max(1, int(math.ceil(length / _PATH_CHUNK)))
            rows = []
            for k in range(chunks + 1):
                t = k / chunks
                if along_x:
                    x = x0 + (x1 - x0) * t
                    pair = ((x, y0), (x, y1))
                else:
                    y = y0 + (y1 - y0) * t
                    pair = ((x0, y), (x1, y))
                rows.append(tuple(data.add_vert((px, py, height(px, py) + PATH_LIFT), (px, py), no_ends)
                                  for (px, py) in pair))
            for k in range(chunks):
                (a0, b0), (a1, b1) = rows[k], rows[k + 1]
                # Counter-clockwise from above either way (normal up).
                data.add_face((a0, a1, b1, b0) if along_x else (a0, b0, b1, a1), 1)

    mesh = data.to_mesh(PARKS_OBJECT_NAME, (road_mat.get_or_create_sidewalk_material(),
                                            road_mat.get_or_create_sidewalk_material()))
    obj = bpy.data.objects.new(PARKS_OBJECT_NAME, mesh)
    obj["procgen_maps_parks"] = len(specs)
    if collection is not None:
        collection.objects.link(obj)
    return [obj]

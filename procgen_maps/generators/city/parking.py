"""Supermarket parking lots: a car-grid asphalt pad with painted stall
lines, built in the gap that special_buildings.py's reduced supermarket
footprint_shrink leaves in front of the store.

Split per the project's PP/BPY convention: `plan_parking_lots_for_supermarkets`/
`plan_parking_stalls`/`plan_parking_lines` are pure Python (plain dataclasses
and dicts in/out); `build_parking_lot` is the thin bpy build step that turns
a ParkingLotSpec into an asphalt mesh, painted line-marking meshes, and
parked-car Instance-Collection Empties (via assets.factory.spawn, the same
mechanism generators/city/props.py uses for every other prop).
"""
import math
from dataclasses import dataclass
from typing import List, Tuple

from .buildings import BuildingPlan
from .layout import Block

PARKING_PREFIX = "ProcgenMaps_Parking"

_MIN_FREED_DEPTH = 3.0    # meters; smaller gaps aren't worth turning into a lot
_STALL_WIDTH = 2.6
_STALL_DEPTH = 5.0
_ROW_AISLE = 2.5
_ASPHALT_CELL_SIZE = 8.0  # meters; matches streets.py's terrain-follow subdivision approach
_LINE_WIDTH = 0.12


@dataclass(frozen=True)
class ParkingLotSpec:
    block_id: int
    cx: float
    cy: float
    half_width: float
    half_depth: float


@dataclass(frozen=True)
class ParkingStall:
    x: float
    y: float
    rotation_z: float


def plan_parking_lots_for_supermarkets(blocks: List[Block], plans: List[BuildingPlan]) -> List[ParkingLotSpec]:
    """Every supermarket's footprint is a shrunk, block-centered rectangle
    (special_buildings.py's footprint_shrink=0.55) - so shrinking alone
    already frees an equal margin on all 4 sides of the block, including
    the entrance (-Y) side. This turns just that front margin - between the
    block's own front edge and the building's own front wall - into a
    parking lot, skipping any supermarket whose freed strip is too shallow
    to fit even one row of cars."""
    block_by_id = {block.id: block for block in blocks}
    specs = []
    for plan in plans:
        if plan.facade.key != "supermarket":
            continue
        block = block_by_id.get(plan.block_id)
        if block is None:
            continue

        min_x = min(p[0] for p in plan.footprint)
        max_x = max(p[0] for p in plan.footprint)
        building_min_y = min(p[1] for p in plan.footprint)

        block_cx, block_cy = block.center
        block_hw, block_hh = block.half_size
        front_edge = block_cy - block_hh

        freed_depth = building_min_y - front_edge
        if freed_depth < _MIN_FREED_DEPTH:
            continue

        lot_half_depth = freed_depth / 2.0
        lot_cy = front_edge + lot_half_depth
        lot_half_width = (max_x - min_x) / 2.0 * 0.9
        specs.append(ParkingLotSpec(plan.block_id, block_cx, lot_cy, lot_half_width, lot_half_depth))
    return specs


def _layout_geometry(spec: ParkingLotSpec) -> Tuple[float, int, List[float]]:
    lot_width = spec.half_width * 2.0
    lot_depth = spec.half_depth * 2.0
    n_stalls = max(1, int(lot_width / _STALL_WIDTH))
    stall_width = lot_width / n_stalls

    two_rows = lot_depth >= (_STALL_DEPTH * 2 + _ROW_AISLE)
    if two_rows:
        row_offsets = [-(lot_depth / 2.0 - _STALL_DEPTH / 2.0), (lot_depth / 2.0 - _STALL_DEPTH / 2.0)]
    else:
        row_offsets = [0.0]
    return stall_width, n_stalls, row_offsets


def plan_parking_stalls(spec: ParkingLotSpec) -> List[ParkingStall]:
    """Perpendicular stalls (cars nose-in, long axis along Y) side by side
    across the lot's width - one row, or two rows with a drive aisle
    between them if the lot is deep enough."""
    stall_width, n_stalls, row_offsets = _layout_geometry(spec)
    lot_width = spec.half_width * 2.0

    stalls = []
    for row_y in row_offsets:
        for i in range(n_stalls):
            x = spec.cx - lot_width / 2.0 + stall_width * (i + 0.5)
            y = spec.cy + row_y
            # assets/library.py's "car" builder puts the car's long axis
            # along local X - rotating 90 degrees points it along Y, i.e.
            # perpendicular to the row, matching a real parking stall.
            stalls.append(ParkingStall(x, y, rotation_z=math.pi / 2.0))
    return stalls


def plan_parking_lines(spec: ParkingLotSpec) -> List[Tuple[float, float, float, float]]:
    """One painted divider line between each pair of adjacent stalls (plus
    the two outer edges), spanning each row's own depth. Returned as plain
    (x0, y0, x1, y1) segments."""
    stall_width, n_stalls, row_offsets = _layout_geometry(spec)
    lot_width = spec.half_width * 2.0

    lines = []
    for row_y in row_offsets:
        y0 = spec.cy + row_y - _STALL_DEPTH / 2.0
        y1 = spec.cy + row_y + _STALL_DEPTH / 2.0
        for i in range(n_stalls + 1):
            x = spec.cx - lot_width / 2.0 + stall_width * i
            lines.append((x, y0, x, y1))
    return lines


def build_parking_lot(spec: ParkingLotSpec, terrain_params=None, collection=None):
    """Build one asphalt pad + one line-marking object + one parked-car
    Empty per stall for `spec`. Returns the list of created objects."""
    from .. import terrain as terrain_gen
    from ...assets import factory

    def height_at(x, y):
        if terrain_params is None:
            return 0.0
        return terrain_gen.sample_world_height(x, y, terrain_params) + 0.03  # avoid z-fighting with terrain

    created = []

    asphalt_obj = _build_parking_asphalt(f"{PARKING_PREFIX}_{spec.block_id}_Asphalt", spec, height_at)
    if collection is not None:
        collection.objects.link(asphalt_obj)
    created.append(asphalt_obj)

    lines = plan_parking_lines(spec)
    if lines:
        lines_obj = _build_parking_lines(f"{PARKING_PREFIX}_{spec.block_id}_Lines", lines, height_at)
        if collection is not None:
            collection.objects.link(lines_obj)
        created.append(lines_obj)

    for stall in plan_parking_stalls(spec):
        z = height_at(stall.x, stall.y)
        empty = factory.spawn("parked_car", (stall.x, stall.y, z), rotation_z=stall.rotation_z,
                               collection=collection)
        created.append(empty)

    return created


def _build_parking_asphalt(name, spec: ParkingLotSpec, height_at):
    import bpy

    x0, x1 = spec.cx - spec.half_width, spec.cx + spec.half_width
    y0, y1 = spec.cy - spec.half_depth, spec.cy + spec.half_depth
    n_x = max(1, math.ceil((x1 - x0) / _ASPHALT_CELL_SIZE))
    n_y = max(1, math.ceil((y1 - y0) / _ASPHALT_CELL_SIZE))

    verts = []
    index_by_cell = {}
    for j in range(n_y + 1):
        for i in range(n_x + 1):
            x = x0 + (x1 - x0) * i / n_x
            y = y0 + (y1 - y0) * j / n_y
            index_by_cell[(i, j)] = len(verts)
            verts.append((x, y, height_at(x, y)))

    faces = []
    for j in range(n_y):
        for i in range(n_x):
            faces.append((index_by_cell[(i, j)], index_by_cell[(i + 1, j)],
                          index_by_cell[(i + 1, j + 1)], index_by_cell[(i, j + 1)]))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)

    from ...materials import prop_mat
    mesh.materials.append(prop_mat.get_or_create_prop_material("parking_asphalt"))
    return bpy.data.objects.new(name, mesh)


def _build_parking_lines(name, lines, height_at):
    import bpy

    verts = []
    faces = []
    for (x0, y0, x1, y1) in lines:
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        dx, dy = dx / length, dy / length
        side_x, side_y = -dy * _LINE_WIDTH / 2.0, dx * _LINE_WIDTH / 2.0

        idx = len(verts)
        z0 = height_at(x0, y0) + 0.02
        z1 = height_at(x1, y1) + 0.02
        verts.append((x0 + side_x, y0 + side_y, z0))
        verts.append((x0 - side_x, y0 - side_y, z0))
        verts.append((x1 - side_x, y1 - side_y, z1))
        verts.append((x1 + side_x, y1 + side_y, z1))
        faces.append((idx, idx + 1, idx + 2, idx + 3))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)

    from ...materials import prop_mat
    mesh.materials.append(prop_mat.get_or_create_prop_material("parking_line"))
    return bpy.data.objects.new(name, mesh)

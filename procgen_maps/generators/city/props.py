"""Prop placement: street lamps, benches, trees, parked cars, and signs.

`plan_props` is pure Python: computes placement points along streets and
inside park/commercial blocks, filtered through a `SpatialHashGrid` so
footprints never overlap. `build_props` is the thin bpy build step that
hands each planned prop to `assets.factory.spawn`.
"""
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Tuple

from ...assets import library
from ...utils.spatial import SpatialHashGrid
from .. import terrain as terrain_gen
from .buildings import BuildingPlan
from .layout import Block
from .streets import StreetGraph

PROPS_COLLECTION_NAME = "ProcgenMaps_Props"

_LAMP_SPACING = 25.0
_TREE_STREET_SPACING = 18.0
_PARK_TREE_DENSITY = 0.02        # trees per square meter of park block area
_CAR_SPACING = 10.0
_SIGN_CHANCE_AT_SHOPFRONT = 0.6
_PARK_FOUNTAIN_MIN_AREA = 400.0  # sq meters; smaller parks get benches/trees only, no centerpiece


@dataclass
class PropPlacement:
    asset_id: str
    location: Tuple[float, float, float]
    rotation_z: float = 0.0
    scale: float = 1.0


def plan_props(graph: StreetGraph, blocks: List[Block], zone_by_block: Dict[int, str],
                preset, seed=None, terrain_params=None, building_plans: List[BuildingPlan] = None) -> List[PropPlacement]:
    """Compute collision-free placement points for all prop categories.

    If `terrain_params` is given, each prop's z is sampled from the same
    noise field as the terrain mesh/streets/buildings (see
    generators/terrain.py), so props sit on the ground instead of floating
    above or getting buried under sloped terrain.

    If `building_plans` is given, each building's footprint is pre-registered
    in the collision grid (as its bounding circle - a conservative
    approximation, since footprints are rectangles) before any prop is
    placed, so lamps/trees/benches/signs never spawn inside or clipping a
    building."""
    rng = random.Random(preset.seed if seed is None else seed)
    grid = SpatialHashGrid(cell_size=5.0)
    placements: List[PropPlacement] = []
    density = max(preset.prop_density, 0.05)
    tree_ids = library.tree_asset_ids()

    for index, plan in enumerate(building_plans or []):
        cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
        cy = sum(p[1] for p in plan.footprint) / len(plan.footprint)
        radius = max(math.hypot(p[0] - cx, p[1] - cy) for p in plan.footprint)
        grid.insert(f"building_{index}", cx, cy, radius)

    def height_at(x, y):
        if terrain_params is None:
            return 0.0
        return terrain_gen.sample_world_height(x, y, terrain_params)

    def try_place(asset_id, x, y, rotation_z=0.0, scale=1.0):
        radius = library.get_asset(asset_id).footprint_radius
        if grid.has_collision(x, y, radius):
            return False
        grid.insert(asset_id, x, y, radius)
        placements.append(PropPlacement(asset_id, (x, y, height_at(x, y)), rotation_z, scale))
        return True

    for (a, b, street_class) in graph.edges:
        start, end = graph.nodes[a], graph.nodes[b]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length < 1e-6:
            continue
        dx, dy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        nx, ny = -dy, dx
        half_width = preset.street_width_arterial if street_class == "arterial" else preset.street_width_local
        offset = half_width / 2.0 + 1.5

        n_lamps = max(1, int(length / (_LAMP_SPACING / density)))
        for i in range(n_lamps + 1):
            t = i / max(n_lamps, 1)
            x, y = start[0] + dx * length * t + nx * offset, start[1] + dy * length * t + ny * offset
            try_place("street_lamp", x, y, rotation_z=math.atan2(dy, dx))

        n_trees = max(1, int(length / (_TREE_STREET_SPACING / density)))
        for i in range(n_trees + 1):
            t = (i + 0.5) / max(n_trees, 1)
            x, y = start[0] + dx * length * t - nx * offset, start[1] + dy * length * t - ny * offset
            try_place(rng.choice(tree_ids), x, y, rotation_z=rng.uniform(0, math.tau), scale=rng.uniform(0.85, 1.15))

        if preset.cars_enabled and street_class == "local" and length >= _CAR_SPACING:
            car_offset = preset.street_width_local / 2.0 + 1.2
            n_cars = int(length / _CAR_SPACING)
            for i in range(n_cars):
                if rng.random() > density:
                    continue
                t = (i + 0.5) / max(n_cars, 1)
                x = start[0] + dx * length * t + nx * car_offset
                y = start[1] + dy * length * t + ny * car_offset
                try_place("parked_car", x, y, rotation_z=math.atan2(dy, dx))

    for block in blocks:
        zone = zone_by_block.get(block.id)
        cx, cy = block.center
        hw, hh = block.half_size

        if zone == "park":
            if block.area >= _PARK_FOUNTAIN_MIN_AREA:
                try_place("fountain", cx, cy)

            n_trees = max(1, int(block.area * _PARK_TREE_DENSITY * density))
            for _ in range(n_trees):
                x, y = cx + rng.uniform(-hw, hw), cy + rng.uniform(-hh, hh)
                try_place(rng.choice(tree_ids), x, y, rotation_z=rng.uniform(0, math.tau), scale=rng.uniform(0.85, 1.15))

            n_benches = max(0, int((block.area ** 0.5) / 15.0 * density))
            for _ in range(n_benches):
                x, y = cx + rng.uniform(-hw * 0.6, hw * 0.6), cy + rng.uniform(-hh * 0.6, hh * 0.6)
                try_place("bench", x, y, rotation_z=rng.uniform(0, math.tau))

        elif zone == "commercial" and rng.random() < _SIGN_CHANCE_AT_SHOPFRONT * density:
            try_place("sign", cx + hw * 0.95, cy, rotation_z=math.pi / 2.0)

    return placements


def build_props(placements: List[PropPlacement], collection=None):
    """Spawn every planned prop via assets.factory.spawn. Returns the created Empty objects."""
    from ...assets import factory

    created = []
    for placement in placements:
        empty = factory.spawn(placement.asset_id, placement.location, rotation_z=placement.rotation_z,
                               scale=placement.scale, collection=collection)
        created.append(empty)
    return created

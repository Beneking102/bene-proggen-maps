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
from . import setback
from .setback import SIDEWALK_WIDTH
from .streets import StreetGraph, node_fan_radii, sidewalk_trim

PROPS_COLLECTION_NAME = "ProcgenMaps_Props"

_LAMP_SPACING = 25.0
_TREE_STREET_SPACING = 18.0
_PARK_TREE_DENSITY = 0.035       # trees per square meter of park block area
_CAR_SPACING = 10.0
_SIGN_CHANCE_AT_SHOPFRONT = 0.6
_PARK_FOUNTAIN_MIN_AREA = 400.0  # sq meters; smaller parks get benches/trees only, no centerpiece
_SIDEWALK_PROP_INSET = 1.0       # meters from the curb to the lamp/tree line (inside the 2 m sidewalk)
_CAR_LANE_INSET = 1.3            # meters from the curb to a parked car's centerline
_END_CLEARANCE = 0.5             # meters past the crossing street's sidewalk before the first lamp/tree
_PARKING_LOT_MARGIN = 0.5        # meters around a parking lot kept free of street props

# City-edge forest: trees scattered in a ring outside the built-up radius,
# thinning out with distance, so the city doesn't end in a hard line on
# bare ground. Density is per square meter at the inner edge of the ring.
_FOREST_RING_START = 1.06        # x city radius
_FOREST_RING_END = 1.9           # x city radius
_FOREST_DENSITY = 0.0028         # trees / sqm at the inner edge (~1 tree per 19 m)
_FOREST_MAX_TREES = 3500


@dataclass
class PropPlacement:
    asset_id: str
    location: Tuple[float, float, float]
    rotation_z: float = 0.0
    scale: float = 1.0


def plan_props(graph: StreetGraph, blocks: List[Block], zone_by_block: Dict[int, str],
                preset, seed=None, terrain_params=None, building_plans: List[BuildingPlan] = None,
                city_radius: float = None, parking_lot_specs=None, river=None, park_specs=None) -> List[PropPlacement]:
    """Compute collision-free placement points for all prop categories.

    If `city_radius` is given, a forest ring is scattered outside it (see
    plan_forest_ring) so the city blends into the landscape.

    If `terrain_params` is given, each prop's z is sampled from the same
    noise field as the terrain mesh/streets/buildings (see
    generators/terrain.py), so props sit on the ground instead of floating
    above or getting buried under sloped terrain.

    If `building_plans` is given, each building's footprint is pre-registered
    in the collision grid (as its bounding circle - a conservative
    approximation, since footprints are rectangles) before any prop is
    placed, so lamps/trees/benches/signs never spawn inside or clipping a
    building. `parking_lot_specs` (parking.ParkingLotSpec) likewise keep
    their rectangles free of street props."""
    rng = random.Random(preset.seed if seed is None else seed)
    grid = SpatialHashGrid(cell_size=5.0)
    placements: List[PropPlacement] = []
    density = max(preset.prop_density, 0.05)
    tree_ids = library.tree_asset_ids()
    fan_radius_by_node = node_fan_radii(graph, preset)
    roads = setback.roads_from_graph(graph, preset)
    lot_rects = [(spec.cx - spec.half_width - _PARKING_LOT_MARGIN, spec.cy - spec.half_depth - _PARKING_LOT_MARGIN,
                  spec.cx + spec.half_width + _PARKING_LOT_MARGIN, spec.cy + spec.half_depth + _PARKING_LOT_MARGIN)
                 for spec in (parking_lot_specs or [])]

    for index, plan in enumerate(building_plans or []):
        cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
        cy = sum(p[1] for p in plan.footprint) / len(plan.footprint)
        radius = max(math.hypot(p[0] - cx, p[1] - cy) for p in plan.footprint)
        grid.insert(f"building_{index}", cx, cy, radius)

    def height_at(x, y):
        if terrain_params is None:
            return 0.0
        return terrain_gen.sample_world_height(x, y, terrain_params)

    park_by_block = {spec.block_id: spec for spec in (park_specs or [])}

    def try_place(asset_id, x, y, rotation_z=0.0, scale=1.0):
        # The footprint grows with the instance scale (forest trees are up
        # to 1.6x) - checking the unscaled radius let big trees overlap.
        radius = library.get_asset(asset_id).footprint_radius * scale
        if grid.has_collision(x, y, radius):
            return False
        if any(x0 < x < x1 and y0 < y < y1 for (x0, y0, x1, y1) in lot_rects):
            return False
        # Nothing grows in the river or on its sloped banks; lamps and
        # cars on a bridge are fine (they stand on the deck's sidewalk).
        if river is not None and asset_id not in ("street_lamp", "parked_car") and \
                river.contains(x, y, river.bank_margin + radius):
            return False
        grid.insert(asset_id, x, y, radius)
        placements.append(PropPlacement(asset_id, (x, y, height_at(x, y)), rotation_z, scale))
        return True

    for (a, b, street_class) in graph.edges:
        if street_class == "country":
            continue   # no lamps, street trees or parked cars out in the country
        start, end = graph.nodes[a], graph.nodes[b]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length < 1e-6:
            continue
        dx, dy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        nx, ny = -dy, dx
        width = preset.street_width_arterial if street_class == "arterial" else preset.street_width_local
        # Lamps and trees stand on the sidewalk (streets.py builds one
        # SIDEWALK_WIDTH wide along each curb), not out in the grass.
        offset = width / 2.0 + _SIDEWALK_PROP_INSET

        # Keep clear of the crossing street at each end: the sidewalk
        # itself stops sidewalk_trim() short of the node, and a lamp placed
        # at the node (t = 0 / t = 1, as before) stood in the middle of the
        # crossing street's asphalt - several hundred per Metropole.
        trim_a, trim_b = street_prop_trims(graph, a, b, fan_radius_by_node)
        usable = length - trim_a - trim_b

        def along_points(spacing, half_step_offset):
            if usable <= 0.0:
                return []   # a stub edge entirely inside its two crossings' clearance
            n = max(1, int(usable / (spacing / density)))
            if half_step_offset:
                return [trim_a + usable * (i + 0.5) / n for i in range(n)]
            return [trim_a + usable * i / n for i in range(n + 1)]

        for along in along_points(_LAMP_SPACING, half_step_offset=False):
            x, y = start[0] + dx * along + nx * offset, start[1] + dy * along + ny * offset
            try_place("street_lamp", x, y, rotation_z=math.atan2(dy, dx))

        for along in along_points(_TREE_STREET_SPACING, half_step_offset=True):
            x, y = start[0] + dx * along - nx * offset, start[1] + dy * along - ny * offset
            try_place(rng.choice(tree_ids), x, y, rotation_z=rng.uniform(0, math.tau), scale=rng.uniform(0.85, 1.15))

        if preset.cars_enabled and street_class == "local" and usable >= _CAR_SPACING:
            # Parked *on* the road, in the kerbside lane - previously the
            # cars were offset 1.2 m *outside* the asphalt, i.e. on the grass
            # - and, like the lamps, only along the trimmed range so no car
            # sits in the intersection fan or on the zebra crossing.
            car_offset = preset.street_width_local / 2.0 - _CAR_LANE_INSET
            n_cars = int(usable / _CAR_SPACING)
            for i in range(n_cars):
                if rng.random() > density:
                    continue
                along = trim_a + usable * (i + 0.5) / max(n_cars, 1)
                x = start[0] + dx * along + nx * car_offset
                y = start[1] + dy * along + ny * car_offset
                try_place("parked_car", x, y, rotation_z=math.atan2(dy, dx))

    for block in blocks:
        zone = zone_by_block.get(block.id)
        cx, cy = block.center
        hw, hh = block.half_size

        if zone == "park":
            park = park_by_block.get(block.id)
            if block.area >= _PARK_FOUNTAIN_MIN_AREA:
                try_place("fountain", cx, cy)

            # Scatter only over the part of the block that keeps a sidewalk's
            # clearance from every road - the same setback solver the
            # buildings use. Grid blocks overlap their arterials by up to
            # ~1.7 m (setback.py), so the full block put trees on the asphalt.
            hw, hh = setback.shrink_footprint_for_setback(
                block.center, block.half_size, setback.roads_near(block.center, block.half_size, roads),
                min_half_size=1.0)
            # A plaza is paved: a few trees along its rim only; a park keeps
            # its trees off the paths (parks.py lays a cross through the centre).
            n_trees = max(1, int(block.area * _PARK_TREE_DENSITY * density))
            if park is not None and park.kind == "plaza":
                n_trees = max(1, n_trees // 4)
            placed_trees = 0
            for _ in range(n_trees * 3):   # retries: the path cross and the benches reject a share of candidates
                if placed_trees >= n_trees:
                    break
                if park is not None and park.kind == "plaza":
                    x = cx + rng.choice((-1.0, 1.0)) * (hw - 1.5)
                    y = cy + rng.uniform(-hh + 1.5, hh - 1.5)
                else:
                    x, y = cx + rng.uniform(-hw, hw), cy + rng.uniform(-hh, hh)
                if park is not None and park.kind == "park" and park.on_paving(x, y, 1.2):
                    continue
                if try_place(rng.choice(tree_ids), x, y, rotation_z=rng.uniform(0, math.tau), scale=rng.uniform(0.85, 1.15)):
                    placed_trees += 1

            n_benches = max(0, int((block.area ** 0.5) / 15.0 * density))
            if park is not None:
                from .parks import bench_spots
                n_benches = max(2, n_benches * 2)
                for x, y, rotation_z in bench_spots(park, rng, n_benches):
                    try_place("bench", x, y, rotation_z=rotation_z)
                # A lamp at each path end / plaza corner: parks are lit at night too.
                for sx, sy in ((-1.0, -1.0), (1.0, 1.0)):
                    try_place("street_lamp", cx + sx * (hw - 0.8), cy + sy * (hh - 0.8))
            else:
                for _ in range(n_benches):
                    x, y = cx + rng.uniform(-hw * 0.6, hw * 0.6), cy + rng.uniform(-hh * 0.6, hh * 0.6)
                    try_place("bench", x, y, rotation_z=rng.uniform(0, math.tau))

        elif zone == "commercial" and rng.random() < _SIGN_CHANCE_AT_SHOPFRONT * density:
            # At the block's road-cleared edge (see the park case above), not
            # the raw block edge, which can be asphalt on grid arterials.
            hw, _hh = setback.shrink_footprint_for_setback(
                block.center, block.half_size, setback.roads_near(block.center, block.half_size, roads),
                min_half_size=1.0)
            try_place("sign", cx + hw, cy, rotation_z=math.pi / 2.0)

    if city_radius is not None and city_radius > 0.0:
        # Street lines run out to (and a little past) the city circle, so
        # the inner forest ring overlaps their last segments - keep those
        # trees off the asphalt and sidewalk too.
        rim = city_radius * _FOREST_RING_START - 2.0 * (preset.street_width_arterial + SIDEWALK_WIDTH)
        rim_roads = [road for road in roads
                     if max(math.hypot(road[0], road[1]), math.hypot(road[2], road[3])) >= rim]
        park_rects = [spec.rect for spec in park_by_block.values()]
        for x, y, scale, rotation_z in plan_forest_ring(city_radius, rng):
            if setback.road_clearance((x, y, x, y), rim_roads) < SIDEWALK_WIDTH:
                continue
            # The ring starts just outside the city circle, which the
            # outermost blocks can still reach: no wild trees on a rim
            # park's paths or plaza.
            if any(x0 - 1.0 < x < x1 + 1.0 and y0 - 1.0 < y < y1 + 1.0 for (x0, y0, x1, y1) in park_rects):
                continue
            try_place(rng.choice(tree_ids), x, y, rotation_z=rotation_z, scale=scale)

    return placements


def street_prop_trims(graph: StreetGraph, a: int, b: int, fan_radius_by_node: Dict[int, float]) -> Tuple[float, float]:
    """(trim at node a, trim at node b): how far along an edge the first
    sidewalk prop may stand from each end node - the crossing street's
    half width plus its sidewalk (streets.sidewalk_trim) plus a small
    clearance - or 0 at a dead end. Pure Python, unit-tested."""
    trims = []
    for node in (a, b):
        trim = sidewalk_trim(graph.node_degree.get(node, 0), fan_radius_by_node.get(node, 0.0))
        trims.append(trim + _END_CLEARANCE if trim > 0.0 else 0.0)
    return trims[0], trims[1]


def plan_forest_ring(city_radius: float, rng: random.Random) -> List[Tuple[float, float, float, float]]:
    """(x, y, scale, rotation_z) candidates for the city-edge forest: points
    uniformly distributed over the ring [_FOREST_RING_START, _FOREST_RING_END]
    x radius, each kept with a probability that falls off linearly with
    distance so the forest thins out instead of ending in another hard
    edge. Larger trees (up to 1.6x) than the manicured street trees. Pure
    Python; callers push the result through the collision grid."""
    inner = city_radius * _FOREST_RING_START
    outer = city_radius * _FOREST_RING_END
    ring_area = math.pi * (outer * outer - inner * inner)
    count = min(_FOREST_MAX_TREES, int(ring_area * _FOREST_DENSITY))
    candidates = []
    for _ in range(count):
        # Uniform in area: r = sqrt(u * (R^2 - r0^2) + r0^2).
        r = math.sqrt(rng.random() * (outer * outer - inner * inner) + inner * inner)
        falloff = 1.0 - (r - inner) / (outer - inner)
        if rng.random() > falloff:
            continue
        angle = rng.uniform(0.0, math.tau)
        candidates.append((r * math.cos(angle), r * math.sin(angle), rng.uniform(1.0, 1.6), rng.uniform(0.0, math.tau)))
    return candidates


def build_props(placements: List[PropPlacement], collection=None):
    """Spawn every planned prop via assets.factory.spawn. Returns the created Empty objects."""
    from ...assets import factory

    created = []
    for placement in placements:
        empty = factory.spawn(placement.asset_id, placement.location, rotation_z=placement.rotation_z,
                               scale=placement.scale, collection=collection)
        created.append(empty)
    return created

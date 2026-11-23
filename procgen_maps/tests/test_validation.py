"""Pure-Python tests for procgen_maps/validation.py and the multi-seed fuzz
over every preset: every (preset, seed) plan must validate clean and be
deterministic, including seed 0, a huge seed, and a city without terrain."""
import math

import pytest

from procgen_maps.config.presets import PRESETS
from procgen_maps.generators.city import plan_city
from procgen_maps.generators.city.buildings import BuildingPlan, FacadeType
from procgen_maps.generators.city.layout import Block
from procgen_maps.generators.city.parking import ParkingLotSpec, plan_parking_lots_for_supermarkets
from procgen_maps.generators.city.props import PropPlacement, plan_props, street_prop_trims
from procgen_maps.generators.city.setback import SIDEWALK_WIDTH, road_clearance
from procgen_maps.generators.city.signage import plan_signage
from procgen_maps.generators.city.streets import (StreetGraph, build_street_graph, corner_sidewalk_paths,
                                                   node_corridor_discs, node_fan_radii, road_corridors)
from procgen_maps.generators.city.layout import StreetSegment
from procgen_maps.generators.dungeon import DungeonParams, generate_dungeon
from procgen_maps.generators.terrain import TerrainParams
from procgen_maps.validation import ValidationReport, street_components, validate_city_plan

HUGE_SEED = 2**31 - 1
FUZZ_SEEDS = (0, 1, 2, 3, 5, 8, 13, 21, 42, 99, 12345, HUGE_SEED)

_FACADE = FacadeType("office_block", ("commercial",), 0.7, 3.0, "flat", 1)


def _cross_graph(width_axis=60.0):
    """A '+' of two streets crossing at the origin: 5 nodes, 4 edges, degree-4 center."""
    segments = [
        StreetSegment(0, (-width_axis, 0.0), (0.0, 0.0), "arterial"),
        StreetSegment(1, (0.0, 0.0), (width_axis, 0.0), "arterial"),
        StreetSegment(2, (0.0, -width_axis), (0.0, 0.0), "local"),
        StreetSegment(3, (0.0, 0.0), (0.0, width_axis), "local"),
    ]
    return build_street_graph(segments)


# --- fuzz: every preset x many seeds -----------------------------------------

@pytest.mark.parametrize("preset_key", sorted(PRESETS))
@pytest.mark.parametrize("seed", FUZZ_SEEDS)
def test_every_preset_and_seed_plans_a_clean_city(preset_key, seed):
    preset = PRESETS[preset_key]
    terrain = TerrainParams(seed=seed, world_size=preset.radius * 4.2)
    plan = plan_city(preset, terrain_params=terrain, seed=seed)
    report = validate_city_plan(plan, preset)
    assert report.ok, report.format()
    assert plan.building_plans, "a city must have buildings"
    assert street_components(plan.street_graph) == [len(plan.street_graph.nodes)]


@pytest.mark.parametrize("preset_key", sorted(PRESETS))
@pytest.mark.parametrize("seed", (0, 42, HUGE_SEED))
def test_plan_city_is_deterministic(preset_key, seed):
    preset = PRESETS[preset_key]
    terrain = TerrainParams(seed=seed)
    assert repr(plan_city(preset, terrain, seed=seed)) == repr(plan_city(preset, terrain, seed=seed))


@pytest.mark.parametrize("preset_key", sorted(PRESETS))
def test_plan_city_without_terrain_puts_everything_at_ground_zero(preset_key):
    preset = PRESETS[preset_key]
    plan = plan_city(preset, terrain_params=None, seed=4)
    assert validate_city_plan(plan, preset).ok
    assert all(p.location[2] == 0.0 for p in plan.prop_placements)
    assert all(p.location[2] == 0.0 for p in plan.sign_placements)


@pytest.mark.parametrize("seed", (0, 1, HUGE_SEED))
def test_dungeon_handles_edge_seeds(seed):
    layout = generate_dungeon(DungeonParams(seed=seed))
    assert layout.rooms
    assert repr(generate_dungeon(DungeonParams(seed=seed))) == repr(layout)


@pytest.mark.parametrize("seed", (0, HUGE_SEED))
def test_terrain_heightmap_handles_edge_seeds(seed):
    from procgen_maps.generators.terrain import generate_heightmap, sample_world_height

    params = TerrainParams(seed=seed, resolution=16)
    heights = generate_heightmap(params)
    assert heights.shape == (16, 16)
    assert math.isfinite(float(heights.sum()))
    assert math.isfinite(sample_world_height(0.0, 0.0, params))


# --- regressions behind the fuzz ---------------------------------------------

def test_street_props_keep_clear_of_the_crossing_street():
    """Lamps/trees used to be placed at t=0/t=1 of every edge, i.e. in the
    middle of the crossing street's asphalt (several hundred per Metropole)."""
    preset = PRESETS["METROPOLE"]
    graph = _cross_graph()
    placements = plan_props(graph, [], {}, preset, seed=1)
    half_arterial = preset.street_width_arterial / 2.0
    half_local = preset.street_width_local / 2.0
    fan_clear = preset.street_width_arterial / 2.0 + SIDEWALK_WIDTH
    for placement in placements:
        x, y, _z = placement.location
        if placement.asset_id == "parked_car":
            # Cars park on the road by design, but never in the crossing/crosswalk zone.
            assert math.hypot(x, y) > fan_clear, placement
            continue
        # Off both roads' asphalt: the horizontal arterial (|y| <= 7) and the vertical local (|x| <= 3.5).
        assert abs(y) > half_arterial + 0.01 or abs(x) > half_axis_clear(half_arterial), placement
        assert abs(x) > half_local + 0.01 or abs(y) > half_axis_clear(half_local), placement
    assert any(p.asset_id == "street_lamp" for p in placements)


def half_axis_clear(half_width):
    return half_width + SIDEWALK_WIDTH


def test_street_prop_trims_zero_at_dead_ends_and_cover_crossing_at_nodes():
    preset = PRESETS["KLEINSTADT"]
    graph = _cross_graph()
    radii = node_fan_radii(graph, preset)
    center = next(n for n, d in graph.node_degree.items() if d == 4)
    leaf = next(n for n, d in graph.node_degree.items() if d == 1)
    trim_center, trim_leaf = street_prop_trims(graph, center, leaf, radii)
    assert trim_leaf == 0.0
    assert trim_center > preset.street_width_arterial / 2.0 + SIDEWALK_WIDTH


def test_plan_props_scales_collision_radius_with_instance_scale():
    """Forest trees are up to 1.6x; the grid checked the unscaled radius so
    big trees overlapped each other."""
    from procgen_maps.assets import library

    preset = PRESETS["KLEINSTADT"]
    graph = StreetGraph(nodes=[], edges=[], node_degree={})
    placements = plan_props(graph, [], {}, preset, seed=3, city_radius=60.0)
    trees = [p for p in placements if library.get_asset(p.asset_id).category == "tree"]
    assert len(trees) > 20
    for i, a in enumerate(trees):
        ra = library.get_asset(a.asset_id).footprint_radius * a.scale
        for b in trees[i + 1:]:
            rb = library.get_asset(b.asset_id).footprint_radius * b.scale
            assert math.hypot(a.location[0] - b.location[0], a.location[1] - b.location[1]) >= ra + rb - 1e-6


def test_plan_props_keeps_street_props_out_of_parking_lots():
    preset = PRESETS["KLEINSTADT"]
    graph = _cross_graph()
    lot = ParkingLotSpec(block_id=0, cx=20.0, cy=-8.0, half_width=12.0, half_depth=3.0)
    placements = plan_props(graph, [], {}, preset, seed=1, parking_lot_specs=[lot])
    for p in placements:
        x, y, _z = p.location
        assert not (lot.cx - lot.half_width < x < lot.cx + lot.half_width
                    and lot.cy - lot.half_depth < y < lot.cy + lot.half_depth), p


def test_traffic_light_stands_on_the_corner_not_in_the_crossing_street():
    preset = PRESETS["METROPOLE"]
    segments = [
        StreetSegment(0, (-60.0, 0.0), (0.0, 0.0), "arterial"),
        StreetSegment(1, (0.0, 0.0), (60.0, 0.0), "arterial"),
        StreetSegment(2, (0.0, -60.0), (0.0, 0.0), "arterial"),
        StreetSegment(3, (0.0, 0.0), (0.0, 60.0), "arterial"),
    ]
    graph = build_street_graph(segments)
    lights = [s for s in plan_signage(graph, preset, seed=1) if s.kind == "traffic_light"]
    assert lights
    half = preset.street_width_arterial / 2.0
    for light in lights:
        x, y, _z = light.location
        assert abs(x) > half + 0.01 and abs(y) > half + 0.01, light


def test_parking_lot_front_is_pulled_back_from_the_road():
    """Grid blocks overlap their arterials by ~1.7 m, so a lot reaching the
    block's front edge was asphalt-on-asphalt."""
    block = Block(id=0, center=(0.0, 0.0), half_size=(15.0, 15.0))
    supermarket = FacadeType("supermarket", ("commercial",), 0.35, 4.0, "flat", 12)
    plan = BuildingPlan(0, [(-8.0, -3.0), (8.0, -3.0), (8.0, 13.0), (-8.0, 13.0)], 1, 5.5, supermarket)
    # A 14 m wide road whose centerline sits 1.7 m inside the block's front edge (y = -15).
    roads = [(-100.0, -13.3 - 7.0, 100.0, -13.3 - 7.0, 14.0)]
    without = plan_parking_lots_for_supermarkets([block], [plan])
    with_roads = plan_parking_lots_for_supermarkets([block], [plan], roads=roads)
    assert len(without) == len(with_roads) == 1
    lot = with_roads[0]
    rect = (lot.cx - lot.half_width, lot.cy - lot.half_depth, lot.cx + lot.half_width, lot.cy + lot.half_depth)
    assert road_clearance(rect, roads) >= SIDEWALK_WIDTH - 1e-6
    assert lot.half_depth < without[0].half_depth


# --- the validator itself ----------------------------------------------------

def _plan_with(buildings=(), props=(), signs=(), lots=(), graph=None):
    from procgen_maps.generators.city import CityPlan
    from procgen_maps.generators.city.layout import CityLayout

    graph = graph if graph is not None else _cross_graph()
    return CityPlan(CityLayout(), {}, graph, list(buildings), [], list(props), list(signs), list(lots))


def test_validator_detects_overlapping_buildings_and_buildings_on_roads():
    a = BuildingPlan(0, [(20.0, 20.0), (40.0, 20.0), (40.0, 40.0), (20.0, 40.0)], 2, 3.0, _FACADE)
    b = BuildingPlan(1, [(30.0, 30.0), (50.0, 30.0), (50.0, 50.0), (30.0, 50.0)], 2, 3.0, _FACADE)
    on_road = BuildingPlan(2, [(10.0, -2.0), (30.0, -2.0), (30.0, 10.0), (10.0, 10.0)], 2, 3.0, _FACADE)
    report = validate_city_plan(_plan_with(buildings=[a, b, on_road]), PRESETS["KLEINSTADT"])
    assert report.count("building_overlap") == 1
    assert report.count("building_on_road") == 1


def test_validator_detects_props_in_buildings_on_roads_and_overlapping():
    building = BuildingPlan(0, [(20.0, 20.0), (40.0, 20.0), (40.0, 40.0), (20.0, 40.0)], 2, 3.0, _FACADE)
    props = [
        PropPlacement("street_lamp", (30.0, 30.0, 0.0)),          # inside the building
        PropPlacement("tree_01", (10.0, 0.0, 0.0)),               # on the arterial
        PropPlacement("parked_car", (40.0, 2.0, 0.0)),            # cars may stand on the road
        PropPlacement("bench", (-30.0, 30.0, 0.0)),
        PropPlacement("bench", (-30.2, 30.0, 0.0)),               # overlaps the first bench
    ]
    report = validate_city_plan(_plan_with(buildings=[building], props=props), PRESETS["KLEINSTADT"])
    assert report.count("prop_in_building") == 1
    assert report.count("prop_on_road") == 1
    assert report.count("prop_overlap") == 1


def test_validator_detects_disconnected_streets_and_clean_report_formats():
    segments = [StreetSegment(0, (0.0, 0.0), (10.0, 0.0), "local"), StreetSegment(1, (50.0, 50.0), (60.0, 50.0), "local")]
    graph = build_street_graph(segments)
    assert street_components(graph) == [2, 2]
    report = validate_city_plan(_plan_with(graph=graph), PRESETS["DORF"])
    assert report.count("disconnected_streets") == 1
    assert "disconnected_streets: 1" in report.summary()
    assert ValidationReport().ok and ValidationReport().summary().startswith("OK")
    assert "checked:" in report.format()


# --- v0.12.0 regressions: terrain under roads, sidewalk corners --------------

def _wedge_area(inner, outer):
    """Shoelace area of the ring between an inner and an outer polyline."""
    ring = list(inner) + list(reversed(outer))
    return abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
                   for i in range(len(ring)))) / 2.0


def test_corner_sidewalks_close_every_gap_at_a_four_way_crossing():
    """v0.11.0 trimmed each strip's sidewalk fan_radius + SIDEWALK_WIDTH short
    of the node and built nothing in between: every crossing had a bare
    sidewalk-width square of grass on each of its four corners."""
    half = 4.0
    outgoing = [((1.0, 0.0), half), ((0.0, 1.0), half), ((-1.0, 0.0), half), ((0.0, -1.0), half)]
    pieces = corner_sidewalk_paths((10.0, -5.0), outgoing, fan_radius=half)
    assert len(pieces) == 4
    for inner, outer in pieces:
        assert len(inner) == len(outer) == 3
        # Inner path: strip end -> asphalt corner -> strip end, each 2 m
        # (one sidewalk width) long, i.e. exactly the missing square.
        assert _wedge_area(inner, outer) == pytest.approx(SIDEWALK_WIDTH ** 2)
        corner = inner[1]
        assert abs(abs(corner[0] - 10.0) - half) < 1e-9 and abs(abs(corner[1] + 5.0) - half) < 1e-9


def test_corner_sidewalks_bridge_a_t_junction_and_wrap_around_a_bend():
    outgoing = [((1.0, 0.0), 3.0), ((-1.0, 0.0), 3.0), ((0.0, 1.0), 3.0)]
    pieces = corner_sidewalk_paths((0.0, 0.0), outgoing, fan_radius=3.0)
    assert len(pieces) == 3
    straight = [(inner, outer) for inner, outer in pieces if len(inner) == 2]
    assert len(straight) == 1
    inner, outer = straight[0]
    # The top of the T: one slab straight across, 2 x trim = 10 m long.
    assert _wedge_area(inner, outer) == pytest.approx(2 * (3.0 + SIDEWALK_WIDTH) * SIDEWALK_WIDTH)
    assert all(y == pytest.approx(-3.0) for _x, y in inner)

    bend = corner_sidewalk_paths((0.0, 0.0), [((1.0, 0.0), 3.0), ((0.0, 1.0), 3.0)], fan_radius=3.0)
    assert len(bend) == 2
    outside = max(bend, key=lambda piece: len(piece[0]))
    # Outside of the bend: the inner edge hugs the fan radius all the way
    # round, so no grass wedge is left between asphalt and pavement.
    arc = outside[0][1:-1]
    assert all(math.hypot(x, y) == pytest.approx(3.0) for x, y in arc)
    assert all(math.hypot(x, y) == pytest.approx(3.0 + SIDEWALK_WIDTH) for x, y in outside[1][1:-1])


def test_road_corridors_cover_asphalt_and_both_sidewalks():
    graph = _cross_graph()
    preset = PRESETS["KLEINSTADT"]
    corridors = road_corridors(graph, preset)
    assert len(corridors) == len(graph.edges)
    widths = {round(c[4], 3) for c in corridors}
    assert widths == {round(preset.street_width_arterial / 2 + SIDEWALK_WIDTH, 3),
                      round(preset.street_width_local / 2 + SIDEWALK_WIDTH, 3)}
    discs = node_corridor_discs(graph, preset)
    assert len(discs) == 1    # only the degree-4 center; dead ends need no disc
    (cx, cy, radius), = discs
    assert (cx, cy) == (0.0, 0.0)
    # Reaches the outer corner of the crossing's sidewalk square.
    assert radius == pytest.approx((preset.street_width_arterial / 2 + SIDEWALK_WIDTH) * math.sqrt(2))

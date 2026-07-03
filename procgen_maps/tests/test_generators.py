"""Pure-Python tests for noise, math helpers, terrain, city and dungeon generators."""
import math

import numpy as np
import pytest

from procgen_maps.assets.library import ASSET_DEFS
from procgen_maps.config.presets import PRESETS
from procgen_maps.generators.city.buildings import plan_buildings
from procgen_maps.generators.city.layout import Block, generate_layout
from procgen_maps.generators.city.props import plan_props
from procgen_maps.generators.city.special_buildings import (
    SPECIAL_BUILDING_SPECS,
    plan_special_buildings,
)
from procgen_maps.generators.city.streets import build_street_graph
from procgen_maps.generators.city.zones import classify_zones
from procgen_maps.generators.dungeon import DungeonParams, generate_dungeon
from procgen_maps.generators.terrain import TerrainParams, generate_heightmap, sample_world_height
from procgen_maps.utils.math_helpers import clamp, lerp, remap, smoothstep
from procgen_maps.utils.noise import fbm, fbm_heightmap, value_noise_2d
from procgen_maps.utils.spatial import SpatialHashGrid

_KNOWN_ZONES = {"residential", "commercial", "industrial", "park"}
_DUNGEON_SEEDS = (0, 1, 42)


def test_spatial_hash_grid_detects_collision_with_large_registered_item():
    # Regression test: a query point can sit many grid cells away from a
    # large item's own center cell yet still be inside its radius (e.g.
    # registering a whole building footprint as a large bounding circle
    # alongside small props) - the search span has to grow with the
    # largest radius ever inserted, not just the query's own radius.
    grid = SpatialHashGrid(cell_size=5.0)
    grid.insert("building", 0.0, 0.0, radius=20.0)
    assert grid.has_collision(18.0, 0.0, radius=0.5)
    assert not grid.has_collision(100.0, 0.0, radius=0.5)


def test_spatial_hash_grid_no_collision_when_far_apart():
    grid = SpatialHashGrid(cell_size=5.0)
    grid.insert("a", 0.0, 0.0, radius=1.0)
    assert not grid.has_collision(10.0, 10.0, radius=1.0)
    assert grid.has_collision(1.5, 0.0, radius=1.0)


def test_fbm_same_seed_is_deterministic():
    coords = np.linspace(-10.0, 10.0, 40)
    xs, ys = np.meshgrid(coords, coords)
    a = fbm(xs, ys, seed=5)
    b = fbm(xs, ys, seed=5)
    assert np.array_equal(a, b)


def test_fbm_different_seed_differs():
    coords = np.linspace(-10.0, 10.0, 40)
    xs, ys = np.meshgrid(coords, coords)
    a = fbm(xs, ys, seed=5)
    b = fbm(xs, ys, seed=6)
    assert not np.array_equal(a, b)


def test_fbm_stays_in_unit_range():
    coords = np.linspace(-50.0, 50.0, 64)
    xs, ys = np.meshgrid(coords, coords)
    values = fbm(xs, ys, seed=3)
    assert np.all(values >= 0.0)
    assert np.all(values <= 1.0)


def test_fbm_heightmap_shape_and_range():
    heightmap = fbm_heightmap(48, 300.0, seed=1)
    assert heightmap.shape == (48, 48)
    assert np.all(heightmap >= 0.0)
    assert np.all(heightmap <= 1.0)


def test_fbm_heightmap_determinism_and_seed_sensitivity():
    a = fbm_heightmap(32, 200.0, seed=9)
    b = fbm_heightmap(32, 200.0, seed=9)
    c = fbm_heightmap(32, 200.0, seed=10)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_value_noise_2d_deterministic_and_bounded():
    a = value_noise_2d(3.7, -2.1, seed=4)
    b = value_noise_2d(3.7, -2.1, seed=4)
    c = value_noise_2d(3.7, -2.1, seed=5)
    assert a == b
    assert a != c
    assert 0.0 <= a <= 1.0


def test_lerp():
    assert lerp(0.0, 10.0, 0.5) == 5.0
    assert lerp(2.0, 4.0, 0.0) == 2.0
    assert lerp(2.0, 4.0, 1.0) == 4.0


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-5, 0, 10) == 0
    assert clamp(15, 0, 10) == 10


def test_remap():
    assert remap(5.0, 0.0, 10.0, 0.0, 100.0) == 50.0
    assert remap(0.0, 0.0, 10.0, -1.0, 1.0) == -1.0
    assert remap(3.0, 5.0, 5.0, 0.0, 1.0) == 0.0


def test_smoothstep():
    assert smoothstep(0.0, 1.0, 0.5) == 0.5
    assert smoothstep(0.0, 1.0, -1.0) == 0.0
    assert smoothstep(0.0, 1.0, 2.0) == 1.0
    assert smoothstep(5.0, 5.0, 3.0) == 0.0
    assert smoothstep(5.0, 5.0, 7.0) == 1.0


def test_heightmap_matches_sample_world_height():
    params = TerrainParams(resolution=33, world_size=200.0, seed=5)
    heights = generate_heightmap(params)
    half = params.world_size / 2.0
    coords = np.linspace(-half, half, params.resolution)

    for row, col in ((0, 0), (16, 16), (32, 0), (10, 27)):
        x, y = coords[col], coords[row]
        expected = heights[row][col]
        actual = sample_world_height(x, y, params)
        assert actual == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("preset_key", sorted(PRESETS))
def test_preset_pipeline(preset_key):
    preset = PRESETS[preset_key]

    layout = generate_layout(preset)
    assert len(layout.blocks) >= 1

    # Regression: raster-mode blocks (Metropole, Industrial) are built from
    # the axis-aligned bounding box of an irregular Voronoi-like cell, which
    # can still overlap a neighbor's bbox even after the fill-factor shrink -
    # generate_layout must resolve these before returning.
    for i, block_a in enumerate(layout.blocks):
        for block_b in layout.blocks[i + 1:]:
            overlap_x = (block_a.half_size[0] + block_b.half_size[0]) - abs(block_a.center[0] - block_b.center[0])
            overlap_y = (block_a.half_size[1] + block_b.half_size[1]) - abs(block_a.center[1] - block_b.center[1])
            assert overlap_x <= 0 or overlap_y <= 0, f"blocks {block_a.id} and {block_b.id} overlap"

    graph = build_street_graph(layout.streets)
    assert len(graph.nodes) > 0
    assert len(graph.edges) > 0
    for a, b, street_class in graph.edges:
        assert 0 <= a < len(graph.nodes)
        assert 0 <= b < len(graph.nodes)
        assert street_class in ("arterial", "local")

    zone_by_block = classify_zones(layout.blocks, preset)
    assert set(zone_by_block) == {block.id for block in layout.blocks}
    assert all(zone in _KNOWN_ZONES for zone in zone_by_block.values())

    special_plans, reserved_block_ids = plan_special_buildings(layout.blocks, zone_by_block, preset)
    for plan in special_plans:
        assert zone_by_block[plan.block_id] != "park"
    assert reserved_block_ids == {plan.block_id for plan in special_plans}

    regular_blocks = [b for b in layout.blocks if b.id not in reserved_block_ids]
    plans = plan_buildings(regular_blocks, zone_by_block, preset)
    for plan in plans:
        assert zone_by_block[plan.block_id] != "park"
        assert plan.block_id not in reserved_block_ids

    plans = plans + special_plans
    placements = plan_props(graph, layout.blocks, zone_by_block, preset, building_plans=plans)
    for placement in placements:
        assert placement.asset_id in ASSET_DEFS

    for placement in placements:
        px, py, _ = placement.location
        for plan in plans:
            cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
            cy = sum(p[1] for p in plan.footprint) / len(plan.footprint)
            building_radius = max(math.hypot(p[0] - cx, p[1] - cy) for p in plan.footprint)
            assert math.hypot(px - cx, py - cy) >= building_radius, (
                f"{placement.asset_id} at ({px}, {py}) overlaps a building's bounding circle")


@pytest.mark.parametrize("preset_key", sorted(PRESETS))
def test_plan_special_buildings_reserved_blocks_match_plans(preset_key):
    preset = PRESETS[preset_key]
    layout = generate_layout(preset)
    zone_by_block = classify_zones(layout.blocks, preset)

    plans, reserved_block_ids = plan_special_buildings(layout.blocks, zone_by_block, preset)

    assert reserved_block_ids == {plan.block_id for plan in plans}
    # Every spec claims at most one block per city (count_formula 'one'), and
    # even 'scaled' supermarket picks distinct, never-repeated blocks, so no
    # block should be reserved by more than one plan.
    assert len(reserved_block_ids) == len(plans)


@pytest.mark.parametrize("preset_key", sorted(PRESETS))
def test_plan_special_buildings_never_on_park_blocks(preset_key):
    preset = PRESETS[preset_key]
    layout = generate_layout(preset)
    zone_by_block = classify_zones(layout.blocks, preset)

    plans, _ = plan_special_buildings(layout.blocks, zone_by_block, preset)

    for plan in plans:
        assert zone_by_block[plan.block_id] != "park"


@pytest.mark.parametrize("preset_key", sorted(PRESETS))
def test_plan_special_buildings_does_not_reuse_regular_building_blocks(preset_key):
    # Mirrors generators.city.__init__.generate_city's pipeline order: regular
    # buildings must never be planned onto a block a special building reserved.
    preset = PRESETS[preset_key]
    layout = generate_layout(preset)
    zone_by_block = classify_zones(layout.blocks, preset)

    special_plans, reserved_block_ids = plan_special_buildings(layout.blocks, zone_by_block, preset)
    regular_blocks = [b for b in layout.blocks if b.id not in reserved_block_ids]
    regular_plans = plan_buildings(regular_blocks, zone_by_block, preset)

    regular_block_ids = {plan.block_id for plan in regular_plans}
    assert regular_block_ids.isdisjoint(reserved_block_ids)
    # All preset city sizes (see config/presets.py) comfortably exceed every
    # spec's min_blocks_required, so every special type should actually place
    # (supermarket may place more than once - see the scaling test below).
    assert {plan.facade.key for plan in special_plans} == {spec.facade.key for spec in SPECIAL_BUILDING_SPECS}


def test_plan_special_buildings_supermarket_count_scales_with_city_size():
    for preset_key in ("METROPOLE", "INDUSTRIAL"):
        preset = PRESETS[preset_key]
        layout = generate_layout(preset)
        zone_by_block = classify_zones(layout.blocks, preset)

        plans, _ = plan_special_buildings(layout.blocks, zone_by_block, preset)
        supermarket_count = sum(1 for plan in plans if plan.facade.key == "supermarket")

        assert supermarket_count == max(1, len(layout.blocks) // 45)


def test_plan_special_buildings_skips_types_below_min_blocks_required():
    preset = PRESETS["DORF"]
    tiny_blocks = [Block(id=i, center=(float(i) * 5.0, 0.0), half_size=(2.0, 2.0)) for i in range(3)]
    zone_by_block = {block.id: "commercial" for block in tiny_blocks}

    # Every spec's min_blocks_required (smallest is 6) exceeds this 3-block
    # city, so nothing should be placed at all.
    plans, reserved_block_ids = plan_special_buildings(tiny_blocks, zone_by_block, preset)

    assert plans == []
    assert reserved_block_ids == set()


def test_plan_special_buildings_is_deterministic_for_a_given_seed():
    preset = PRESETS["KLEINSTADT"]
    layout = generate_layout(preset)
    zone_by_block = classify_zones(layout.blocks, preset)

    plans_a, reserved_a = plan_special_buildings(layout.blocks, zone_by_block, preset, seed=7)
    plans_b, reserved_b = plan_special_buildings(layout.blocks, zone_by_block, preset, seed=7)

    assert reserved_a == reserved_b
    assert [p.block_id for p in plans_a] == [p.block_id for p in plans_b]
    assert [p.tint for p in plans_a] == [p.tint for p in plans_b]


def test_special_building_specs_have_unique_material_indices():
    indices = [spec.facade.material_index for spec in SPECIAL_BUILDING_SPECS]
    assert len(indices) == len(set(indices))
    assert all(index >= 12 for index in indices)


def test_generate_dungeon_produces_rooms():
    for seed in _DUNGEON_SEEDS:
        layout = generate_dungeon(DungeonParams(seed=seed))
        assert len(layout.rooms) >= 1


def test_generate_dungeon_corridor_endpoints_are_finite():
    for seed in _DUNGEON_SEEDS:
        layout = generate_dungeon(DungeonParams(seed=seed))
        for corridor in layout.corridors:
            assert math.isfinite(corridor.start[0])
            assert math.isfinite(corridor.start[1])
            assert math.isfinite(corridor.end[0])
            assert math.isfinite(corridor.end[1])


def test_generate_dungeon_is_deterministic():
    layout_a = generate_dungeon(DungeonParams(seed=3))
    layout_b = generate_dungeon(DungeonParams(seed=3))
    assert len(layout_a.rooms) == len(layout_b.rooms)

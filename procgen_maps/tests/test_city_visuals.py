"""v0.13 city visuals: night-window planning, roof shapes, parks/plazas,
the river (layout, bridges, validator rules) and the road-bed profile -
all pure Python, no Blender."""
import dataclasses
import random

import numpy as np
import pytest

from procgen_maps.config.presets import PRESETS
from procgen_maps.generators.city import plan_city
from procgen_maps.generators.city.buildings import (FACADE_TYPES, BuildingPlan, pick_roof_shape,
                                                    plan_floor_lighting, window_lighting)
from procgen_maps.generators.city.layout import RiverSpec, generate_layout, plan_river
from procgen_maps.generators.city.parks import ParkSpec, bench_spots, plan_parks
from procgen_maps.generators.city.props import PropPlacement
from procgen_maps.generators.city.streets import build_street_graph
from procgen_maps.generators.city.water import bridge_edges
from procgen_maps.generators.terrain import TerrainParams, corridor_lowering
from procgen_maps.validation import street_components, validate_city_plan


# --- night windows -----------------------------------------------------------

def test_dark_floor_has_no_lit_window():
    assert window_lighting(random.Random(1), 9, "residential", "dark") == [(0.0, 0.0)] * 9


def test_office_band_floor_is_lit_wall_to_wall_and_cool():
    panes = window_lighting(random.Random(1), 7, "office", "band")
    assert all(lit == 1.0 for lit, _warm in panes)
    assert all(warm < 0.5 for _lit, warm in panes)


def test_residential_floor_lights_whole_apartments_not_single_panes():
    """Lit/dark changes only at apartment boundaries (runs of 2-3 bays),
    so a residential floor is never a one-pane checkerboard."""
    panes = window_lighting(random.Random(7), 40, "residential", "mixed")
    assert len(panes) == 40
    runs = []
    for pane in panes:
        if runs and runs[-1][0] == pane:
            runs[-1][1] += 1
        else:
            runs.append([pane, 1])
    # Every run except possibly the clipped last one spans at least 2 bays.
    assert all(length >= 2 for _pane, length in runs[:-1])
    assert any(lit > 0 for lit, _warm in panes) and any(lit == 0 for lit, _warm in panes)
    warm_lit = [warm for lit, warm in panes if lit > 0]
    assert warm_lit.count(1.0) >= len(warm_lit) * 0.5   # mostly warm


def test_floor_lighting_states_are_coherent_and_deterministic():
    states = [plan_floor_lighting(random.Random(3), kind) for kind in ("residential", "office", "industrial")]
    assert all(state in ("dark", "band", "mixed") for state in states)
    assert [plan_floor_lighting(random.Random(11), "office") for _ in range(5)] == \
        [plan_floor_lighting(random.Random(11), "office") for _ in range(5)]
    industrial = {plan_floor_lighting(random.Random(s), "industrial") for s in range(40)}
    assert industrial <= {"dark", "band"}


def test_every_facade_has_a_lighting_kind():
    assert {f.lighting for f in FACADE_TYPES} == {"residential", "office", "industrial"}
    assert all(f.lighting == "residential" for f in FACADE_TYPES if f.balconies)


# --- roofs -------------------------------------------------------------------

def test_roof_shapes_by_facade():
    by_key = {f.key: f for f in FACADE_TYPES}
    assert pick_roof_shape(random.Random(1), by_key["glass_tower"]) == "flat"
    shapes = {pick_roof_shape(random.Random(s), by_key["factory_hall"]) for s in range(60)}
    assert shapes == {"sawtooth", "gable"}
    shapes = {pick_roof_shape(random.Random(s), by_key["cottage"]) for s in range(60)}
    assert shapes == {"hip", "gable"}
    assert pick_roof_shape(random.Random(1), by_key["mixed_use"]) == "gable"


# --- parks and plazas --------------------------------------------------------

def _park_layout(preset_key="KLEINSTADT", seed=4):
    preset = PRESETS[preset_key]
    plan = plan_city(preset, TerrainParams(seed=seed, world_size=preset.radius * 4.2), seed=seed)
    return preset, plan


def test_every_park_block_gets_a_spec_and_paths_stay_inside_the_block():
    preset, plan = _park_layout()
    park_blocks = {b.id for b in plan.layout.blocks if plan.zone_by_block[b.id] == "park"}
    specs = {spec.block_id: spec for spec in plan.park_specs}
    assert set(specs) <= park_blocks and len(specs) >= len(park_blocks) * 0.8
    for spec in specs.values():
        x0, y0, x1, y1 = spec.rect
        for (px0, py0, px1, py1) in spec.paths:
            assert x0 - 1e-6 <= px0 < px1 <= x1 + 1e-6 and y0 - 1e-6 <= py0 < py1 <= y1 + 1e-6
    assert {spec.kind for spec in specs.values()} == {"park", "plaza"}


def test_plan_parks_is_deterministic():
    preset, plan = _park_layout()
    a = plan_parks(plan.layout.blocks, plan.zone_by_block, preset, seed=4)
    b = plan_parks(plan.layout.blocks, plan.zone_by_block, preset, seed=4)
    assert repr(a) == repr(b)


def test_benches_sit_beside_paths_and_trees_off_them():
    spec = ParkSpec(0, "park", (0.0, 0.0), (12.0, 12.0), [(-12.0, -1.2, 12.0, 1.2), (-1.2, -12.0, 1.2, 12.0)])
    for x, y, _rot in bench_spots(spec, random.Random(2), 20):
        assert not spec.on_paving(x, y), (x, y)
        assert -12.0 <= x <= 12.0 and -12.0 <= y <= 12.0
    assert spec.on_paving(0.0, 5.0) and not spec.on_paving(5.0, 5.0)
    preset, plan = _park_layout()
    assert validate_city_plan(plan, preset).count("tree_on_path") == 0


def test_validator_flags_a_tree_on_a_park_path():
    preset, plan = _park_layout()
    spec = next(spec for spec in plan.park_specs if spec.kind == "park")
    plan.prop_placements.append(PropPlacement("tree_01", (spec.center[0], spec.center[1] + 3.0, 0.0)))
    assert validate_city_plan(plan, preset).count("tree_on_path") == 1


# --- river ---------------------------------------------------------------------

def test_river_keeps_clear_of_the_bounding_streets():
    preset = PRESETS["KLEINSTADT"]
    xs = [-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0]
    river, column = plan_river(np.random.default_rng(1), xs, -100.0, 100.0, preset)
    assert river is not None and 0 <= column < len(xs) - 1
    clearance = preset.street_width_arterial / 2.0 + 2.0 + 1.0
    for x, _y in river.points:
        assert xs[column] + clearance < x - river.half_width - river.bank_margin
        assert x + river.half_width + river.bank_margin < xs[column + 1] - clearance
    assert river.x_range == (xs[column], xs[column + 1])
    assert plan_river(np.random.default_rng(1), xs, -100.0, 100.0, dataclasses.replace(preset, water="none")) == (None, -1)
    assert plan_river(np.random.default_rng(1), [-10.0, 0.0, 10.0, 20.0], -10.0, 10.0, preset) == (None, -1)


@pytest.mark.parametrize("preset_key", sorted(PRESETS))
def test_river_city_has_bridges_no_lots_in_water_and_stays_connected(preset_key):
    preset = PRESETS[preset_key]
    for seed in (0, 7, 42):
        layout = generate_layout(preset, seed=seed)
        assert layout.river is not None
        x_lo, x_hi = layout.river.x_range
        for block in layout.blocks:
            assert block.center[0] + block.half_size[0] <= x_lo + 1e-6 or block.center[0] - block.half_size[0] >= x_hi - 1e-6
        graph = build_street_graph(layout.streets)
        assert street_components(graph) == [len(graph.nodes)]
        assert len(bridge_edges(graph, layout.river)) >= 2


def test_river_spec_distance_and_corridors():
    river = RiverSpec([(0.0, -10.0), (0.0, 10.0)], half_width=3.0, bank_margin=2.0, x_range=(-15.0, 15.0))
    assert river.distance(2.0, 0.0) == pytest.approx(2.0)
    assert river.contains(2.9, 5.0) and not river.contains(3.1, 5.0) and river.contains(4.5, 5.0, 2.0)
    assert river.corridors() == [(0.0, -10.0, 0.0, 10.0, 3.0)]


def test_validator_flags_building_tree_and_sign_in_water():
    preset = PRESETS["DORF"]
    plan = plan_city(preset, TerrainParams(seed=3, world_size=preset.radius * 4.2), seed=3)
    assert validate_city_plan(plan, preset).ok
    river = plan.river
    x, y = river.points[len(river.points) // 2]
    wet = dataclasses.replace(plan.building_plans[0],
                              footprint=[(x - 2, y - 2), (x + 2, y - 2), (x + 2, y + 2), (x - 2, y + 2)])
    plan.building_plans.append(wet)
    plan.prop_placements.append(PropPlacement("tree_02", (x, y, 0.0)))
    plan.prop_placements.append(PropPlacement("street_lamp", (x, y, 0.0)))   # allowed: on a bridge deck
    sign = dataclasses.replace(plan.sign_placements[0], location=(x, y, 0.0))
    plan.sign_placements.append(sign)
    report = validate_city_plan(plan, preset)
    assert report.count("building_in_water") == 1
    assert report.count("prop_in_water") == 1
    assert report.count("sign_in_water") == 1


def test_no_props_or_signs_on_river_banks_in_any_preset():
    for key, preset in PRESETS.items():
        plan = plan_city(preset, TerrainParams(seed=5, world_size=preset.radius * 4.2), seed=5)
        report = validate_city_plan(plan, preset)
        assert report.ok, (key, report.format())
        assert report.checked.get("river_corridors", 0) > 0


def test_water_off_preset_has_no_river_and_full_grid():
    preset = dataclasses.replace(PRESETS["KLEINSTADT"], water="none")
    layout = generate_layout(preset, seed=4)
    assert layout.river is None
    n = max(2, int((preset.radius * 2) / preset.block_size))
    assert len(layout.blocks) == n * n


# --- road bed profile (the v0.12 "lip") ----------------------------------------

def test_corridor_lowering_rises_under_the_sidewalk_and_fades_outside():
    dist = np.array([0.0, 3.0, 4.0, 5.0, 5.5, 6.5, 8.0])
    amount = corridor_lowering(dist, half_width=5.0, depth=0.5, shallow_depth=0.2, shoulder=2.0, margin=1.5)
    assert amount[0] == pytest.approx(0.5) and amount[1] == pytest.approx(0.5)       # asphalt
    assert amount[2] == pytest.approx(0.35)                                            # mid-sidewalk
    assert amount[3] == pytest.approx(0.2)                                             # sidewalk outer edge
    assert 0.0 < amount[4] < 0.2 and amount[5] == pytest.approx(0.0) and amount[6] == 0.0
    assert np.all(np.diff(amount) <= 1e-9)                                             # monotonic outward


def test_corridor_lowering_without_shoulder_is_the_old_profile():
    dist = np.array([0.0, 2.0, 3.0, 4.0])
    amount = corridor_lowering(dist, half_width=3.0, depth=2.6, shallow_depth=2.6, shoulder=0.0, margin=2.0)
    assert list(amount) == pytest.approx([2.6, 2.6, 2.6, 1.3])

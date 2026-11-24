"""Pure-Python tests for generators/landscape.py (fields, hedgerows, forest
clumps) and the terrain's horizon skirt geometry - the v0.15 countryside."""
import math
import random

import numpy as np
import pytest

from procgen_maps.generators import landscape
from procgen_maps.generators.terrain import TerrainParams, generate_heightmap, horizon_geometry, terrain_edge_loop


def test_landscape_masks_are_in_range_and_deterministic():
    xs = np.linspace(-500.0, 500.0, 101)
    xs, ys = np.meshgrid(xs, xs)
    first = landscape.landscape_masks(xs, ys, 220.0, 4)
    second = landscape.landscape_masks(xs, ys, 220.0, 4)
    for mask_a, mask_b in zip(first, second):
        assert mask_a.shape == xs.shape
        assert float(mask_a.min()) >= 0.0 and float(mask_a.max()) <= 1.0
        assert np.array_equal(mask_a, mask_b)
    other = landscape.landscape_masks(xs, ys, 220.0, 5)
    assert not np.array_equal(first[1], other[1])   # the field tones change with the seed


def test_fields_hedges_and_forest_stay_outside_the_city():
    xs = np.linspace(-500.0, 500.0, 201)
    xs, ys = np.meshgrid(xs, xs)
    field, tone, hedge, forest = landscape.landscape_masks(xs, ys, 200.0, 7)
    inside = np.hypot(xs, ys) < 200.0 * landscape.FIELD_START
    assert float(field[inside].max()) == 0.0
    assert float(hedge[inside].max()) == 0.0
    assert float(forest[np.hypot(xs, ys) < 200.0 * landscape.FOREST_START].max()) == 0.0
    outside = np.hypot(xs, ys) > 200.0 * landscape.FIELD_FULL
    assert float(field[outside].min()) == 1.0
    # A patchwork, not one field: many distinct tones, and hedges on some borders.
    assert len(np.unique(np.round(tone[outside], 3))) > 20
    assert 0.01 < float(hedge[outside].mean()) < 0.3
    # Woods cover part of the ring, not all of it and not none of it.
    ring = (np.hypot(xs, ys) > 200.0 * landscape.FOREST_FULL) & (np.hypot(xs, ys) < 200.0 * landscape.FOREST_THIN_FROM)
    assert 0.2 < float((forest[ring] > 0.5).mean()) < 0.8
    assert float(forest[np.hypot(xs, ys) > 200.0 * landscape.FOREST_END].max()) == 0.0


def test_forest_density_scalar_matches_array_path():
    seed = 3
    points = [(250.0, 10.0), (-300.0, 120.0), (0.0, 330.0), (390.0, -390.0)]
    scalars = [landscape.forest_density(x, y, 220.0, seed) for x, y in points]
    arrays = landscape.forest_density(np.array([p[0] for p in points]), np.array([p[1] for p in points]), 220.0, seed)
    assert scalars == pytest.approx(list(arrays))


def test_hedgerow_points_lie_on_borders_outside_city_and_woods():
    rng = random.Random(9)
    points = landscape.hedgerow_points(220.0, 500.0, 9, rng)
    assert 200 < len(points) < 6000
    xs = np.array([p[0] for p in points])
    ys = np.array([p[1] for p in points])
    assert float(np.hypot(xs, ys).min()) >= 220.0 * landscape.FIELD_FULL - 1.0
    assert float(np.abs(xs).max()) <= 500.0 - 11.0 and float(np.abs(ys).max()) <= 500.0 - 11.0
    _f, _t, hedge, forest = landscape.landscape_masks(xs, ys, 220.0, 9)
    assert float((hedge > 0.3).mean()) > 0.85   # on the hedge band (the +-0.6 m jitter moves a few off it)
    assert float((forest > 0.5).mean()) < 0.05  # not inside the woods
    assert all(0.8 <= s <= 1.25 for _x, _y, s, _r in points)
    assert landscape.hedgerow_points(220.0, 500.0, 9, random.Random(9)) == points


def test_hedgerow_points_empty_when_map_is_not_larger_than_city():
    assert landscape.hedgerow_points(220.0, 230.0, 1, random.Random(1)) == []


def test_horizon_skirt_rings_the_terrain_edge():
    params = TerrainParams(resolution=9, world_size=80.0, seed=2)
    loop = terrain_edge_loop(params, generate_heightmap(params))
    assert len(loop) == 4 * (params.resolution - 1)
    assert loop[0][:2] == (-40.0, -40.0)
    assert all(max(abs(x), abs(y)) == pytest.approx(40.0) for x, y, _z in loop)
    verts, faces = horizon_geometry(loop, skirt_width=100.0, drop=4.0, reach=5000.0)
    n = len(loop)
    assert len(verts) == 3 * n and len(faces) == 2 * n
    floor_z = min(p[2] for p in loop) - 4.0
    assert all(v[2] == pytest.approx(floor_z) for v in verts[n:])
    # The skirt ring sits outside the terrain, the far ring at the reach.
    assert all(math.hypot(x, y) > 40.0 for x, y, _z in verts[n:2 * n])
    assert all(math.hypot(x, y) == pytest.approx(5000.0) for x, y, _z in verts[2 * n:])
    assert all(len(set(f)) == 4 for f in faces)
    assert horizon_geometry([]) == ([], [])

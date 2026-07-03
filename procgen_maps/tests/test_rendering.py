"""Pure-Python tests for the showcase-render auto-framing math."""
import math

import pytest

from procgen_maps.rendering.framing import compute_framing


def test_compute_framing_centers_on_bounds():
    plan = compute_framing((-10.0, -10.0, 0.0), (10.0, 10.0, 5.0), angle="overview")
    assert plan.center == pytest.approx((0.0, 0.0, 2.5))
    assert plan.look_at == plan.center


def test_compute_framing_camera_is_offset_diagonally_from_center():
    bounds_min, bounds_max = (-50.0, -50.0, 0.0), (50.0, 50.0, 20.0)
    plan = compute_framing(bounds_min, bounds_max, angle="overview")
    # camera should be pulled back and up from the center on all 3 axes,
    # not sitting at (or behind) it
    assert plan.camera_location[0] < plan.center[0]
    assert plan.camera_location[1] < plan.center[1]
    assert plan.camera_location[2] > plan.center[2]


def test_compute_framing_close_is_nearer_than_overview():
    bounds_min, bounds_max = (-50.0, -50.0, 0.0), (50.0, 50.0, 20.0)
    center = ((bounds_min[0] + bounds_max[0]) / 2.0, (bounds_min[1] + bounds_max[1]) / 2.0)

    overview = compute_framing(bounds_min, bounds_max, angle="overview")
    close = compute_framing(bounds_min, bounds_max, angle="close")

    dist_overview = math.hypot(overview.camera_location[0] - center[0], overview.camera_location[1] - center[1])
    dist_close = math.hypot(close.camera_location[0] - center[0], close.camera_location[1] - center[1])
    assert dist_close < dist_overview


def test_compute_framing_unknown_angle_raises():
    with pytest.raises(ValueError):
        compute_framing((0, 0, 0), (1, 1, 1), angle="bogus")


def test_compute_framing_clip_end_covers_camera_distance():
    bounds_min, bounds_max = (-200.0, -200.0, 0.0), (200.0, 200.0, 50.0)
    plan = compute_framing(bounds_min, bounds_max, angle="overview")
    camera_distance = math.dist(plan.camera_location, plan.look_at)
    assert plan.clip_end > camera_distance


def test_compute_framing_tiny_bounds_use_minimum_radius():
    # a near-empty scene (e.g. terrain not generated yet) shouldn't collapse
    # the camera onto the origin
    plan = compute_framing((-0.1, -0.1, 0.0), (0.1, 0.1, 0.1), angle="overview")
    assert math.dist(plan.camera_location, plan.look_at) > 1.0

"""Pure-Python camera auto-framing: given a world-space bounding box, compute
where a camera should sit and look to frame it well. No bpy here (safe to
pytest outside Blender) - turning this plan into an actual bpy.types.Camera
object (which needs mathutils for the look-at rotation) is the thin build
step in rendering/showcase.py.

Three angle presets, matched to the ad-hoc framing this project's renders
have used throughout development:
- "overview": a wide, elevated three-quarter view of the whole generated area.
- "close": zoomed in on one corner/building cluster.
- "low": a low, more dramatic street-level-ish angle.
"""
from dataclasses import dataclass
from typing import Tuple

_ANGLE_PRESETS = {
    "overview": {"distance_factor": 0.42, "height_factor": 0.30},
    "close": {"distance_factor": 0.18, "height_factor": 0.12},
    "low": {"distance_factor": 0.55, "height_factor": 0.08},
}

MIN_RADIUS = 20.0          # meters; keeps framing sane for a near-empty scene
HEIGHT_CLEARANCE = 6.0      # meters; extra camera height above the raw height_factor offset


@dataclass(frozen=True)
class FramingPlan:
    center: Tuple[float, float, float]
    camera_location: Tuple[float, float, float]
    look_at: Tuple[float, float, float]
    focal_length: float
    clip_end: float


def compute_framing(bounds_min: Tuple[float, float, float], bounds_max: Tuple[float, float, float],
                     angle: str = "overview", lens: float = 32.0) -> FramingPlan:
    """Compute a camera placement that frames the given world-space AABB."""
    if angle not in _ANGLE_PRESETS:
        raise ValueError(f"Unknown angle preset '{angle}'. Available: {', '.join(_ANGLE_PRESETS)}")
    preset = _ANGLE_PRESETS[angle]

    cx = (bounds_min[0] + bounds_max[0]) / 2.0
    cy = (bounds_min[1] + bounds_max[1]) / 2.0
    cz = (bounds_min[2] + bounds_max[2]) / 2.0
    size_x = bounds_max[0] - bounds_min[0]
    size_y = bounds_max[1] - bounds_min[1]
    radius = max(size_x, size_y, MIN_RADIUS)

    distance = radius * preset["distance_factor"]
    height = radius * preset["height_factor"]

    camera_location = (cx - distance, cy - distance * 0.7, cz + height + HEIGHT_CLEARANCE)
    clip_end = max(radius * 20.0, 5000.0)

    return FramingPlan(
        center=(cx, cy, cz),
        camera_location=camera_location,
        look_at=(cx, cy, cz),
        focal_length=lens,
        clip_end=clip_end,
    )
